"""The reveal gate: whose notes `notes_for_component` hands a given viewer.

Separate from `test_selectors.py`, which is deliberately database-free.
"""

import pytest

from melrater.core import selectors, services
from melrater.core.models import Classification, Component, Reviewer

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


def test_notes_for_component_hides_other_notes_until_you_rate(
    component, user, other_rater
) -> None:
    # Arrange: a colleague has written one, this viewer has not rated
    services.rate_component(
        user=other_rater, component=component, label="Noise", note="sagittal sinus"
    )

    # Act
    panel = selectors.notes_for_component(component, user)

    # Assert
    assert panel.others == []


def test_notes_for_component_reveals_other_notes_once_you_rate(
    component, user, other_rater
) -> None:
    # Arrange
    services.rate_component(
        user=other_rater, component=component, label="Noise", note="sagittal sinus"
    )
    services.rate_component(user=user, component=component, label="Signal")

    # Act
    panel = selectors.notes_for_component(component, user)

    # Assert
    assert [n.note for n in panel.others] == ["sagittal sinus"]


def test_notes_for_component_attributes_the_other_reviewers_label(
    component, user, other_rater
) -> None:
    # Arrange
    services.rate_component(
        user=other_rater, component=component, label="Noise", note="sagittal sinus"
    )
    services.rate_component(user=user, component=component, label="Signal")

    # Act
    panel = selectors.notes_for_component(component, user)

    # Assert: the note is shown with the decision it explains
    assert (panel.others[0].reviewer, panel.others[0].label) == ("colleague", "Noise")


def test_notes_for_component_excludes_your_own_note(
    component, user, other_rater
) -> None:
    # Arrange: both have written, so `others` must hold exactly one
    services.rate_component(
        user=other_rater, component=component, label="Noise", note="theirs"
    )
    services.rate_component(user=user, component=component, label="Signal", note="mine")

    # Act
    panel = selectors.notes_for_component(component, user)

    # Assert
    assert [n.note for n in panel.others] == ["theirs"]


def test_notes_for_component_skips_a_reviewer_who_left_no_note(
    component, user, other_rater
) -> None:
    # Arrange: a rating without a note is not a note
    services.rate_component(user=other_rater, component=component, label="Noise")
    services.rate_component(user=user, component=component, label="Signal")

    # Act
    panel = selectors.notes_for_component(component, user)

    # Assert
    assert panel.others == []


def test_notes_for_component_excludes_a_fix_reviewers_note(component, user) -> None:
    # Arrange: FIX never writes one, so the kind filter is what is under test
    fix = Reviewer.objects.create(
        kind=Reviewer.Kind.FIX, name="UKBiobank @ thr1", fix_model="UKBiobank"
    )
    Classification.objects.create(
        component=component, reviewer=fix, label="Noise", note="not a human note"
    )
    services.rate_component(user=user, component=component, label="Signal")

    # Act
    panel = selectors.notes_for_component(component, user)

    # Assert
    assert panel.others == []
