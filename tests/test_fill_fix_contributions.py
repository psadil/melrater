"""Migration 0009: the one-time fill of FIX's reasons for verdicts already stored.

Called directly, against the current models, on a database that holds a run
ingested the old way: FIX's verdicts and probabilities, and no reasons.
"""

import importlib
from pathlib import Path

import pytest
from django.apps import apps

from melrater.core import fixmodel, melodic, services
from melrater.core.models import Classification, Reviewer, Run
from tests.conftest import run_inputs

pytestmark = pytest.mark.django_db

fill = importlib.import_module(
    "melrater.core.migrations.0009_fill_fix_contributions"
).fill_contributions


@pytest.fixture
def legacy_run(explained_melodic_dir: Path, media_root: Path) -> Run:
    """The explained fixture run, ingested as it was before reasons existed."""
    return services.ingest_run(
        source=melodic.load_run(run_inputs(explained_melodic_dir))
    )


def _unexplained() -> int:
    return Classification.objects.filter(
        reviewer__kind=Reviewer.Kind.FIX, contributions__isnull=True
    ).count()


def test_migration_fills_every_verdict_it_can_explain(
    legacy_run: Run, fix_models_dir: Path, monkeypatch
) -> None:
    # Arrange
    monkeypatch.setenv("MELRATER_FIX_MODELS", str(fix_models_dir))

    # Act
    fill(apps, None)

    # Assert
    assert _unexplained() == 0


def test_migration_fills_reasons_that_add_up_to_the_stored_call(
    legacy_run: Run, fix_models_dir: Path, monkeypatch
) -> None:
    # Arrange
    monkeypatch.setenv("MELRATER_FIX_MODELS", str(fix_models_dir))

    # Act
    fill(apps, None)
    row = Classification.objects.get(
        component__run=legacy_run, component__index=3, reviewer__kind="fix"
    )

    # Assert
    assert fixmodel.explains(row.probability, row.baseline, row.contributions)


def test_migration_leaves_reasons_that_are_already_there(
    legacy_run: Run, fix_models_dir: Path, monkeypatch
) -> None:
    # Arrange: one row already explained, as a fixture load would leave it
    monkeypatch.setenv("MELRATER_FIX_MODELS", str(fix_models_dir))
    first = Classification.objects.filter(
        component__run=legacy_run, component__index=1, reviewer__kind="fix"
    )
    first.update(baseline=0.0, contributions={"kept": 1.0})

    # Act
    fill(apps, None)

    # Assert
    assert first.get().contributions == {"kept": 1.0}


def test_migration_skips_a_run_its_reasons_do_not_add_up_for(
    legacy_run: Run, fix_models_dir: Path, monkeypatch
) -> None:
    # Arrange: a stored probability the model never gave
    monkeypatch.setenv("MELRATER_FIX_MODELS", str(fix_models_dir))
    Classification.objects.filter(
        component__run=legacy_run, component__index=2, reviewer__kind="fix"
    ).update(probability=0.5)

    # Act
    fill(apps, None)

    # Assert: nothing on that run, rather than reasons for the wrong call
    assert _unexplained() == legacy_run.components.count()


def test_migration_skips_a_run_whose_model_is_missing(
    legacy_run: Run, tmp_path: Path, monkeypatch
) -> None:
    # Arrange
    monkeypatch.setenv("MELRATER_FIX_MODELS", str(tmp_path))

    # Act
    fill(apps, None)

    # Assert
    assert _unexplained() == legacy_run.components.count()


def test_migration_refuses_in_a_source_checkout_without_models(
    legacy_run: Run, monkeypatch
) -> None:
    # Arrange: what `pixi run manage migrate` in the default environment sees
    monkeypatch.delenv("MELRATER_FIX_MODELS", raising=False)
    monkeypatch.setenv("MELRATER_DEV", "1")

    # Act / Assert: recorded as applied, it would never run again
    with pytest.raises(RuntimeError, match="MELRATER_FIX_MODELS"):
        fill(apps, None)


def test_migration_steps_aside_on_a_deployment(legacy_run: Run, monkeypatch) -> None:
    # Arrange: the container's entrypoint migrates with neither present
    monkeypatch.delenv("MELRATER_FIX_MODELS", raising=False)
    monkeypatch.delenv("MELRATER_DEV", raising=False)

    # Act: must not fail, or the container would not start
    fill(apps, None)

    # Assert: its rows arrive as a fixture instead
    assert _unexplained() == legacy_run.components.count()
