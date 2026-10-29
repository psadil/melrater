"""Every save is a row: what the readers see once reviewers change their minds.

Separate from `test_selectors.py`, which is deliberately database-free.
"""

from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import connection

from melrater.core import selectors, services
from melrater.core.models import Classification, Component, Run

pytestmark = pytest.mark.django_db


@pytest.fixture
def run(bare_runs) -> Run:
    return bare_runs(1)[0]


@pytest.fixture
def rerated(run: Run, user, other_rater) -> Component:
    """Two reviewers who each changed their mind, one also editing their note."""
    component = run.components.get(index=1)
    services.rate_component(
        user=user, component=component, label="Signal", note="network"
    )
    services.rate_component(user=user, component=component, label="Noise")
    services.set_component_note(user=user, component=component, note="motion after all")
    services.rate_component(user=other_rater, component=component, label="Unknown")
    services.rate_component(user=other_rater, component=component, label="Signal")
    return component


def _dump(path: Path) -> Path:
    call_command(
        "dumpdata",
        "core.classification",
        "--natural-foreign",
        "--natural-primary",
        output=str(path),
    )
    return path


def test_current_rows_are_one_per_reviewer(rerated: Component) -> None:
    # Act
    current = Classification.objects.filter(component=rerated)

    # Assert
    assert current.count() == 2


def test_current_rows_are_the_rows_the_sql_view_holds(rerated: Component) -> None:
    # Arrange: the rule is written twice, once for the app and once for SQL
    # written by hand (migration 0010), and the two must not drift apart
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM core_classification_current ORDER BY id")
        in_view = [row[0] for row in cursor.fetchall()]

    # Act
    current = list(Classification.objects.order_by("pk").values_list("pk", flat=True))

    # Assert
    assert current == in_view


def test_user_labels_for_run_hold_the_newest_label(
    rerated: Component, run: Run, user
) -> None:
    # Act
    labels = selectors.user_labels_for_run(run, user)

    # Assert
    assert labels == {1: "Noise"}


def test_user_label_for_component_is_the_newest(rerated: Component, user) -> None:
    # Act
    label = selectors.user_label_for_component(rerated, user)

    # Assert
    assert label == "Noise"


def test_notes_for_component_hold_the_newest_note(rerated: Component, user) -> None:
    # Act
    panel = selectors.notes_for_component(rerated, user)

    # Assert
    assert panel.mine == "motion after all"


def test_run_list_counts_a_rerated_component_once(rerated: Component, user) -> None:
    # Act
    page = selectors.run_list_page(user)

    # Assert
    assert page.rows[0].n_rated == 1


def test_a_discussion_post_carries_the_newest_label(rerated: Component, user) -> None:
    # Act
    post = services.create_discussion_post(
        user=user, component=rerated, body="changed my mind"
    )

    # Assert
    assert post.label == "Noise"


def test_an_earlier_row_is_found_by_its_natural_key(rerated: Component, user) -> None:
    # Arrange
    first = Classification.history.filter(
        component=rerated, reviewer__user=user
    ).earliest("pk")

    # Act
    found = Classification.history.get_by_natural_key(*first.natural_key())

    # Assert
    assert found == first


def test_a_fixture_round_trip_keeps_every_row(rerated: Component, tmp_path) -> None:
    # Arrange
    fixture = _dump(tmp_path / "history.json")
    n_rows = Classification.history.count()
    Classification.history.all().delete()

    # Act
    call_command("loaddata", str(fixture), verbosity=0)

    # Assert
    assert Classification.history.count() == n_rows


def test_a_fixture_round_trip_keeps_which_row_is_current(
    rerated: Component, user, tmp_path
) -> None:
    # Arrange: the rows come back with new ids, in the order they were dumped
    fixture = _dump(tmp_path / "history.json")
    Classification.history.all().delete()

    # Act
    call_command("loaddata", str(fixture), verbosity=0)

    # Assert
    assert (
        Classification.objects.get(component=rerated, reviewer__user=user).label
        == "Noise"
    )


def test_loading_the_same_fixture_twice_adds_nothing(
    rerated: Component, tmp_path
) -> None:
    # Arrange
    fixture = _dump(tmp_path / "history.json")
    n_rows = Classification.history.count()

    # Act
    call_command("loaddata", str(fixture), verbosity=0)

    # Assert
    assert Classification.history.count() == n_rows
