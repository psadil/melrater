"""Write-side operations (HackSoft-style services)."""

from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from melrater.core import lake, melodic, metrics, montage
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


def ingest_run(*, source: melodic.MelodicSource, image_workers: int = 0) -> Run:
    """Ingest one loaded MELODIC+pyFIX run (see lake.discover_runs).

    Creates the Run, its Components with chart payloads, one FIX Reviewer +
    Classifications per fix4melview file, and renders the slice montages
    into MEDIA_ROOT/runs/<id>/.
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

    with transaction.atomic():
        run = Run.objects.create(
            path=str(path),
            label=source.label,
            tr=source.tr,
            n_timepoints=source.n_timepoints,
            fd=[float(v) for v in source.fd],
            frequencies=[float(v) for v in source.frequencies],
            metric_stats=metrics.compute_metric_stats(table, signal_rows).model_dump(),
            montage_format="avif" if montage.AVIF_OK else "png",
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

        # inside the transaction so a render failure rolls the run back
        # instead of leaving a half-ingested run that blocks re-import
        try:
            _render_montages(
                run,
                ic_path=source.ic_path,
                mean_path=source.mean_path,
                mask_path=source.mask_path,
                n_components=source.n_components,
                workers=image_workers,
            )
        except Exception:
            shutil.rmtree(
                Path(settings.MEDIA_ROOT) / "runs" / str(run.pk), ignore_errors=True
            )
            raise
    return run


def rerender_montages(*, run: Run, image_workers: int = 0) -> None:
    """Re-render an ingested run's montages in place (after a display change).

    Reads the run directory's images again (named by the feat layout) but
    leaves the run's rows — and every reviewer's classifications — untouched.
    The stored montage_format is updated only after rendering succeeds, so a
    failed render leaves the page serving the still-present old files instead
    of 404s.
    """
    ic_path, mean_path, mask_path = lake.run_montage_paths(Path(run.path))
    _render_montages(
        run,
        ic_path=ic_path,
        mean_path=mean_path,
        mask_path=mask_path,
        n_components=run.components.count(),
        workers=image_workers,
    )
    fmt = "avif" if montage.AVIF_OK else "png"
    if run.montage_format != fmt:
        stale_ext = run.montage_format
        run.montage_format = fmt
        run.save(update_fields=["montage_format"])
        out_dir = Path(settings.MEDIA_ROOT) / "runs" / str(run.pk)
        for stale in out_dir.glob(f"*.{stale_ext}"):
            stale.unlink()


def _render_montages(
    run: Run,
    *,
    ic_path: Path,
    mean_path: Path,
    mask_path: Path,
    n_components: int,
    workers: int,
) -> None:
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
    out_dir = Path(settings.MEDIA_ROOT) / "runs" / str(run.pk)
    montage.render_run_montages(
        ic_path,
        list(range(1, n_components + 1)),
        bg,
        picks_by_axis,
        window,
        out_dir,
        workers=workers,
    )
