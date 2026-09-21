"""The discussion tab in a real browser: the swaps only htmx can prove."""

import pytest
from playwright.sync_api import expect

from melrater.core.models import DiscussionPost, Run

pytestmark = pytest.mark.e2e


@pytest.fixture
def rated_page(logged_in_page, live_server, ingested_run: Run):
    """`logged_in_page` with IC 1 rated, so its thread is open.

    Rated through the ratebar rather than the ORM: that is how a reviewer
    actually reaches the discussion, and it exercises the gate from the same
    side they do.
    """
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    logged_in_page.click("button.rate-noise")
    logged_in_page.wait_for_selector("button.rate-noise.selected")
    return logged_in_page


def test_the_tab_opens_the_thread(rated_page, ingested_run: Run) -> None:
    # Act
    rated_page.click("#nav-tab-other")
    rated_page.wait_for_url("**/ic/1/discussion/")

    # Assert: the composer is what the open thread looks like
    expect(rated_page.locator("#post-body")).to_be_visible()


def test_d_moves_between_the_two_tabs(rated_page, ingested_run: Run) -> None:
    # Act
    rated_page.keyboard.press("d")
    rated_page.wait_for_url("**/ic/1/discussion/")
    rated_page.keyboard.press("d")
    rated_page.wait_for_url(f"**/runs/{ingested_run.pk}/ic/1/")

    # Assert
    assert rated_page.url.endswith(f"/runs/{ingested_run.pk}/ic/1/")


def test_posting_shows_the_post_and_clears_the_composer(
    rated_page, live_server, ingested_run: Run
) -> None:
    # Arrange
    rated_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/discussion/")

    # Act: one #discussion swap has to do both
    rated_page.fill("#post-body", "quokka-on-a-bicycle")
    rated_page.click("button:has-text('post')")
    rated_page.wait_for_selector(".post-body")

    # Assert
    assert (
        rated_page.inner_text(".post-body"),
        rated_page.input_value("#post-body"),
    ) == ("quokka-on-a-bicycle", "")


def test_deleting_your_own_post_removes_its_row(
    rated_page, live_server, ingested_run: Run
) -> None:
    # Arrange
    rated_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/discussion/")
    rated_page.fill("#post-body", "quokka-on-a-bicycle")
    rated_page.click("button:has-text('post')")
    rated_page.wait_for_selector(".post-body")

    # Act: hx-confirm raises a native dialog, which playwright dismisses by
    # default -- accept it, then the empty 200 swaps the row away
    rated_page.on("dialog", lambda dialog: dialog.accept())
    rated_page.click("summary:has-text('edit')")
    rated_page.click("button.post-delete")
    rated_page.wait_for_selector(".post-body", state="detached")

    # Assert
    assert not DiscussionPost.objects.exists()


def test_typing_in_the_composer_does_not_rate(
    rated_page, live_server, ingested_run: Run
) -> None:
    # Arrange: c focuses the box on this page too
    rated_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/discussion/")

    # Act: s and n would otherwise be rating hotkeys
    rated_page.keyboard.press("c")
    rated_page.keyboard.type("sn")

    # Assert
    assert rated_page.input_value("#post-body") == "sn"


def test_saving_an_edit_replaces_that_row_alone(
    rated_page, live_server, ingested_run: Run
) -> None:
    # Arrange: the save button's hx-include is "closest details", which only a
    # browser actually resolves
    rated_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/discussion/")
    rated_page.fill("#post-body", "first thought")
    rated_page.click("button:has-text('post')")
    rated_page.wait_for_selector(".post-body")

    # Act
    rated_page.click("summary:has-text('edit')")
    rated_page.fill(".post-edit textarea", "second thought")
    rated_page.click("button:has-text('save')")
    rated_page.wait_for_selector(".post-body:has-text('second thought')")

    # Assert
    assert DiscussionPost.objects.get().body == "second thought"
