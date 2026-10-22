"""The keyboard layout in a real browser: what each key does, and what it doesn't.

A key that must do nothing is proven with a sentinel: press it, then a key with
a visible effect, and wait for that effect. Keydown handlers run in order and
playwright reports requests in the order they were sent, so once the sentinel
has landed the key under test has been handled, and any POST it sent has
already been recorded.
"""

from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, expect

from melrater.core.models import Classification, Run

pytestmark = pytest.mark.e2e


def record_posts(page: Page) -> list[str]:
    """Every POST `page` sends from now on, by path."""
    sent: list[str] = []
    page.on(
        "request",
        lambda request: (
            sent.append(urlparse(request.url).path)
            if request.method == "POST"
            else None
        ),
    )
    return sent


@pytest.fixture
def ic1(logged_in_page, live_server, ingested_run: Run) -> Page:
    """The rating page of IC 1."""
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    return logged_in_page


@pytest.fixture
def anat_ic1(logged_in_anat_page, live_server, anat_ingested_run) -> Page:
    """The rating page of IC 1 on a run with both backgrounds."""
    logged_in_anat_page.goto(f"{live_server.url}/runs/{anat_ingested_run.pk}/ic/1/")
    return logged_in_anat_page


# --- rating ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "label"),
    [("Digit1", "Signal"), ("Digit2", "Unknown"), ("Digit3", "Noise")],
)
def test_a_digit_rates(ic1: Page, user, key: str, label: str) -> None:
    # Act
    ic1.keyboard.press(key)
    ic1.wait_for_selector(f"button.rate-{label.lower()}.selected")

    # Assert
    assert (
        Classification.objects.get(reviewer__user=user, component__index=1).label
        == label
    )


def test_shift_and_a_digit_does_not_rate(ic1: Page) -> None:
    """Only the three bare digits rate: on AZERTY, Shift is how you get one."""
    # Arrange
    sent = record_posts(ic1)

    # Act: then a sentinel rating
    ic1.keyboard.press("Shift+Digit1")
    ic1.keyboard.press("Digit3")
    ic1.wait_for_selector("button.rate-noise.selected")

    # Assert: the sentinel's POST and nothing else
    assert len(sent) == 1


@pytest.mark.parametrize("key", ["KeyN", "KeyU", "KeyO", "KeyB", "KeyM"])
def test_a_retired_key_does_nothing(anat_ic1: Page, key: str) -> None:
    """The old layout's letters are unbound, not aliased.

    On the anatomical run, where b and m used to have something to swap.
    """
    # Arrange
    sent = record_posts(anat_ic1)

    # Act
    anat_ic1.keyboard.press(key)
    anat_ic1.keyboard.press("Digit3")
    anat_ic1.wait_for_selector("button.rate-noise.selected")

    # Assert
    assert len(sent) == 1


def test_d_no_longer_switches_tabs(ic1: Page) -> None:
    # Act: D is the background key now, and this run has only one background
    ic1.keyboard.press("KeyD")
    ic1.keyboard.press("Digit3")
    ic1.wait_for_selector("button.rate-noise.selected")

    # Assert
    assert ic1.url.endswith("/ic/1/")


def test_holding_a_rating_key_rates_once(ic1: Page) -> None:
    # Arrange: the first press has landed, and htmx has wired up the buttons
    # it swapped in -- that waits for the swap to settle, and until then a
    # click on the new buttons does nothing at all
    sent = record_posts(ic1)
    ic1.keyboard.down("Digit3")
    ic1.wait_for_selector("#ratebar:not(.htmx-added) button.rate-noise.selected")

    # Act: the auto-repeats while it stays down, then a sentinel
    ic1.keyboard.down("Digit3")
    ic1.keyboard.down("Digit3")
    ic1.keyboard.up("Digit3")
    ic1.keyboard.press("Digit1")
    ic1.wait_for_selector("button.rate-signal.selected")

    # Assert: the first press and the sentinel, nothing between
    assert len(sent) == 2


# --- moving ---------------------------------------------------------------


def test_space_moves_to_the_next_component(ic1: Page) -> None:
    # Act
    ic1.keyboard.press("Space")
    ic1.wait_for_url("**/ic/2/")

    # Assert
    assert ic1.url.endswith("/ic/2/")


