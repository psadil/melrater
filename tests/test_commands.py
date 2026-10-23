"""Management commands: account issuing, batch import, and montage upkeep."""

import dataclasses
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError

from melrater.core import lake
from melrater.core.api import INGEST_GROUP
from melrater.core.models import Classification, MetricCohort, Reviewer, Run
from tests.conftest import FIX_MODEL, montage_dir, run_inputs

pytestmark = pytest.mark.django_db


# --- create_rater -------------------------------------------------------


def test_create_rater_makes_a_non_privileged_account(capsys) -> None:
    # Act
    call_command("create_rater", "alice")

    # Assert: a reviewer with admin rights could rewrite everyone's ratings
    alice = User.objects.get(username="alice")
    assert not (alice.is_staff or alice.is_superuser)


def test_create_rater_issues_a_usable_password(capsys) -> None:
    # Act
    call_command("create_rater", "alice")

    # Assert: the printed password is the only copy, so it has to work
    password = _printed_password(capsys)
    assert User.objects.get(username="alice").check_password(password)


def test_create_rater_refuses_to_clobber_an_existing_account() -> None:
    # Arrange
    call_command("create_rater", "alice")

    # Act / Assert
    with pytest.raises(Exception, match="already exists"):
        call_command("create_rater", "alice")


def test_create_rater_reset_issues_a_different_password(capsys) -> None:
    # Arrange
    call_command("create_rater", "alice")
    first = _printed_password(capsys)

    # Act
    call_command("create_rater", "alice", "--reset")

    # Assert
    assert _printed_password(capsys) != first


def _printed_password(capsys) -> str:
    for line in capsys.readouterr().out.splitlines():
        if line.strip().startswith("password "):
            return line.split("password ", 1)[1].strip()
    raise AssertionError("no password in the command output")


# --- import_run ---------------------------------------------------------


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    """A stand-in path; the catalog itself is monkeypatched away below."""
    path = tmp_path / "study.duckdb"
    path.touch()
    return path


def _discovered(melodic_dir: Path, *, broken: bool) -> lake.DiscoveredRun:
    inputs = run_inputs(melodic_dir)
    if broken:
        # a features file that is not there: melodic.load_run raises
        inputs = dataclasses.replace(inputs, features=melodic_dir / "nope.csv")
    return lake.DiscoveredRun(label=melodic_dir.name, root=melodic_dir, inputs=inputs)


@pytest.fixture
def two_runs(monkeypatch, melodic_dir: Path, tmp_path: Path):
    """One healthy run and one that fails, in that order."""
    healthy = _discovered(melodic_dir, broken=False)
    broken = _discovered(melodic_dir, broken=True)
    # a distinct root, so the second is not skipped as already ingested
    other = tmp_path / "sub-02_task-test_desc-preproc_bold"
    broken = lake.DiscoveredRun(label=other.name, root=other, inputs=broken.inputs)
    monkeypatch.setattr(lake, "open_catalog", lambda *a, **k: None)
    monkeypatch.setattr(lake, "discover_runs", lambda *a, **k: [healthy, broken])
    return healthy, broken


@pytest.fixture
def no_models(tmp_path: Path) -> Path:
    """A model directory with nothing in it: every FIX result goes unexplained."""
    path = tmp_path / "no-models"
    path.mkdir()
    return path


def _import(catalog: Path, models: Path, *args: str) -> None:
    call_command(
        "import_run", str(catalog), "--workers", "1", "--fix-models", str(models), *args
    )


def test_import_run_reports_a_failure_by_exiting_non_zero(
    catalog: Path, two_runs, media_root: Path, no_models: Path
) -> None:
    # Act / Assert
    with pytest.raises(CommandError, match="could not be ingested"):
        _import(catalog, no_models)


def test_import_run_keeps_going_past_a_broken_run(
    catalog: Path, two_runs, media_root: Path, no_models: Path
) -> None:
    # Act: the healthy run comes first, the broken one must not undo it
    with pytest.raises(CommandError):
        _import(catalog, no_models)

    # Assert
    assert Run.objects.count() == 1


