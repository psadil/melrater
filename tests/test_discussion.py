"""The discussion gate: what `discussion_for_component` hands a given viewer.

The rule the thread rests on is that a reviewer who has not rated a component
learns nothing about its discussion -- not a post, not an author, not a count,
not that anybody has written at all -- so that the label they eventually give
is their own. Separate from `test_selectors.py`, which is deliberately
database-free.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from melrater.core import selectors, services
from melrater.core.models import Component, DiscussionPost

pytestmark = pytest.mark.django_db


@pytest.fixture
def component(bare_runs) -> Component:
    return Component.objects.get(run=bare_runs(1)[0], index=1)


@pytest.fixture
def posted_by_colleague(component, other_rater) -> DiscussionPost:
    """One post by somebody other than `user`, on `component`."""
    services.rate_component(user=other_rater, component=component, label="Noise")
    return services.create_discussion_post(
        user=other_rater, component=component, body="the ring follows the sinus"
    )


def test_discussion_reports_you_have_not_rated(component, user) -> None:
    # Act
    thread = selectors.discussion_for_component(component, user)

    # Assert
    assert thread.rated is False


def test_discussion_hides_every_post_until_you_rate(
    component, user, posted_by_colleague
) -> None:
    # Act
    thread = selectors.discussion_for_component(component, user)

    # Assert
    assert thread.posts == []


def test_discussion_is_identical_whether_or_not_anybody_has_posted(
    component, user, posted_by_colleague
) -> None:
    # Arrange: the same viewer, the same unrated state, on a component that
    # does have a thread and one that does not
    bare = Component.objects.get(run=component.run, index=2)

    # Act
    with_posts = selectors.discussion_for_component(component, user)
    without = selectors.discussion_for_component(bare, user)

    # Assert: nothing distinguishes them, so nothing can be inferred
    assert with_posts == without


def test_discussion_reveals_posts_once_you_rate(
    component, user, posted_by_colleague
) -> None:
    # Arrange
    services.rate_component(user=user, component=component, label="Signal")

    # Act
    thread = selectors.discussion_for_component(component, user)

    # Assert
    assert [p.body for p in thread.posts] == ["the ring follows the sinus"]


def test_discussion_orders_posts_oldest_first(component, user, other_rater) -> None:
    # Arrange
    services.rate_component(user=other_rater, component=component, label="Noise")
    services.rate_component(user=user, component=component, label="Signal")
    services.create_discussion_post(user=other_rater, component=component, body="first")
    services.create_discussion_post(user=user, component=component, body="second")

    # Act
    thread = selectors.discussion_for_component(component, user)

    # Assert
    assert [p.body for p in thread.posts] == ["first", "second"]


def test_discussion_marks_your_own_posts(component, user, posted_by_colleague) -> None:
    # Arrange
    services.rate_component(user=user, component=component, label="Signal")
    services.create_discussion_post(user=user, component=component, body="mine")

    # Act
    thread = selectors.discussion_for_component(component, user)

    # Assert
    assert [p.mine for p in thread.posts] == [False, True]


def test_discussion_keeps_the_label_the_author_had_when_they_wrote(
    component, user, other_rater, posted_by_colleague
) -> None:
    # Arrange: the colleague posted while calling it noise, then changed
    # their mind. The post argued for noise and must not be retitled.
    services.rate_component(user=other_rater, component=component, label="Signal")
    services.rate_component(user=user, component=component, label="Signal")

    # Act
    thread = selectors.discussion_for_component(component, user)

    # Assert
    assert thread.posts[0].label == "Noise"


def test_discussion_attributes_each_post_to_its_author(
    component, user, posted_by_colleague
) -> None:
    # Arrange
    services.rate_component(user=user, component=component, label="Signal")

    # Act
    thread = selectors.discussion_for_component(component, user)

    # Assert
    assert thread.posts[0].reviewer == "colleague"


def test_discussion_leaves_out_another_components_posts(
    component, user, posted_by_colleague
) -> None:
    # Arrange
    other = Component.objects.get(run=component.run, index=2)
    services.rate_component(user=user, component=other, label="Signal")

    # Act
    thread = selectors.discussion_for_component(other, user)

    # Assert
    assert thread.posts == []


def test_discussion_flags_an_edited_post(component, user) -> None:
    # Arrange
    services.rate_component(user=user, component=component, label="Signal")
    post = services.create_discussion_post(
        user=user, component=component, body="first thought"
    )
    services.edit_discussion_post(user=user, post=post, body="second thought")

    # Act
    thread = selectors.discussion_for_component(component, user)

    # Assert
    assert thread.posts[0].edited_at is not None


def test_discussion_query_count_does_not_grow_with_the_number_of_posts(
    component, user, other_rater
) -> None:
    # Arrange
    services.rate_component(user=other_rater, component=component, label="Noise")
    services.rate_component(user=user, component=component, label="Signal")
    services.create_discussion_post(user=other_rater, component=component, body="one")
    with CaptureQueriesContext(connection) as few:
        selectors.discussion_for_component(component, user)

    # Act
    for n in range(8):
        services.create_discussion_post(
            user=other_rater, component=component, body=f"more {n}"
        )
    with CaptureQueriesContext(connection) as many:
        selectors.discussion_for_component(component, user)

    # Assert
    assert len(many) == len(few)


def test_a_post_is_findable_by_its_natural_key(posted_by_colleague) -> None:
    # Act
    found = DiscussionPost.objects.get_by_natural_key(
        *posted_by_colleague.natural_key()
    )

    # Assert
    assert found == posted_by_colleague
