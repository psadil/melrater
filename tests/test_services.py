from pathlib import Path

import pytest

from melrater.core import services
from melrater.core.models import Classification, Component, Reviewer, Run
from tests.conftest import N_COMPONENTS, N_TIMEPOINTS, TR


@pytest.mark.django_db
def test_ingest_run_creates_domain_objects(melodic_dir: Path, media_root: Path) -> None:
    # Act
    run = services.ingest_run(path=melodic_dir)

    # Assert
    assert run.tr == pytest.approx(TR)
    assert run.n_timepoints == N_TIMEPOINTS
    assert run.components.count() == N_COMPONENTS
    component = run.components.get(index=1)
    assert len(component.timecourse) == N_TIMEPOINTS
    assert "featA" in component.metrics

    reviewer = Reviewer.objects.get(kind=Reviewer.Kind.FIX)
    assert reviewer.name == "TestModel @ thr5"
    labels = list(
        Classification.objects.filter(reviewer=reviewer)
        .order_by("component__index")
        .values_list("label", flat=True)
    )
    assert labels == ["Signal", "Noise", "Noise"]


@pytest.mark.django_db
def test_ingest_run_renders_montages(melodic_dir: Path, media_root: Path) -> None:
    # Act
    run = services.ingest_run(path=melodic_dir)

    # Assert: one file per component per display axis
    out_dir = media_root / "runs" / str(run.pk)
    files = sorted(p.name for p in out_dir.iterdir())
    assert len(files) == N_COMPONENTS * 3
    assert f"ic001_axial.{run.montage_format}" in files
    assert f"ic003_sagittal.{run.montage_format}" in files


@pytest.mark.django_db
def test_ingest_rolls_back_when_montage_rendering_fails(
    melodic_dir: Path, media_root: Path
) -> None:
    # Arrange: remove the montage background so rendering must fail
    (melodic_dir / "filtered_func_data.ica" / "mean.nii.gz").unlink()

    # Act / Assert: nothing half-ingested remains, so a fixed run can re-import
    with pytest.raises(FileNotFoundError):
        services.ingest_run(path=melodic_dir)
    assert Run.objects.count() == 0
    assert not (media_root / "runs").exists() or not any(
        (media_root / "runs").iterdir()
    )


@pytest.mark.django_db
def test_ingest_run_twice_raises(melodic_dir: Path, media_root: Path) -> None:
    # Arrange
    services.ingest_run(path=melodic_dir)

    # Act / Assert
    with pytest.raises(services.RunAlreadyIngested):
        services.ingest_run(path=melodic_dir)
    assert Run.objects.count() == 1


@pytest.mark.django_db
def test_rate_component_creates_then_updates(
    melodic_dir: Path, media_root: Path, django_user_model
) -> None:
    # Arrange
    services.ingest_run(path=melodic_dir)
    user = django_user_model.objects.create_user("rater")
    component = Component.objects.get(run__path=str(melodic_dir), index=2)

    # Act
    first = services.rate_component(user=user, component=component, label="Signal")
    second = services.rate_component(user=user, component=component, label="Noise")

    # Assert: one row per (component, human reviewer), updated in place
    assert first.pk == second.pk
    assert second.label == "Noise"
    assert Classification.objects.filter(reviewer__user=user).count() == 1
    assert Reviewer.objects.get(user=user).kind == Reviewer.Kind.HUMAN


@pytest.mark.django_db
def test_rate_component_rejects_bad_label(
    melodic_dir: Path, media_root: Path, django_user_model
) -> None:
    # Arrange
    services.ingest_run(path=melodic_dir)
    user = django_user_model.objects.create_user("rater")
    component = Component.objects.get(run__path=str(melodic_dir), index=1)

    # Act / Assert
    with pytest.raises(ValueError, match="invalid label"):
        services.rate_component(user=user, component=component, label="Artifact")
