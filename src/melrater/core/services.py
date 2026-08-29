"""Write-side operations (HackSoft-style services)."""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from melrater.core import lake, melodic, metrics, montage, transfer
from melrater.core import storage as montage_store
from melrater.core.models import Classification, Component, Reviewer, Run
from melrater.core.schemas import FixReviewerPayload, RunPayload

logger = logging.getLogger("melrater.ingest")


class RunAlreadyIngested(Exception):
    pass


class PushRejected(Exception):
    """A pushed run conflicts with what this database already holds."""


class PushTooLarge(Exception):
    """A pushed run is over one of the server's ingest ceilings."""


def reviewer_for_user(user: AbstractBaseUser) -> Reviewer:
    reviewer, _ = Reviewer.objects.get_or_create(
        user=user,
        defaults={"kind": Reviewer.Kind.HUMAN, "name": user.get_username()},
    )
    return reviewer


def rate_component(
    *, user: AbstractBaseUser, component: Component, label: str
) -> Classification:
    if label not in Classification.Label.values:
        raise ValueError(f"invalid label: {label!r}")
    reviewer = reviewer_for_user(user)
    classification, _ = Classification.objects.update_or_create(
        component=component, reviewer=reviewer, defaults={"label": label}
    )
    return classification


def _fix_reviewer(result: melodic.FixResult) -> Reviewer:
    reviewer, _ = Reviewer.objects.get_or_create(
        name=result.reviewer_name,
        defaults={
            "kind": Reviewer.Kind.FIX,
            "fix_model": result.model,
            "fix_threshold": result.threshold,
        },
    )
    return reviewer


def montage_format() -> str:
    return "avif" if montage.AVIF_OK else "png"


def ingest_run(*, source: melodic.MelodicSource, image_workers: int = 0) -> Run:
    """Ingest one loaded MELODIC+pyFIX run (see lake.discover_runs).

    Creates the Run, its Components with chart payloads, one FIX Reviewer +
    Classifications per fix4melview file, and stores the slice montages under
    the run's uuid.

    Montages are rendered *and stored* before the transaction opens. The
    database runs with transaction_mode=IMMEDIATE, so an atomic block holds
    SQLite's single write lock for its whole duration — and a 96-component
    render takes seconds, which is long enough to push every concurrent rating
    POST past the 5 s connection timeout and into an OperationalError. The
    montages are keyed by a uuid minted here, so nothing can observe them until
    the row that names them is committed; a failure afterwards deletes them.
    """
    path = source.root.resolve()
    if Run.objects.filter(path=str(path)).exists():
        raise RunAlreadyIngested(str(path))
    table = metrics.build_metric_table(source.feature_names, source.features)

    # the (alphabetically) first FIX result defines the Signal ticks shown on
    # the metric glyphs; all FIX results are stored as reviewers below
    signal_rows: list[int] = []
    if source.fix_results:
        signal_rows = [
            i
            for i, verdict in enumerate(source.fix_results[0].verdicts)
            if verdict.label == "Signal"
        ]

    run_uuid = uuid4()
    with _staged_render(
        ic_path=source.ic_path,
        mean_path=source.mean_path,
        mask_path=source.mask_path,
        n_components=source.n_components,
        workers=image_workers,
    ) as staged:
        digest = montage.digest_directory(staged)
        montage_store.store_directory(run_uuid, digest, staged)

    try:
        with transaction.atomic():
            run = Run.objects.create(
                uuid=run_uuid,
                path=str(path),
                label=source.label,
                sub=source.sub,
                ses=source.ses,
                task=source.task,
                run=source.run,
                tr=source.tr,
                n_timepoints=source.n_timepoints,
                fd=[float(v) for v in source.fd],
                frequencies=[float(v) for v in source.frequencies],
                metric_stats=metrics.compute_metric_stats(
                    table, signal_rows
                ).model_dump(),
                montage_format=montage_format(),
                montage_digest=digest,
            )
            components = Component.objects.bulk_create(
                Component(
                    run=run,
                    index=i + 1,
                    explained_var=float(source.icstats[i, 0]),
                    total_var=float(source.icstats[i, 1]),
                    timecourse=[float(v) for v in source.mix[:, i]],
                    spectrum=[float(v) for v in source.ftmix[:, i]],
                    metrics={
                        name: value.model_dump()
                        for name, value in metrics.compute_component_metrics(
                            table, i
                        ).items()
                    },
                )
                for i in range(source.n_components)
            )
            for result in source.fix_results:
                reviewer = _fix_reviewer(result)
                Classification.objects.bulk_create(
                    Classification(
                        component=component,
                        reviewer=reviewer,
                        label=verdict.label,
                        probability=verdict.p_signal,
                    )
                    for component, verdict in zip(components, result.verdicts)
                )
    except Exception:
        # the rows are gone, so the montages are unreachable; drop them rather
        # than leave 20 MB of orphans under a uuid nothing will ever name again
        montage_store.delete_run(run_uuid)
        raise
    return run


