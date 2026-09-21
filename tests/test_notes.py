"""A note is its author's alone: what `notes_for_component` hands a viewer.

Separate from `test_selectors.py`, which is deliberately database-free.
"""

import pytest

from melrater.core import selectors, services
from melrater.core.models import Component

pytestmark = pytest.mark.django_db


@pytest.fixture
def component(bare_runs) -> Component:
    return Component.objects.get(run=bare_runs(1)[0], index=1)


def test_notes_for_component_returns_your_own_note(component, user) -> None:
    # Arrange
    services.rate_component(
        user=user, component=component, label="Noise", note="edge ring"
    )

    # Act
    panel = selectors.notes_for_component(component, user)

    # Assert
    assert panel.mine == "edge ring"


def test_notes_for_component_reports_you_have_not_rated(component, user) -> None:
    # Act
    panel = selectors.notes_for_component(component, user)

    # Assert
    assert panel.rated is False


def test_notes_for_component_never_returns_another_reviewers_note(
    component, user, other_rater
) -> None:
    # Arrange: both have rated and both have written, which used to be the
    # condition under which a colleague's note was revealed
    services.rate_component(
        user=other_rater, component=component, label="Noise", note="theirs"
    )
    services.rate_component(user=user, component=component, label="Signal", note="mine")

    # Act
    panel = selectors.notes_for_component(component, user)

    # Assert: dataclass equality, so the panel has nowhere left to put theirs
    assert panel == selectors.NotePanel(mine="mine", rated=True)
