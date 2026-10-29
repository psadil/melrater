"""Migration 0010: what the rows already in a database become.

Driven through the migration executor rather than by calling the function, as
`test_fill_fix_contributions.py` does: 0010 reads `updated_at`, which the
current models no longer have, so its rows can only be set up in a database
still at 0009.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import F

from melrater.core.models import Classification

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("core", "0009_fill_fix_contributions")]
AFTER = [("core", "0010_classification_history")]


@dataclass(frozen=True)
class Legacy:
    """The pks of three rows stored the 0009 way, and one's last write."""

    once: int  # a human rating never written again
    revised: int  # a human rating written again a minute later
    fix: int  # a FIX verdict
    revised_at: datetime  # that second write, i.e. its old updated_at


def _migrate(target: list[tuple[str, str]]) -> MigrationExecutor:
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    return executor


@pytest.fixture
def legacy() -> Iterator[Legacy]:
    old = _migrate(BEFORE).loader.project_state(BEFORE).apps
    run = old.get_model("core", "Run").objects.create(
        path="/data/run0",
        label="run000",
        tr=2.0,
        n_timepoints=4,
        fd=[],
        frequencies=[],
        metric_stats={"names": [], "dropped": [], "stats": {}},
    )
    components = [
        old.get_model("core", "Component").objects.create(
            run=run,
            index=index,
            explained_var=1.0,
            total_var=1.0,
            timecourse=[],
            spectrum=[],
            metrics={},
        )
        for index in (1, 2)
    ]
    Reviewer = old.get_model("core", "Reviewer")
    human = Reviewer.objects.create(
        kind="human",
        name="rater",
        user=old.get_model("auth", "User").objects.create(username="rater"),
    )
    fix = Reviewer.objects.create(kind="fix", name="TestModel @ thr5")
    Stored = old.get_model("core", "Classification")
    once = Stored.objects.create(component=components[0], reviewer=human, label="Noise")
    revised = Stored.objects.create(
        component=components[1], reviewer=human, label="Signal"
    )
    verdict = Stored.objects.create(
        component=components[0], reviewer=fix, label="Noise", probability=0.01
    )
    # .update(), never .save(): auto_now would stamp the save's own time
    Stored.objects.filter(pk=revised.pk).update(
        updated_at=F("created_at") + timedelta(minutes=1)
    )
    revised_at = Stored.objects.get(pk=revised.pk).updated_at

    _migrate(AFTER)
    yield Legacy(
        once=once.pk, revised=revised.pk, fix=verdict.pk, revised_at=revised_at
    )
    # Leave the schema where every other test expects it. A no-op unless a
    # test moved it.
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def test_every_row_gets_a_uuid_of_its_own(legacy: Legacy) -> None:
    # Act
    uuids = set(Classification.history.values_list("uuid", flat=True))

    # Assert
    assert len(uuids) == 3


def test_a_rating_written_again_is_marked_revised(legacy: Legacy) -> None:
    # Act
    row = Classification.history.get(pk=legacy.revised)

    # Assert
    assert row.action == Classification.Action.REVISED


def test_a_revised_rating_is_timed_at_its_last_write(legacy: Legacy) -> None:
    # Act
    row = Classification.history.get(pk=legacy.revised)

    # Assert
    assert row.created_at == legacy.revised_at


def test_a_rating_never_written_again_stays_a_rating(legacy: Legacy) -> None:
    # Act
    row = Classification.history.get(pk=legacy.once)

    # Assert
    assert row.action == Classification.Action.RATE


def test_a_fix_verdict_stays_a_rating(legacy: Legacy) -> None:
    # Act
    row = Classification.history.get(pk=legacy.fix)

    # Assert
    assert row.action == Classification.Action.RATE


def test_the_view_holds_each_reviewers_rating(legacy: Legacy) -> None:
    # Act
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM core_classification_current ORDER BY id")
        in_view = [row[0] for row in cursor.fetchall()]

    # Assert
    assert in_view == sorted((legacy.once, legacy.revised, legacy.fix))


def test_reversing_refuses_once_a_reviewer_has_two_rows(legacy: Legacy) -> None:
    # Arrange: a re-rating after the migration
    once = Classification.history.get(pk=legacy.once)
    Classification.history.create(
        component=once.component, reviewer=once.reviewer, label="Signal"
    )

    # Act / Assert: the one-row-per-reviewer constraint cannot come back, and
    # going back must not quietly throw the second row away to make it fit
    with pytest.raises(IntegrityError):
        _migrate(BEFORE)
