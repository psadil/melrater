from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import ProtectedError

from melrater.core import melodic, montage, selectors, services
from melrater.core.models import Classification, Component, Reviewer, Run
from tests.conftest import N_COMPONENTS, N_TIMEPOINTS, TR, montage_dir, run_inputs

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
    files = list((montage_dir(media_root, ingested_run)).iterdir())

    assert len(files) == N_COMPONENTS * 3


def test_ingest_montage_names_encode_component_and_axis(
    ingested_run: Run, media_root: Path
) -> None:
    files = {p.name for p in (montage_dir(media_root, ingested_run)).iterdir()}

    assert f"ic001_axial.{ingested_run.montage_format}" in files


def test_ingest_rolls_back_when_montage_rendering_fails(
    melodic_dir: Path, media_root: Path
) -> None:
    # Arrange: remove the montage background so rendering must fail
    (melodic_dir / "filtered_func_data.ica" / "mean.nii.gz").unlink()

    # Act
    with pytest.raises(FileNotFoundError):
        services.ingest_run(source=melodic.load_run(run_inputs(melodic_dir)))

    # Assert: nothing half-ingested remains, so a fixed run can re-import
    assert Run.objects.count() == 0


def test_failed_ingest_leaves_no_media(melodic_dir: Path, media_root: Path) -> None:
    # Arrange
    (melodic_dir / "filtered_func_data.ica" / "mean.nii.gz").unlink()

    # Act
    with pytest.raises(FileNotFoundError):
        services.ingest_run(source=melodic.load_run(run_inputs(melodic_dir)))

    # Assert
    runs_dir = media_root / "runs"
    assert not runs_dir.exists() or not any(runs_dir.iterdir())


def test_rerender_montages_recreates_files(ingested_run: Run, media_root: Path) -> None:
    # Arrange: wipe the rendered images
    out_dir = montage_dir(media_root, ingested_run)
    for path in out_dir.iterdir():
        path.unlink()

    # Act
    call_command("rerender_montages", ingested_run.pk, "--workers", "1")

    # Assert
    assert len(list(out_dir.iterdir())) == N_COMPONENTS * 3


def test_rerender_keeps_format_when_rendering_fails(
    ingested_run: Run, melodic_dir: Path
) -> None:
    # Arrange: remove the montage background so rendering must fail
    original_format = ingested_run.montage_format
    (melodic_dir / "filtered_func_data.ica" / "mean.nii.gz").unlink()

    # Act
    with pytest.raises(FileNotFoundError):
        services.rerender_montages(run=ingested_run)

    # Assert: the page keeps serving the still-present old files
    ingested_run.refresh_from_db()
    assert ingested_run.montage_format == original_format


def test_rerender_updates_format_on_codec_change(
    ingested_run: Run, media_root: Path, monkeypatch
) -> None:
    # Arrange: simulate the AVIF codec disappearing
    monkeypatch.setattr(montage, "AVIF_OK", False)

    # Act
    services.rerender_montages(run=ingested_run)

    # Assert
    ingested_run.refresh_from_db()
    assert ingested_run.montage_format == "png"


def test_rerender_removes_stale_extension_files(
    ingested_run: Run, media_root: Path, monkeypatch
) -> None:
    # Arrange
    original_format = ingested_run.montage_format
    monkeypatch.setattr(montage, "AVIF_OK", False)

    # Act: codec flip re-renders as png
    services.rerender_montages(run=ingested_run)

    # Assert: no orphaned files from the previous format remain
    out_dir = montage_dir(media_root, ingested_run)
    assert not list(out_dir.glob(f"*.{original_format}"))


def test_rerender_montages_rejects_unknown_run(ingested_run: Run) -> None:
    # Act / Assert
    with pytest.raises(CommandError, match="unknown run ids"):
        call_command("rerender_montages", 999)


def test_ingest_run_twice_raises(ingested_run: Run, melodic_dir: Path) -> None:
    with pytest.raises(services.RunAlreadyIngested):
        services.ingest_run(source=melodic.load_run(run_inputs(melodic_dir)))


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


def test_deleting_a_rater_is_refused_while_they_have_ratings(
    ingested_run: Run, user
) -> None:
    # Arrange: CASCADE here used to take the reviewer, and with it every
    # rating they had made, when the account was deleted
    component = ingested_run.components.get(index=1)
    services.rate_component(user=user, component=component, label="Signal")

    # Act / Assert
    with pytest.raises(ProtectedError):
        user.delete()


def test_montages_follow_the_configured_storage(
    ingested_run: Run, media_root: Path
) -> None:
    # Assert: nothing writes through MEDIA_ROOT directly, so redirecting the
    # storage relocates the montages and the repository stays clean
    assert montage_dir(media_root, ingested_run).is_dir()


def test_montage_urls_are_keyed_by_uuid(ingested_run: Run) -> None:
    # Act: primary keys are reassigned by loaddata; uuids are not
    urls = selectors.montage_urls(ingested_run.components.get(index=1))

    # Assert
    assert f"/media/runs/{ingested_run.uuid}/" in urls["axial"]


def test_montage_urls_carry_the_render_revision(ingested_run: Run) -> None:
    # Arrange
    services.rerender_montages(run=ingested_run, image_workers=1)
    ingested_run.refresh_from_db()

    # Act
    urls = selectors.montage_urls(ingested_run.components.get(index=1))

    # Assert: a new revision means new URLs, which is what makes the immutable
    # cache header on /media/ honest
    assert urls["axial"].endswith("?v=1")
