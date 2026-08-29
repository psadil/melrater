"""Management commands: account issuing, batch import, and run transfer."""

import dataclasses
import json
import tarfile
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError

from melrater.core import lake, services
from melrater.core.models import Classification, Component, Reviewer, Run
from tests.conftest import N_COMPONENTS, run_inputs

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


# --- export_runs / loaddata --------------------------------------------


@pytest.fixture
def bundle(tmp_path: Path, ingested_run: Run, user) -> Path:
    """A one-run export, made after a human rating exists locally."""
    component = ingested_run.components.get(index=1)
    services.rate_component(user=user, component=component, label="Signal")
    out = tmp_path / "outgoing"
    call_command("export_runs", "--out", str(out))
    return out


def test_export_writes_a_fixture_and_a_media_tar(bundle: Path) -> None:
    # Assert
    assert {p.name for p in bundle.iterdir()} == {
        "runs-001.json",
        "runs-001-media.tar",
    }


def _fixture_rows(bundle: Path) -> list[dict]:
    return json.loads((bundle / "runs-001.json").read_text())


def test_export_carries_the_run_itself(bundle: Path) -> None:
    # Assert
    assert any(row["model"] == "core.run" for row in _fixture_rows(bundle))


def test_export_omits_human_reviewers(bundle: Path) -> None:
    # Assert: the property the whole transfer design rests on — a fixture can
    # carry no human reviewer, so loading one cannot overwrite server work
    kinds = {
        row["fields"]["kind"]
        for row in _fixture_rows(bundle)
        if row["model"] == "core.reviewer"
    }
    assert kinds == {"fix"}


def test_export_omits_human_classifications(bundle: Path) -> None:
    # Arrange: the `bundle` fixture rates a component before exporting
    reviewers = {
        row["fields"]["name"]: row["fields"]["kind"]
        for row in _fixture_rows(bundle)
        if row["model"] == "core.reviewer"
    }

    # Assert: every classification names a reviewer the fixture calls FIX
    assert all(
        reviewers.get(row["fields"]["reviewer"][0]) == "fix"
        for row in _fixture_rows(bundle)
        if row["model"] == "core.classification"
    )


def test_export_media_tar_is_keyed_by_uuid(bundle: Path, ingested_run: Run) -> None:
    # Assert: extracting under media/ puts the files where montage_urls looks
    with tarfile.open(bundle / "runs-001-media.tar") as tar:
        names = tar.getnames()
    assert all(name.startswith(f"runs/{ingested_run.uuid}/") for name in names)


def test_loaddata_restores_a_deleted_run(bundle: Path, ingested_run: Run) -> None:
    # Arrange: the rows are gone; the fixture is the only copy
    uuid = ingested_run.uuid
    Run.objects.all().delete()

    # Act
    call_command("loaddata", str(bundle / "runs-001.json"))

    # Assert
    assert Component.objects.filter(run__uuid=uuid).count() == N_COMPONENTS


def test_loaddata_does_not_duplicate_an_existing_run(bundle: Path) -> None:
    # Act: natural keys mean a second load updates in place
    call_command("loaddata", str(bundle / "runs-001.json"))

    # Assert
    assert Run.objects.count() == 1


def test_loaddata_leaves_human_ratings_untouched(bundle: Path, user) -> None:
    # Arrange: this is the server's side of the transfer — a rating made here
    # must survive a bundle arriving from the laptop
    before = Classification.objects.get(reviewer__user=user)

    # Act
    call_command("loaddata", str(bundle / "runs-001.json"))

    # Assert
    assert Classification.objects.filter(pk=before.pk, label=before.label).exists()


def test_loaddata_restores_fix_verdicts(bundle: Path, ingested_run: Run) -> None:
    # Arrange
    Classification.objects.filter(reviewer__kind=Reviewer.Kind.FIX).delete()

    # Act
    call_command("loaddata", str(bundle / "runs-001.json"))

    # Assert
    assert (
        Classification.objects.filter(reviewer__kind=Reviewer.Kind.FIX).count()
        == N_COMPONENTS
    )
