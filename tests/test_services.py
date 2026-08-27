from pathlib import Path

import pytest

from melrater.core import services
from melrater.core.models import Classification, Component, Reviewer, Run
from tests.conftest import N_COMPONENTS, N_TIMEPOINTS, TR

pytestmark = pytest.mark.django_db


def test_ingest_stores_tr(ingested_run: Run) -> None:
    assert ingested_run.tr == pytest.approx(TR)


def test_ingest_stores_timepoint_count(ingested_run: Run) -> None:
    assert ingested_run.n_timepoints == N_TIMEPOINTS


def test_ingest_creates_all_components(ingested_run: Run) -> None:
    assert ingested_run.components.count() == N_COMPONENTS


def test_ingest_stores_full_timecourse(ingested_run: Run) -> None:
    assert len(ingested_run.components.get(index=1).timecourse) == N_TIMEPOINTS


def test_ingest_stores_component_metrics(ingested_run: Run) -> None:
    assert "featA" in ingested_run.components.get(index=1).metrics


def test_ingest_names_fix_reviewer(ingested_run: Run) -> None:
    assert Reviewer.objects.get(kind=Reviewer.Kind.FIX).name == "TestModel @ thr5"


def test_ingest_stores_fix_labels_in_component_order(ingested_run: Run) -> None:
    labels = list(
        Classification.objects.filter(reviewer__kind=Reviewer.Kind.FIX)
        .order_by("component__index")
        .values_list("label", flat=True)
    )

    assert labels == ["Signal", "Noise", "Noise"]


def test_ingest_renders_one_montage_per_component_axis(
    ingested_run: Run, media_root: Path
) -> None:
    files = list((media_root / "runs" / str(ingested_run.pk)).iterdir())

    assert len(files) == N_COMPONENTS * 3


def test_ingest_montage_names_encode_component_and_axis(
    ingested_run: Run, media_root: Path
) -> None:
    files = {p.name for p in (media_root / "runs" / str(ingested_run.pk)).iterdir()}

    assert f"ic001_axial.{ingested_run.montage_format}" in files


def test_ingest_rolls_back_when_montage_rendering_fails(
    melodic_dir: Path, media_root: Path
) -> None:
    # Arrange: remove the montage background so rendering must fail
    (melodic_dir / "filtered_func_data.ica" / "mean.nii.gz").unlink()

    # Act
    with pytest.raises(FileNotFoundError):
        services.ingest_run(path=melodic_dir)

    # Assert: nothing half-ingested remains, so a fixed run can re-import
    assert Run.objects.count() == 0


def test_failed_ingest_leaves_no_media(melodic_dir: Path, media_root: Path) -> None:
    # Arrange
    (melodic_dir / "filtered_func_data.ica" / "mean.nii.gz").unlink()

    # Act
    with pytest.raises(FileNotFoundError):
        services.ingest_run(path=melodic_dir)

    # Assert
    runs_dir = media_root / "runs"
    assert not runs_dir.exists() or not any(runs_dir.iterdir())


def test_ingest_run_twice_raises(ingested_run: Run, melodic_dir: Path) -> None:
    with pytest.raises(services.RunAlreadyIngested):
        services.ingest_run(path=melodic_dir)


def test_rate_component_updates_in_place(ingested_run: Run, user) -> None:
    # Arrange
    component = Component.objects.get(run=ingested_run, index=2)

    # Act
    first = services.rate_component(user=user, component=component, label="Signal")
    second = services.rate_component(user=user, component=component, label="Noise")

    # Assert: one row per (component, human reviewer), updated in place
    assert (first.pk, second.label) == (second.pk, "Noise")


def test_rate_component_creates_human_reviewer(ingested_run: Run, user) -> None:
    # Arrange
    component = Component.objects.get(run=ingested_run, index=2)

    # Act
    services.rate_component(user=user, component=component, label="Signal")

    # Assert
    assert Reviewer.objects.get(user=user).kind == Reviewer.Kind.HUMAN


def test_rate_component_rejects_bad_label(ingested_run: Run, user) -> None:
    # Arrange
    component = Component.objects.get(run=ingested_run, index=1)

    # Act / Assert
    with pytest.raises(ValueError, match="invalid label"):
        services.rate_component(user=user, component=component, label="Artifact")
