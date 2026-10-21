"""The FIX reveal: what `fix_reference_for_component` hands a given viewer.

The rule is that a reviewer learns nothing of FIX's call on a component until
they have rated it themselves -- not its label, not its probability, not
whether the run has a classifier at all -- and that the P(signal) strip only
ever carries components they have rated. Separate from `test_selectors.py`,
which is deliberately database-free.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from melrater.core import selectors, services
from melrater.core.models import Classification, Component, Reviewer, Run

pytestmark = pytest.mark.django_db

#: FIX's (label, P(signal)) for components 1-3 of the classified run.
FIX_CALLS = (("Signal", 0.9), ("Noise", 0.01), ("Noise", 0.002))


@pytest.fixture
def runs(bare_runs) -> list[Run]:
    """Two three-component runs, of which only the first has a FIX classifier."""
    made = bare_runs(2)
    fix = Reviewer.objects.create(
        kind=Reviewer.Kind.FIX,
        name="TestModel @ thr5",
        fix_model="TestModel",
        fix_threshold=5,
    )
    Classification.objects.bulk_create(
        Classification(component=c, reviewer=fix, label=label, probability=p)
        for c, (label, p) in zip(
            made[0].components.order_by("index"), FIX_CALLS, strict=True
        )
    )
    return made


@pytest.fixture
def component(runs) -> Component:
    return Component.objects.get(run=runs[0], index=1)


def _reference(component: Component, user) -> selectors.FixReference:
    """The reference as the rating page asks for it."""
    return selectors.fix_reference_for_component(
        component, selectors.user_labels_for_run(component.run, user)
    )


def test_fix_reference_reports_you_have_not_rated(component, user) -> None:
    # Act
    reference = _reference(component, user)

    # Assert
    assert reference.rated is False


def test_fix_reference_hides_the_verdict_until_you_rate(component, user) -> None:
    # Act
    reference = _reference(component, user)

    # Assert
    assert reference.verdicts == []


def test_fix_reference_hides_the_strip_until_you_rate(component, user) -> None:
    # Act
    reference = _reference(component, user)

    # Assert
    assert reference.strip == []


def test_fix_reference_reads_no_fix_row_before_you_rate(component, user) -> None:
    # Arrange: the labels come from the page, which has read them already
    user_labels = selectors.user_labels_for_run(component.run, user)

    # Act
    with CaptureQueriesContext(connection) as captured:
        selectors.fix_reference_for_component(component, user_labels)

    # Assert: the gate returns before any query, so nothing can leak by accident
    assert len(captured) == 0


def test_fix_reference_is_identical_whether_or_not_the_run_has_fix(
    runs, component, user
) -> None:
    # Arrange: the same viewer, the same unrated state, on a run with a FIX
    # classifier and one without
    bare = Component.objects.get(run=runs[1], index=1)

    # Act
    with_fix = _reference(component, user)
    without = _reference(bare, user)

    # Assert: nothing distinguishes them, so nothing can be inferred
    assert with_fix == without


def test_a_colleagues_rating_does_not_reveal_it_to_you(
    component, user, other_rater
) -> None:
    # Arrange
    services.rate_component(user=other_rater, component=component, label="Noise")

    # Act
    reference = _reference(component, user)

    # Assert
    assert reference.rated is False


def test_fix_reference_reveals_the_verdict_once_you_rate(component, user) -> None:
    # Arrange
    services.rate_component(user=user, component=component, label="Noise")

    # Act
    reference = _reference(component, user)

    # Assert
    assert [v.label for v in reference.verdicts] == ["Signal"]


def test_fix_reference_strip_holds_only_components_you_have_rated(
    runs, component, user
) -> None:
    # Arrange: 1 and 3 rated, 2 not
    services.rate_component(user=user, component=component, label="Noise")
    third = Component.objects.get(run=runs[0], index=3)
    services.rate_component(user=user, component=third, label="Noise")

    # Act
    reference = _reference(component, user)

    # Assert
    assert [e.index for e in reference.strip] == [1, 3]


def test_fix_reference_strip_carries_your_label(component, user) -> None:
    # Arrange: a disagreement, which is what the strip exists to show
    services.rate_component(user=user, component=component, label="Noise")

    # Act
    reference = _reference(component, user)

    # Assert
    assert reference.strip[0].user_label == "Noise"
