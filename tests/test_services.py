from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import ProtectedError

from melrater.core import melodic, montage, resample, selectors, services
from melrater.core.models import Classification, Component, Reviewer, Run
from tests.conftest import (
    N_COMPONENTS,
    N_TIMEPOINTS,
    TR,
    anat_run_inputs,
    montage_dir,
    run_inputs,
    write_registration,
)

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


def test_ingest_renders_one_montage_per_component_smoothing_and_axis(
    ingested_run: Run, media_root: Path
) -> None:
    files = list((montage_dir(media_root, ingested_run)).iterdir())

    assert len(files) == montage.montage_count(
        N_COMPONENTS, ("func",), montage.SMOOTHINGS
    )


def test_ingest_montage_names_encode_component_background_smoothing_and_axis(
    ingested_run: Run, media_root: Path
) -> None:
    files = {p.name for p in (montage_dir(media_root, ingested_run)).iterdir()}

    assert f"ic001_func_raw_axial.{ingested_run.montage_format}" in files


def test_ingest_names_the_smoothed_montages(
    ingested_run: Run, media_root: Path
) -> None:
    files = {p.name for p in (montage_dir(media_root, ingested_run)).iterdir()}

    assert f"ic001_func_smooth_axial.{ingested_run.montage_format}" in files


def test_ingest_records_both_smoothing_levels(ingested_run: Run) -> None:
    # Assert: both need nothing but the IC map and the mask, so every run has both
    assert ingested_run.montage_smoothings == ["raw", "smooth"]


def test_ingest_records_the_slice_picks_per_axis(ingested_run: Run) -> None:
    # Assert: the page draws the slice labels from these
    assert set(ingested_run.montage_picks) == {"axial", "coronal", "sagittal"}


def test_ingest_renders_the_same_set_with_parallel_workers(
    melodic_dir: Path, media_root: Path
) -> None:
    # Arrange / Act: the ProcessPool path, which pickles the mask and zooms
    # into each worker and imports scipy in the child
    run = services.ingest_run(
        source=melodic.load_run(run_inputs(melodic_dir)), image_workers=2
    )

    # Assert
    assert len(list(montage_dir(media_root, run).iterdir())) == montage.montage_count(
        N_COMPONENTS, ("func",), montage.SMOOTHINGS
    )


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
    assert len(list(out_dir.iterdir())) == montage.montage_count(
        N_COMPONENTS, ("func",), montage.SMOOTHINGS
    )


def test_rerender_records_the_slice_picks(ingested_run: Run, media_root: Path) -> None:
    # Arrange: a run from before the picks were recorded
    ingested_run.montage_picks = {}
    ingested_run.save(update_fields=["montage_picks"])

    # Act
    services.rerender_montages(run=ingested_run)

    # Assert: a re-render is where such a run gains its slice labels
    assert set(ingested_run.montage_picks) == {"axial", "coronal", "sagittal"}


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


def test_montages_land_under_the_configured_media_root(
    ingested_run: Run, media_root: Path
) -> None:
    # Assert: montages are ordinary Django media, so pointing MEDIA_ROOT at a
    # temporary directory relocates them and the repository stays clean
    assert montage_dir(media_root, ingested_run).is_dir()


def test_montage_urls_are_keyed_by_uuid(ingested_run: Run) -> None:
    # Act: primary keys are reassigned by loaddata; uuids are not
    urls = selectors.montage_urls(ingested_run.components.get(index=1))

    # Assert
    assert f"/media/runs/{ingested_run.uuid}/" in urls["func"]["raw"]["axial"]


def test_montage_urls_carry_the_content_digest(ingested_run: Run) -> None:
    # Act
    urls = selectors.montage_urls(ingested_run.components.get(index=1))

    # Assert: the digest is in the path, which is what makes the immutable
    # cache header on /media/ honest — different bytes are a different URL
    assert f"/{ingested_run.montage_digest}/" in urls["func"]["raw"]["axial"]


def test_rerender_leaves_no_superseded_montages(
    ingested_run: Run, media_root: Path, monkeypatch
) -> None:
    # Arrange: a format change is the cheapest way to force different bytes
    monkeypatch.setattr(montage, "AVIF_OK", False)

    # Act
    services.rerender_montages(run=ingested_run, image_workers=1)
    ingested_run.refresh_from_db()

    # Assert: the old set is dropped once the row points at the new one
    digests = [p.name for p in (media_root / "runs" / str(ingested_run.uuid)).iterdir()]
    assert digests == [str(ingested_run.montage_digest)]


def test_ingest_records_the_functional_background_alone(ingested_run: Run) -> None:
    # Assert: a run with no FEAT registration still ingests, one background
    assert ingested_run.montage_backgrounds == ["func"]


def test_ingest_records_both_backgrounds_when_registered(anat_ingested_run) -> None:
    # Assert
    assert anat_ingested_run.montage_backgrounds == ["func", "anat"]


def test_ingest_renders_a_montage_per_background_and_axis(
    anat_ingested_run, media_root: Path
) -> None:
    # Act
    files = list(montage_dir(media_root, anat_ingested_run).iterdir())

    # Assert
    assert len(files) == montage.montage_count(
        N_COMPONENTS, ("func", "anat"), montage.SMOOTHINGS
    )


def test_ingest_names_the_anatomical_montages(
    anat_ingested_run, media_root: Path
) -> None:
    files = {p.name for p in montage_dir(media_root, anat_ingested_run).iterdir()}

    assert f"ic001_anat_raw_axial.{anat_ingested_run.montage_format}" in files


def test_rerender_picks_up_a_registration_added_after_ingest(
    ingested_run: Run, melodic_dir: Path, media_root: Path
) -> None:
    # Arrange: registration finishing after the run was first ingested, which
    # is the ordinary case for a tree a pipeline is still filling. Written
    # here rather than by a fixture so the sequence is the test's, not
    # pytest's.
    write_registration(melodic_dir)
    services.rerender_montages(run=ingested_run)

    # Assert: a re-render is where a run gains its anatomical background
    assert ingested_run.montage_backgrounds == ["func", "anat"]


def test_rerender_drops_a_registration_that_went_away(
    anat_ingested_run, anat_melodic_dir: Path, media_root: Path
) -> None:
    # Arrange
    (anat_melodic_dir / "reg" / "highres.nii.gz").unlink()

    # Act
    services.rerender_montages(run=anat_ingested_run)

    # Assert: both or neither, so a stale anatomical is never half-served
    assert anat_ingested_run.montage_backgrounds == ["func"]


def test_ingest_fails_on_a_registration_that_is_present_but_broken(
    anat_melodic_dir: Path, media_root: Path
) -> None:
    # Arrange: missing is tolerated, corrupt is not
    (anat_melodic_dir / "reg" / "highres2example_func.mat").write_text("not a matrix\n")

    # Act / Assert
    with pytest.raises(resample.RegistrationError):
        services.ingest_run(source=melodic.load_run(anat_run_inputs(anat_melodic_dir)))
