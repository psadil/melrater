"""Write-side operations (HackSoft-style services)."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from melrater.core import lake, melodic, metrics, montage
from melrater.core import storage as montage_store
from melrater.core.models import Classification, Component, Reviewer, Run


class RunAlreadyIngested(Exception):
    pass


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
        montage_store.store_directory(run_uuid, staged)

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
    Nothing reaches the store until the render has succeeded, so a failed
    render leaves the page serving the still-present old files rather than
    404s. `montage_rev` is bumped so that the montage URLs change, which is
    what lets them be served with an immutable cache header.
    """
    ic_path, mean_path, mask_path = lake.run_montage_paths(Path(run.path))
    with _staged_render(
        ic_path=ic_path,
        mean_path=mean_path,
        mask_path=mask_path,
        n_components=run.components.count(),
        workers=image_workers,
    ) as staged:
        montage_store.store_directory(run.uuid, staged)

    stale_ext = str(run.montage_format)
    fmt = montage_format()
    run.montage_format = fmt
    run.montage_rev = int(run.montage_rev) + 1
    run.save(update_fields=["montage_format", "montage_rev"])
    if stale_ext != fmt:
        montage_store.delete_extension(run.uuid, stale_ext)


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