def test_shift_space_moves_to_the_previous_component(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/2/")

    # Act
    logged_in_page.keyboard.press("Shift+Space")
    logged_in_page.wait_for_url("**/ic/1/")

    # Assert
    assert logged_in_page.url.endswith("/ic/1/")


def test_holding_space_moves_one_component(ic1: Page) -> None:
    # Arrange: the first press has landed on IC 2
    ic1.keyboard.down("Space")
    ic1.wait_for_url("**/ic/2/")

    # Act: the auto-repeats reach the new page, then a sentinel
    ic1.keyboard.down("Space")
    ic1.keyboard.down("Space")
    ic1.keyboard.up("Space")
    ic1.keyboard.press("KeyS")
    expect(ic1.locator('[data-axis-labels="coronal"]')).to_be_visible()

    # Assert
    assert ic1.url.endswith("/ic/2/")


def test_space_on_a_focused_montage_button_moves_on_without_pressing_it(
    ic1: Page,
) -> None:
    """Clicking a button leaves it focused, and Space presses a focused button."""
    # Arrange: focus, not click, so arranging sends no POST of its own
    sent = record_posts(ic1)
    ic1.focus('[data-axis-btn="coronal"]')

    # Act
    ic1.keyboard.press("Space")
    ic1.wait_for_url("**/ic/2/")

    # Assert: no /prefs/axis/ from the button
    assert sent == []


def test_a_key_pressed_while_leaving_does_not_rate_the_component_left(
    ic1: Page,
) -> None:
    """Until the next page commits, the one being left still gets keys."""
    # Arrange: hold the next page in flight
    held = []
    ic1.route("**/ic/2/", lambda route: held.append(route))
    with ic1.expect_request("**/ic/2/"):
        ic1.keyboard.press("Space")
    sent = record_posts(ic1)

    # Act: rate in the gap, then let the next page through
    ic1.keyboard.press("Digit1")
    held[0].continue_()
    ic1.wait_for_url("**/ic/2/")

    # Assert
    assert sent == []


# --- looking --------------------------------------------------------------


def test_s_cycles_the_plane_forward(ic1: Page) -> None:
    # Act
    ic1.keyboard.press("KeyS")

    # Assert: axial, then coronal
    expect(ic1.locator('[data-axis-labels="coronal"]')).to_be_visible()


def test_shift_s_cycles_the_plane_backwards(ic1: Page) -> None:
    # Act
    ic1.keyboard.press("Shift+KeyS")

    # Assert: axial, then back round to sagittal
    expect(ic1.locator('[data-axis-labels="sagittal"]')).to_be_visible()


def test_caps_lock_does_not_change_what_a_letter_does(ic1: Page) -> None:
    """Caps Lock sends key "S" without Shift, exactly as pressing "S" here does."""
    # Act
    ic1.keyboard.press("S")

    # Assert
    expect(ic1.locator('[data-axis-labels="coronal"]')).to_be_visible()


def test_shift_d_still_swaps_the_background(anat_ic1: Page) -> None:
    # Act: two backgrounds, so backwards is the same swap
    anat_ic1.keyboard.press("Shift+KeyD")

    # Assert
    expect(
        anat_ic1.locator(
            'img[data-bg-img="anat"][data-sm-img="raw"][data-axis-img="axial"]'
        )
    ).to_be_visible()


# --- text boxes -----------------------------------------------------------


def test_escape_out_of_the_go_to_box_gives_the_keys_back(ic1: Page, user) -> None:
    # Act
    ic1.keyboard.press("KeyG")
    ic1.keyboard.press("Escape")
    ic1.keyboard.press("Digit3")
    ic1.wait_for_selector("button.rate-noise.selected")

    # Assert
    assert Classification.objects.filter(reviewer__user=user).exists()


def test_the_go_to_box_takes_digits_rather_than_ratings(ic1: Page, user) -> None:
    # Act
    ic1.keyboard.press("KeyG")
    ic1.keyboard.type("2")
    ic1.keyboard.press("Enter")
    ic1.wait_for_url("**/ic/2/")

    # Assert
    assert not Classification.objects.filter(reviewer__user=user).exists()


def test_holding_g_types_nothing_into_the_go_to_box(ic1: Page) -> None:
    # Act: the first press focuses the box, the repeats then arrive inside it
    ic1.keyboard.down("KeyG")
    ic1.keyboard.down("KeyG")
    ic1.keyboard.up("KeyG")

    # Assert
    assert ic1.input_value("#jump") == ""