def test_import_run_refuses_to_start_without_a_model_directory(
    catalog: Path, two_runs, media_root: Path
) -> None:
    # Act / Assert: nothing adds reasons to a run after ingest, so a campaign
    # without them is refused up front rather than discovered afterwards
    with pytest.raises(CommandError, match="--fix-models"):
        call_command("import_run", str(catalog), "--fix-models", "")


def test_import_run_refuses_a_model_directory_that_is_not_there(
    catalog: Path, two_runs, media_root: Path, tmp_path: Path
) -> None:
    # Act / Assert
    with pytest.raises(CommandError, match="not a directory"):
        _import(catalog, tmp_path / "missing")


@pytest.fixture
def one_explained_run(monkeypatch, explained_melodic_dir: Path):
    """A catalog that resolves to the explained fixture run alone."""
    found = lake.DiscoveredRun(
        label=explained_melodic_dir.name,
        root=explained_melodic_dir,
        inputs=run_inputs(explained_melodic_dir),
    )
    monkeypatch.setattr(lake, "open_catalog", lambda *a, **k: None)
    monkeypatch.setattr(lake, "discover_runs", lambda *a, **k: [found])
    return found


def test_import_run_stores_fixs_reasons(
    catalog: Path, one_explained_run, media_root: Path, fix_models_dir: Path
) -> None:
    # Act
    _import(catalog, fix_models_dir)

    # Assert
    assert not Classification.objects.filter(
        reviewer__kind=Reviewer.Kind.FIX, contributions__isnull=True
    ).exists()


def test_import_run_ingests_a_run_whose_model_is_missing(
    catalog: Path, one_explained_run, media_root: Path, no_models: Path
) -> None:
    # Act: FIX's verdict is still FIX's verdict without its reasons
    _import(catalog, no_models)

    # Assert
    assert Run.objects.count() == 1


def test_import_run_names_a_run_it_could_not_explain(
    catalog: Path, one_explained_run, media_root: Path, no_models: Path, capsys
) -> None:
    # Act
    _import(catalog, no_models)

    # Assert
    assert f"no {FIX_MODEL}.pyfix_model" in capsys.readouterr().err


def test_import_run_dry_run_writes_nothing(
    catalog: Path, two_runs, media_root: Path
) -> None:
    # Act
    call_command("import_run", str(catalog), "--dry-run")

    # Assert
    assert Run.objects.count() == 0


# --- ingest accounts ----------------------------------------------------


def test_create_rater_ingest_grants_push_rights(capsys) -> None:
    # Act
    call_command("create_rater", "laptop", "--ingest")

    # Assert
    assert User.objects.get(username="laptop").groups.filter(name=INGEST_GROUP).exists()


def test_create_rater_without_ingest_grants_nothing(capsys) -> None:
    # Act: a reviewer must not be able to push runs
    call_command("create_rater", "alice")

    # Assert
    assert not User.objects.get(username="alice").groups.exists()


def test_create_rater_ingest_stays_non_privileged(capsys) -> None:
    # Act
    call_command("create_rater", "laptop", "--ingest")

    # Assert: the group is the whole grant — no admin, no model permissions
    laptop = User.objects.get(username="laptop")
    assert not (laptop.is_staff or laptop.is_superuser)


# --- prune_orphan_montages ----------------------------------------------


def test_prune_removes_montages_no_run_names(
    ingested_run: Run, media_root: Path
) -> None:
    # Arrange: what a push that died before its rows leaves behind
    uuid = ingested_run.uuid
    Run.objects.all().delete()

    # Act
    call_command("prune_orphan_montages")

    # Assert
    assert not (media_root / "runs" / str(uuid)).exists()


def test_prune_keeps_a_run_that_is_still_named(
    ingested_run: Run, media_root: Path
) -> None:
    # Act
    call_command("prune_orphan_montages")

    # Assert
    assert montage_dir(media_root, ingested_run).is_dir()


