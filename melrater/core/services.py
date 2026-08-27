"""Write-side operations (HackSoft-style services)."""

from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from melrater.core import melodic, metrics, montage
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


def ingest_run(*, path: Path, image_workers: int = 0) -> Run:
    """Ingest one MELODIC+pyFIX derivatives directory.

    Creates the Run, its Components with chart payloads, one FIX Reviewer +
    Classifications per fix4melview file, and renders the slice montages
    into MEDIA_ROOT/runs/<id>/.
    """
    path = path.resolve()
    if Run.objects.filter(path=str(path)).exists():
        raise RunAlreadyIngested(str(path))
    source = melodic.load_source(path)
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
            metric_stats=metrics.metric_stats_payload(table, signal_rows),
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
                metrics=metrics.component_metrics_payload(table, i),
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
            _render_montages(run, source, image_workers)
        except Exception:
            shutil.rmtree(
                Path(settings.MEDIA_ROOT) / "runs" / str(run.pk), ignore_errors=True
            )
            raise
    return run


def _render_montages(run: Run, source: melodic.MelodicSource, workers: int) -> None:
    ica = source.root / "filtered_func_data.ica"
    mean_img = melodic.nib.load(ica / "mean.nii.gz")
    mask_img = melodic.nib.load(ica / "mask.nii.gz")
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
        ica / "melodic_IC.nii.gz",
        list(range(1, source.n_components + 1)),
        bg,
        picks_by_axis,
        window,
        out_dir,
        workers=workers,
    )
