"""Management commands: account issuing, batch import, and montage upkeep."""

import dataclasses
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError

from melrater.core import lake
from melrater.core.api import INGEST_GROUP
from melrater.core.models import Run
from tests.conftest import montage_dir, run_inputs

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


def test_import_run_reports_a_failure_by_exiting_non_zero(
    catalog: Path, two_runs, media_root: Path
) -> None:
    # Act / Assert
    with pytest.raises(CommandError, match="could not be ingested"):
        call_command("import_run", str(catalog), "--workers", "1")


def test_import_run_keeps_going_past_a_broken_run(
    catalog: Path, two_runs, media_root: Path
) -> None:
    # Act: the healthy run comes first, the broken one must not undo it
    with pytest.raises(CommandError):
        call_command("import_run", str(catalog), "--workers", "1")

    # Assert
    assert Run.objects.count() == 1


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
