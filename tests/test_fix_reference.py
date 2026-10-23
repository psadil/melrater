"""The FIX reveal: what `fix_reference_for_component` hands a given viewer.

The rule is that a reviewer learns nothing of FIX's call on a component until
they have rated it themselves -- not its label, not its probability, not
whether the run has a classifier at all -- and that the P(signal) strip only
ever carries components they have rated. Separate from `test_selectors.py`,
which is deliberately database-free.
"""

import math

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from melrater.core import fixmodel, selectors, services
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


# --- FIX's reasons ------------------------------------------------------

#: FIX's reasons for component 1: ten features, so two fall below the cut.
PUSHES = (0.9, -2.0, 0.1, 0.5, -0.05, 1.5, -0.3, 0.2, 0.7, -0.6)
REASONS = {f"feat{i}": push for i, push in enumerate(PUSHES)}
#: Chosen so that the reasons add up to component 1's P(signal) of 0.9.
BASELINE = math.log(0.9 / 0.1) - math.fsum(PUSHES)


@pytest.fixture
def explained(component) -> Component:
    """Component 1, its FIX row now carrying the reasons for its call."""
    Classification.objects.filter(
        component=component, reviewer__kind=Reviewer.Kind.FIX
    ).update(baseline=BASELINE, contributions=REASONS)
    return component


def test_fix_reference_hides_the_reasons_until_you_rate(explained, user) -> None:
    # Act
    reference = _reference(explained, user)

    # Assert: they add up to FIX's call, so they are its call in another form
    assert reference.reasons is None


def test_fix_reference_reveals_the_reasons_once_you_rate(explained, user) -> None:
    # Arrange
    services.rate_component(user=user, component=explained, label="Noise")

    # Act
    reference = _reference(explained, user)

    # Assert
    assert reference.reasons is not None


@pytest.fixture
def revealed(explained, user) -> selectors.FixReasons:
    """The reasons as a viewer who has rated sees them (the test above pins that)."""
    services.rate_component(user=user, component=explained, label="Noise")
    reasons = _reference(explained, user).reasons
    assert reasons is not None
    return reasons


def test_fix_reference_lists_the_largest_pushes_first(revealed) -> None:
    # either direction, by size, and only REASONS_SHOWN of them
    assert [row.name for row in revealed.rows] == [
        "feat1",
        "feat5",
        "feat0",
        "feat8",
        "feat9",
        "feat3",
        "feat6",
        "feat7",
    ]


def test_fix_reference_sums_the_reasons_it_does_not_list(revealed) -> None:
    # feat2 (+0.1) and feat4 (-0.05)
    assert revealed.rest_push == pytest.approx(0.05)


def test_fix_reference_counts_the_reasons_it_does_not_list(revealed) -> None:
    assert revealed.n_rest == 2


def test_fix_reference_reasons_add_up_to_fixs_call(revealed) -> None:
    assert fixmodel.logistic(revealed.logit) == pytest.approx(0.9)


def test_fix_reference_names_the_model_the_cohort_bands_are_kept_under(
    revealed,
) -> None:
    assert revealed.model == "TestModel"


def test_fix_reference_gives_no_reasons_for_a_row_stored_without_them(
    runs, user
) -> None:
    # Arrange: component 2's FIX row predates reasons
    second = Component.objects.get(run=runs[0], index=2)
    services.rate_component(user=user, component=second, label="Noise")

    # Act
    reference = _reference(second, user)

    # Assert
    assert reference.reasons is None