def rerender_montages(*, run: Run, image_workers: int = 0) -> None:
    """Re-render an ingested run's montages in place (after a display change).

    Reads the run directory's images again (named by the feat layout) but
    leaves the run's rows — and every reviewer's classifications — untouched.

    The new set is written to its own digest directory *beside* the old one,
    the row is then pointed at it, and only then is everything else dropped.
    So a failed render leaves the page serving the still-present old files
    rather than 404s, and there is no moment at which an already-issued URL
    answers with different bytes — which is what lets montages be served with
    an immutable cache header.
    """
    ic_path, mean_path, mask_path = lake.run_montage_paths(Path(run.path))
    with _staged_render(
        ic_path=ic_path,
        mean_path=mean_path,
        mask_path=mask_path,
        n_components=run.components.count(),
        workers=image_workers,
    ) as staged:
        digest = montage.digest_directory(staged)
        montage_store.store_directory(run.uuid, digest, staged)

    run.montage_format = montage_format()
    run.montage_digest = digest
    run.save(update_fields=["montage_format", "montage_digest"])
    montage_store.retain_digest(run.uuid, digest)


def delete_run(*, run: Run) -> None:
    """Remove a run, its components, its classifications and its montages."""
    run_uuid: UUID = run.uuid
    run.delete()
    montage_store.delete_run(run_uuid)


@contextmanager
def _staged_render(
    *,
    ic_path: Path,
    mean_path: Path,
    mask_path: Path,
    n_components: int,
    workers: int,
) -> Iterator[Path]:
    """Render one run's montages into a temporary directory.

    A plain directory rather than the storage backend, because the render
    parallelizes over spawned processes and montage.py is deliberately
    Django-free; storage.store_directory ingests the result.
    """
    mean_img = melodic.nib.load(mean_path)
    mask_img = melodic.nib.load(mask_path)
    assert isinstance(mean_img, melodic.nib.nifti1.Nifti1Image)
    assert isinstance(mask_img, melodic.nib.nifti1.Nifti1Image)
    bg = montage.canonical_vol(mean_img)
    mask = montage.canonical_vol(mask_img)
    window = montage.robust_window(bg, mask)
    picks_by_axis = {
        name: montage.axis_picks(mask, axis) for name, axis in montage.AXES.items()
    }
    staged = Path(tempfile.mkdtemp(prefix="melrater-montage-"))
    try:
        montage.render_run_montages(
            ic_path,
            list(range(1, n_components + 1)),
            bg,
            picks_by_axis,
            window,
            staged,
            workers=workers,
        )
        yield staged
    finally:
        shutil.rmtree(staged, ignore_errors=True)


# --- the ingest API's write side -----------------------------------------


def store_pushed_montages(
    *, run_uuid: UUID, payload: RunPayload, tar: IO[bytes]
) -> int:
    """Validate an uploaded montage tar and store it. Writes no database rows.

    Deliberately runs *before* the rows, which is the same ordering — and the
    same reason — as `ingest_run`: files under a uuid no row names are
    unreachable, so a push that dies here leaves orphans (see
    `prune_orphan_montages`) rather than a run whose page is a screen of
    broken images.

    Every name is rebuilt by `transfer.read_montage_tar`, so nothing the
    sender chose reaches a path. The set is then checked twice over: it must
    hold exactly three montages per component, and its digest — recomputed
    from the bytes that actually arrived — must be the one the payload
    declared. A truncated upload therefore cannot be committed as a whole run.
    """
    digest = payload.montage_digest
    n_components = len(payload.components)
    prefix = montage_store.run_prefix(run_uuid, digest)
    # a re-push of bytes already here must not be able to delete them on the
    # way out; only a set this call created is ours to clean up
    preexisting = bool(montage_store.names_for_run(run_uuid, digest))
    hashed: list[tuple[str, bytes]] = []
    try:
        for name, data in transfer.read_montage_tar(
            tar,
            n_components=n_components,
            montage_format=payload.montage_format,
            max_member_bytes=settings.INGEST_MAX_MONTAGE_BYTES,
        ):
            montage_store.save(prefix + name, data)
            hashed.append((name, hashlib.sha256(data).digest()))
        expected = 3 * n_components
        if len(hashed) != expected:
            raise PushRejected(
                f"{len(hashed)} montages for {n_components} components, "
                f"expected {expected}"
            )
        recomputed = montage.digest_hashed_montages(hashed)
        if recomputed != digest:
            raise PushRejected(
                f"the montages digest {recomputed}, but the payload says {digest}"
            )
    except Exception:
        if not preexisting:
            montage_store.delete_digest(run_uuid, digest)
        raise
    return len(hashed)


