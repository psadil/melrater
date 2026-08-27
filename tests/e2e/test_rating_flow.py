import pytest

from melrater.core.models import Classification, Run

pytestmark = pytest.mark.e2e


def test_login_lands_on_run_list(logged_in_page) -> None:
    assert logged_in_page.locator("h1").inner_text() == "Runs"


def test_keyboard_shortcut_records_rating(
    logged_in_page, live_server, ingested_run: Run, user
) -> None:
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")

    # Act: htmx posts the rating, then swaps the selected button back in
    logged_in_page.keyboard.press("3")
    logged_in_page.wait_for_selector("button.rate-noise.selected")

    # Assert
    assert Classification.objects.filter(
        reviewer__user=user, component__index=1, label="Noise"
    ).exists()


def test_axis_button_switches_montage(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")

    # Act
    logged_in_page.click('[data-axis-btn="coronal"]')

    # Assert
    assert logged_in_page.locator('img[data-axis-img="coronal"]').is_visible()


def test_arrow_key_navigates_to_next_component(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")

    # Act
    logged_in_page.keyboard.press("ArrowRight")
    logged_in_page.wait_for_url("**/ic/2/")

    # Assert
    assert logged_in_page.url.endswith("/ic/2/")


def test_axis_choice_persists_across_navigation(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange: choose coronal on one component (wait for the preference POST)
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    with logged_in_page.expect_response("**/prefs/axis/"):
        logged_in_page.click('[data-axis-btn="coronal"]')

    # Act: move to the next component
    logged_in_page.keyboard.press("ArrowRight")
    logged_in_page.wait_for_url("**/ic/2/")

    # Assert
    assert logged_in_page.locator('img[data-axis-img="coronal"]').is_visible()
