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
    expect(
        logged_in_page.locator('img[data-sm-img="raw"][data-axis-img="coronal"]')
    ).to_be_visible()


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
    expect(
        logged_in_page.locator('img[data-sm-img="raw"][data-axis-img="coronal"]')
    ).to_be_visible()


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

    # Assert: the other eleven variants wait behind data-src until switched to
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
        logged_in_anat_page.locator(
            'img[data-bg-img="anat"][data-sm-img="raw"][data-axis-img="axial"]'
        )
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
        logged_in_anat_page.locator(
            'img[data-bg-img="anat"][data-sm-img="raw"][data-axis-img="axial"]'
        )
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
        logged_in_anat_page.locator(
            'img[data-bg-img="anat"][data-sm-img="raw"][data-axis-img="coronal"]'
        )
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


def test_smoothing_hotkey_swaps_the_montage(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")

    # Act
    logged_in_page.keyboard.press("m")

    # Assert: expect() retries — the smoothed map has no src until first shown
    expect(
        logged_in_page.locator('img[data-sm-img="smooth"][data-axis-img="axial"]')
    ).to_be_visible()


def test_smoothing_choice_persists_across_navigation(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange: the hotkey goes through the button's click, so it fires htmx's
    # preference POST exactly as clicking does
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    with logged_in_page.expect_response("**/prefs/smoothing/"):
        logged_in_page.keyboard.press("m")

    # Act
    logged_in_page.keyboard.press("ArrowRight")
    logged_in_page.wait_for_url("**/ic/2/")

    # Assert
    expect(
        logged_in_page.locator('img[data-sm-img="smooth"][data-axis-img="axial"]')
    ).to_be_visible()


def test_smoothing_hotkey_keeps_the_chosen_axis_and_background(
    logged_in_anat_page, live_server, anat_ingested_run
) -> None:
    # Arrange: a non-default axis and background first
    logged_in_anat_page.goto(f"{live_server.url}/runs/{anat_ingested_run.pk}/ic/1/")
    with logged_in_anat_page.expect_response("**/prefs/axis/"):
        logged_in_anat_page.click('[data-axis-btn="coronal"]')
    with logged_in_anat_page.expect_response("**/prefs/background/"):
        logged_in_anat_page.keyboard.press("b")

    # Act: the three choices are independent
    logged_in_anat_page.keyboard.press("m")

    # Assert
    expect(
        logged_in_anat_page.locator(
            'img[data-bg-img="anat"][data-sm-img="smooth"][data-axis-img="coronal"]'
        )
    ).to_be_visible()


def test_axis_hotkey_cycles_the_plane(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")

    # Act: "change plane" is a first-class tip, so it has a key
    logged_in_page.keyboard.press("o")

    # Assert: the coronal frame, labels included, is what shows
    expect(logged_in_page.locator('[data-axis-labels="coronal"]')).to_be_visible()


def test_the_functional_montage_is_the_voxel_grid(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange: montages are encoded at voxel resolution and upscaled by the
    # browser; the synthetic volume is 6 voxels wide and the lightbox 5 wide
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    montage = logged_in_page.locator('img[data-axis-img="axial"].active')
    expect(montage).to_be_visible()

    # Act
    width = montage.evaluate("img => img.naturalWidth")

    # Assert
    assert width == 5 * 6


def test_the_frame_is_pinned_to_twice_the_voxel_grid(
    logged_in_page, live_server, ingested_run: Run
) -> None:
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    montage = logged_in_page.locator('img[data-axis-img="axial"].active')
    expect(montage).to_be_visible()

    # Act: on a wide window the frame is exactly 2x, so the nearest-neighbour
    # upscale lands on whole pixels
    width = logged_in_page.locator('[data-axis-frame="axial"]').bounding_box()["width"]

    # Assert
    assert width == 2 * 5 * 6


def test_a_note_typed_before_rating_rides_along_with_the_rating(
    logged_in_page, live_server, ingested_run: Run, user
) -> None:
    """The note reaches the database even when auto-advance navigates away.

    Deliberately on an unrated component: `fill` dispatches `change`, so the
    blur endpoint does fire, but with no rating yet it stores nothing by
    construction. The only path left is the note riding along with the rating
    POST -- which is the path that has to win the race against auto-advance.
    """
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    logged_in_page.check("[data-auto-advance]")
    logged_in_page.fill("#note-text", "quokka-on-a-bicycle")

    # Act
    logged_in_page.click("button.rate-noise")
    logged_in_page.wait_for_url("**/ic/2/")

    # Assert
    assert (
        Classification.objects.get(reviewer__user=user, component__index=1).note
        == "quokka-on-a-bicycle"
    )


def test_blurring_the_note_box_saves_it(
    logged_in_page, live_server, ingested_run: Run, user
) -> None:
    """The other save path: no rating click, just focus leaving the textarea."""
    # Arrange: a rating already exists, so the note has a row to land on
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")
    logged_in_page.click("button.rate-noise")
    logged_in_page.wait_for_selector("button.rate-noise.selected")

    # Act: fill dispatches change, which is what hx-trigger listens for
    logged_in_page.fill("#note-text", "quokka-on-a-bicycle")
    logged_in_page.click("#jump")
    logged_in_page.wait_for_selector("#note-status.note-saved")

    # Assert
    assert (
        Classification.objects.get(reviewer__user=user, component__index=1).note
        == "quokka-on-a-bicycle"
    )


def test_typing_in_the_note_box_does_not_rate(
    logged_in_page, live_server, ingested_run: Run, user
) -> None:
    """c focuses the box, and the rating hotkeys become ordinary letters in it."""
    # Arrange
    logged_in_page.goto(f"{live_server.url}/runs/{ingested_run.pk}/ic/1/")

    # Act: s and n would otherwise rate Signal and Noise
    logged_in_page.keyboard.press("c")
    logged_in_page.keyboard.type("sn")

    # Assert: the letters landed in the box and rated nothing
    assert (
        logged_in_page.input_value("#note-text"),
        Classification.objects.filter(reviewer__user=user).exists(),
    ) == ("sn", False)
