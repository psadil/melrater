import pytest
from playwright.sync_api import expect

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

    # Assert: expect() retries — this montage has no src until it is switched
    # to, and an img with height:auto has no box until it has loaded
    expect(logged_in_page.locator('img[data-axis-img="coronal"]')).to_be_visible()


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
    expect(logged_in_page.locator('img[data-axis-img="coronal"]')).to_be_visible()


def test_component_page_loads_one_montage(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange: record this component's montage requests. The page also
    # prefetches the *next* component's montage, which is deliberate.
    requested: list[str] = []
    logged_in_page.on(
        "request",
        lambda request: (
            requested.append(request.url) if "ic001_" in request.url else None
        ),
    )

    # Act
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    logged_in_page.wait_for_load_state("networkidle")

    # Assert: two of the three axes wait behind data-src until switched to
    assert len(requested) == 1


def test_auto_advance_moves_to_the_next_component(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    logged_in_page.check("[data-auto-advance]")

    # Act
    logged_in_page.keyboard.press("3")
    logged_in_page.wait_for_url("**/ic/2/")

    # Assert
    assert logged_in_page.url.endswith("/ic/2/")


# --- help bubbles -------------------------------------------------------


def test_help_button_opens_its_panel(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")

    # Act
    logged_in_page.click('[popovertarget="help-chart-metric-glyph"]')

    # Assert
    expect(logged_in_page.locator("#help-chart-metric-glyph")).to_be_visible()


def test_escape_dismisses_the_help_panel(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    logged_in_page.click('[popovertarget="help-chart-spectrum"]')

    # Act
    logged_in_page.keyboard.press("Escape")

    # Assert
    expect(logged_in_page.locator("#help-chart-spectrum")).not_to_be_visible()


def test_help_panel_escapes_the_scrolling_side_panel(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange: .side scrolls and body is overflow:hidden, so a panel that was
    # not in the top layer would come back clipped to less than its 380px
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")

    # Act
    logged_in_page.click('[popovertarget="help-field-outliers"]')
    box = logged_in_page.locator("#help-field-outliers").bounding_box()

    # Assert
    assert box["width"] == 380


def test_family_help_renders_for_a_documented_family(
    logged_in_page, live_server, documented_run: Run
) -> None:
    # Arrange: the family's `?` sits in the details body, not the summary,
    # so it appears only once the family is open
    logged_in_page.goto(f"{live_server.url}/runs/{documented_run.pk}/ic/1/")
    logged_in_page.click("summary:has-text('tsjump')")

    # Act
    logged_in_page.click('[popovertarget="help-metric-tsjump"]')

    # Assert
    expect(logged_in_page.locator("#help-metric-tsjump")).to_contain_text(
        "frame-to-frame jumps"
    )


def test_metric_row_title_carries_the_definition(
    logged_in_page, live_server, documented_run: Run
) -> None:
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{documented_run.pk}/ic/1/")

    # Assert: the legacy pyFIX label and the column's meaning, on two lines
    title = logged_in_page.locator(
        '.metric-name[title^="tsjump:0"]'
    ).first.get_attribute("title")

    assert title == (
        "tsjump:0 · ts.jump.max.abs.diff\nlargest absolute frame-to-frame change, "
        "in units of the timecourse's own standard deviation"
    )


def test_background_hotkey_swaps_the_montage(
    logged_in_anat_page, live_server, anat_ingested_run
) -> None:
    # Arrange
    logged_in_anat_page.goto(f"{live_server.url}/runs/{anat_ingested_run.pk}/ic/1/")

    # Act
    logged_in_anat_page.keyboard.press("b")

    # Assert: expect() retries — the anatomical has no src until first shown
    expect(
        logged_in_anat_page.locator('img[data-bg-img="anat"][data-axis-img="axial"]')
    ).to_be_visible()


def test_background_choice_persists_across_navigation(
    logged_in_anat_page, live_server, anat_ingested_run
) -> None:
    # Arrange: the hotkey goes through the button's click, so it fires htmx's
    # preference POST exactly as clicking does
    logged_in_anat_page.goto(f"{live_server.url}/runs/{anat_ingested_run.pk}/ic/1/")
    with logged_in_anat_page.expect_response("**/prefs/background/"):
        logged_in_anat_page.keyboard.press("b")

    # Act
    logged_in_anat_page.keyboard.press("ArrowRight")
    logged_in_anat_page.wait_for_url("**/ic/2/")

    # Assert
    expect(
        logged_in_anat_page.locator('img[data-bg-img="anat"][data-axis-img="axial"]')
    ).to_be_visible()


def test_background_hotkey_keeps_the_chosen_axis(
    logged_in_anat_page, live_server, anat_ingested_run
) -> None:
    # Arrange: pick a non-default axis first
    logged_in_anat_page.goto(f"{live_server.url}/runs/{anat_ingested_run.pk}/ic/1/")
    with logged_in_anat_page.expect_response("**/prefs/axis/"):
        logged_in_anat_page.click('[data-axis-btn="coronal"]')

    # Act: swapping the background must not throw the axis away
    logged_in_anat_page.keyboard.press("b")

    # Assert
    expect(
        logged_in_anat_page.locator('img[data-bg-img="anat"][data-axis-img="coronal"]')
    ).to_be_visible()


def test_the_browser_decodes_the_montage_codec(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange: montages are AVIF with 4:4:4 chroma (AV1 profile 1), chosen
    # because 4:2:0 smears the per-voxel overlay colour. A browser that could
    # not decode that would show a broken image and nothing else would fail.
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    montage = logged_in_page.locator('img[data-axis-img="axial"].active')

    # Act
    expect(montage).to_be_visible()

    # Assert: naturalWidth is 0 for an image the browser could not decode
    assert montage.evaluate("img => img.naturalWidth") > 0