def create_pushed_run(*, payload: RunPayload) -> Run:
    """Create the rows for a pushed run whose montages are already stored.

    One short transaction, for the reason `ingest_run` spells out: SQLite runs
    with transaction_mode=IMMEDIATE, so an atomic block holds the single write
    lock for its whole duration and every concurrent rating POST waits on it.
    Validation, tar reading and file writing all happen before this opens.
    """
    with transaction.atomic():
        run = Run.objects.create(
            uuid=payload.uuid,
            path=payload.path,
            label=payload.label,
            sub=payload.sub,
            ses=payload.ses,
            task=payload.task,
            run=payload.run,
            tr=payload.tr,
            n_timepoints=payload.n_timepoints,
            fd=payload.fd,
            frequencies=payload.frequencies,
            metric_stats=payload.metric_stats.model_dump(),
            montage_format=payload.montage_format,
            montage_digest=payload.montage_digest,
        )
        components = Component.objects.bulk_create(
            Component(
                run=run,
                index=component.index,
                explained_var=component.explained_var,
                total_var=component.total_var,
                timecourse=component.timecourse,
                spectrum=component.spectrum,
                metrics={
                    name: value.model_dump()
                    for name, value in component.metrics.items()
                },
            )
            for component in payload.components
        )
        for fix in payload.fix:
            reviewer = _pushed_fix_reviewer(fix)
            Classification.objects.bulk_create(
                Classification(
                    component=component,
                    reviewer=reviewer,
                    label=verdict.label,
                    probability=verdict.probability,
                )
                for component, verdict in zip(components, fix.verdicts)
            )
    logger.info("ingest: created %s (%s)", run.label, run.uuid)
    return run


def refresh_pushed_run(*, run: Run, payload: RunPayload) -> Run:
    """Point an already-ingested run at a newly pushed set of montages.

    A run that exists is only ever re-rendered, never rewritten: this touches
    `montage_format` and `montage_digest` and nothing else. No Component and
    no Classification is reachable from here, which is what bounds what a
    compromised ingest account can do to a run reviewers have already worked
    on — and it matches the only reason to push a run twice, which is that its
    images were re-rendered.

    The old sets are dropped only after the row points at the new one, so no
    reviewer is ever served a 404 for an image the page still references.
    """
    run.montage_format = payload.montage_format
    run.montage_digest = payload.montage_digest
    run.save(update_fields=["montage_format", "montage_digest"])
    montage_store.retain_digest(run.uuid, payload.montage_digest)
    logger.info("ingest: refreshed %s (%s)", run.label, run.uuid)
    return run


def check_pushed_run(*, run: Run | None, payload: RunPayload) -> None:
    """Everything that can be refused before a single byte is written.

    Called first, so a rejection leaves the store exactly as it was.
    """
    n_components = len(payload.components)
    if not 0 < n_components <= settings.INGEST_MAX_COMPONENTS:
        raise PushRejected(
            f"{n_components} components, over the "
            f"{settings.INGEST_MAX_COMPONENTS} this server accepts"
        )
    if [c.index for c in payload.components] != list(range(1, n_components + 1)):
        raise PushRejected("components are not 1..n in order")
    for fix in payload.fix:
        if len(fix.verdicts) != n_components:
            raise PushRejected(
                f"{melodic.fix_reviewer_name(fix.model, fix.threshold)} has "
                f"{len(fix.verdicts)} verdicts for {n_components} components"
            )
        _reject_reviewer_name_clash(fix)
    if run is None:
        return
    stored = run.components.count()
    if stored != n_components:
        raise PushRejected(
            f"{run.label} has {stored} components here and {n_components} were "
            "pushed; delete the run on this server first"
        )


def _reject_reviewer_name_clash(fix: FixReviewerPayload) -> None:
    """Refuse a pushed FIX reviewer whose name a human already holds.

    The name is derived rather than sent, by the same function `ingest_run`
    uses, so a payload cannot choose what it is called — and a human
    reviewer's name is a bare username, which can never take the
    ``<model> @ thr<N>`` shape. This turns that "cannot happen" into a 409
    rather than an IntegrityError on the next rating that human makes:
    `Reviewer.name` is unique and `reviewer_for_user` looks up on `user`, so a
    collision would break them permanently and silently.
    """
    name = melodic.fix_reviewer_name(fix.model, fix.threshold)
    if Reviewer.objects.filter(name=name).exclude(kind=Reviewer.Kind.FIX).exists():
        raise PushRejected(f"{name!r} is a human reviewer on this server")


def _pushed_fix_reviewer(fix: FixReviewerPayload) -> Reviewer:
    """The Reviewer row for a pushed pyFIX model+threshold."""
    _reject_reviewer_name_clash(fix)
    reviewer, _ = Reviewer.objects.get_or_create(
        name=melodic.fix_reviewer_name(fix.model, fix.threshold),
        defaults={
            "kind": Reviewer.Kind.FIX,
            "fix_model": fix.model,
            "fix_threshold": fix.threshold,
        },
    )
    return reviewer