def test_prune_dry_run_deletes_nothing(ingested_run: Run, media_root: Path) -> None:
    # Arrange
    uuid = ingested_run.uuid
    Run.objects.all().delete()

    # Act
    call_command("prune_orphan_montages", "--dry-run")

    # Assert
    assert (media_root / "runs" / str(uuid)).exists()


# --- refresh_metric_cohort ----------------------------------------------


def test_refresh_metric_cohort_pools_every_component(ingested_run: Run) -> None:
    # Act
    call_command("refresh_metric_cohort")

    # Assert
    assert MetricCohort.objects.get().n_components == ingested_run.components.count()


def test_refresh_metric_cohort_counts_the_runs_it_pooled(ingested_run: Run) -> None:
    # Act
    call_command("refresh_metric_cohort")

    # Assert: the page says this number, so it is the claim being made
    assert MetricCohort.objects.get().n_runs == 1


def test_refresh_metric_cohort_norms_every_stored_metric(ingested_run: Run) -> None:
    # Act
    call_command("refresh_metric_cohort")

    # Assert: `const` never reached a component row, being constant in the run
    assert set(MetricCohort.objects.get().norms) == {"featA", "featB", "featC"}


def test_refresh_metric_cohort_records_the_pooled_centre(ingested_run: Run) -> None:
    # Act
    call_command("refresh_metric_cohort")

    # Assert: featA = [1, 2, 30] is the whole cohort here, so its median is 2
    norms = MetricCohort.objects.get().norms
    assert norms["featA"]["median"] == pytest.approx(2.0)


def test_refresh_metric_cohort_pools_fixs_reasons_by_model(
    explained_ingested_run: Run,
) -> None:
    # Act
    call_command("refresh_metric_cohort")

    # Assert
    assert set(MetricCohort.objects.get().fix_norms) == {FIX_MODEL}


def test_refresh_metric_cohort_pools_each_feature_fix_split_on(
    explained_ingested_run: Run,
) -> None:
    # Act
    call_command("refresh_metric_cohort")

    # Assert: the small model splits on featA and featC and nothing else
    assert set(MetricCohort.objects.get().fix_norms[FIX_MODEL]) == {"featA", "featC"}


def test_refresh_metric_cohort_counts_a_component_once_per_model(
    explained_ingested_run: Run,
) -> None:
    # Arrange: a second threshold of the same model holds the same reasons
    second = Reviewer.objects.create(
        kind=Reviewer.Kind.FIX,
        name=f"{FIX_MODEL} @ thr10",
        fix_model=FIX_MODEL,
        fix_threshold=10,
    )
    Classification.objects.bulk_create(
        Classification(
            component=row.component,
            reviewer=second,
            label=row.label,
            probability=row.probability,
            baseline=row.baseline,
            contributions=row.contributions,
        )
        for row in Classification.objects.filter(reviewer__kind=Reviewer.Kind.FIX)
    )

    # Act
    call_command("refresh_metric_cohort")

    # Assert: three components, not six reviews of them
    norms = MetricCohort.objects.get().fix_norms
    assert norms[FIX_MODEL]["featA"]["n"] == explained_ingested_run.components.count()


def test_refresh_metric_cohort_stores_no_reasons_where_there_are_none(
    ingested_run: Run,
) -> None:
    # Act
    call_command("refresh_metric_cohort")

    # Assert
    assert MetricCohort.objects.get().fix_norms == {}


def test_refresh_metric_cohort_dry_run_stores_nothing(ingested_run: Run) -> None:
    # Act
    call_command("refresh_metric_cohort", "--dry-run")

    # Assert
    assert not MetricCohort.objects.exists()


def test_refresh_metric_cohort_says_so_with_nothing_to_pool(capsys) -> None:
    # Act
    call_command("refresh_metric_cohort")

    # Assert
    assert "no components to pool" in capsys.readouterr().out


def test_refresh_metric_cohort_leaves_no_cohort_when_there_is_nothing_to_pool() -> None:
    # Act
    call_command("refresh_metric_cohort")

    # Assert: an empty cohort would claim every strip has a reference it does not
    assert not MetricCohort.objects.exists()
