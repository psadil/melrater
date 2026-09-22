import re

import pytest
from django.core.management import call_command

from melrater.core import selectors, services
from melrater.core.models import Classification, DiscussionPost, Run

pytestmark = pytest.mark.django_db


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def test_run_list_redirects_anonymous_to_login(client, ingested_run: Run) -> None:
    # Act
    response = client.get("/")

    # Assert
    assert response.url.startswith("/accounts/login/")


def test_run_list_shows_run_label(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get("/")

    # Assert
    assert ingested_run.label in response.content.decode()


def test_run_list_shows_progress(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get("/")

    # Assert
    assert "0 / 3 rated by you" in response.content.decode()


def test_run_list_shows_fix_reviewer(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get("/")

    # Assert
    assert "TestModel @ thr5" in response.content.decode()


def test_component_detail_shows_position(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert
    assert "IC 1 / 3" in response.content.decode()


def test_component_detail_embeds_montage(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert
    assert (
        f"ic001_func_raw_axial.{ingested_run.montage_format}"
        in response.content.decode()
    )


def test_component_detail_shows_fix_probability(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert
    assert "P(signal) = 0.9" in response.content.decode()


def test_component_detail_offers_axis_switcher(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert
    assert "data-axis-btn" in response.content.decode()


def test_component_detail_404s_out_of_range(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/99/")

    # Assert
    assert response.status_code == 404


def test_rate_post_records_classification(logged_in, ingested_run: Run, user) -> None:
    # Act
    logged_in.post(f"/runs/{ingested_run.pk}/ic/2/rate/", {"label": "Noise"})

    # Assert
    assert Classification.objects.filter(
        reviewer__user=user, component__index=2, label="Noise"
    ).exists()


def test_rate_post_returns_selected_button(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.post(f"/runs/{ingested_run.pk}/ic/2/rate/", {"label": "Noise"})

    # Assert
    assert "rate-noise selected" in response.content.decode()


def test_rate_post_refreshes_verdict_card_out_of_band(
    logged_in, ingested_run: Run
) -> None:
    # Act
    response = logged_in.post(f"/runs/{ingested_run.pk}/ic/2/rate/", {"label": "Noise"})

    # Assert
    assert 'hx-swap-oob="true"' in response.content.decode()


def test_rate_post_updates_progress(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.post(f"/runs/{ingested_run.pk}/ic/2/rate/", {"label": "Noise"})

    # Assert
    assert "1 / 3 rated" in response.content.decode()


def test_rate_post_rejects_bad_label(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.post(f"/runs/{ingested_run.pk}/ic/2/rate/", {"label": "Bogus"})

    # Assert
    assert response.status_code == 400


# --- notes -------------------------------------------------------------


def _component(run: Run, index: int = 2):
    return run.components.get(index=index)


#: A sentinel rather than plausible prose. The page's own help text discusses
#: sagittal sinus, edge rings and every other look-alike, so a realistic note
#: would match copy that was on the page already and the leak assertion below
#: would pass without proving anything.
OTHER_NOTE = "quokka-on-a-bicycle"


def test_rate_post_saves_a_note_sent_with_the_rating(
    logged_in, ingested_run: Run, user
) -> None:
    # Act
    logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/rate/", {"label": "Noise", "note": "edge ring"}
    )

    # Assert
    assert Classification.objects.get(reviewer__user=user, component__index=2).note == (
        "edge ring"
    )


def test_rate_post_without_a_note_keeps_the_existing_one(
    logged_in, ingested_run: Run, user
) -> None:
    # Arrange: pins .get("note") over .get("note", "") in the view
    services.rate_component(
        user=user, component=_component(ingested_run), label="Noise", note="edge ring"
    )

    # Act
    logged_in.post(f"/runs/{ingested_run.pk}/ic/2/rate/", {"label": "Signal"})

    # Assert
    assert Classification.objects.get(reviewer__user=user, component__index=2).note == (
        "edge ring"
    )


def test_component_page_never_shows_another_reviewers_note(
    logged_in, ingested_run: Run, user, other_rater
) -> None:
    # Arrange: both have rated, which used to be what revealed a colleague's
    # note. A note is private now, so there is no condition under which it
    # appears -- and the gate is in the selector, so it never reaches the HTML.
    component = _component(ingested_run)
    services.rate_component(
        user=other_rater, component=component, label="Noise", note=OTHER_NOTE
    )
    services.rate_component(user=user, component=component, label="Signal")

    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/")

    # Assert
    assert OTHER_NOTE not in response.content.decode()


def test_rate_post_never_reveals_another_reviewers_note(
    logged_in, ingested_run: Run, other_rater
) -> None:
    # Arrange: rate_response.html swaps the note card out of band, so this is
    # the one response in which a rating and a note card are rendered together
    services.rate_component(
        user=other_rater,
        component=_component(ingested_run),
        label="Noise",
        note=OTHER_NOTE,
    )

    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/rate/", {"label": "Signal"}
    )

    # Assert
    assert OTHER_NOTE not in response.content.decode()


def test_rate_post_refreshes_the_note_card_out_of_band(
    logged_in, ingested_run: Run
) -> None:
    # Act
    response = logged_in.post(f"/runs/{ingested_run.pk}/ic/2/rate/", {"label": "Noise"})

    # Assert
    assert 'id="note-card"' in response.content.decode()


def test_component_page_wires_the_note_into_the_rating_post(
    logged_in, ingested_run: Run
) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/")

    # Assert: without this the note would race auto-advance's navigation
    assert 'hx-include="#note-text"' in response.content.decode()


def test_component_page_warns_a_note_is_not_stored_until_you_rate(
    logged_in, ingested_run: Run
) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/")

    # Assert
    assert "not saved until you rate" in response.content.decode()


def test_note_post_stores_the_note(logged_in, ingested_run: Run, user) -> None:
    # Arrange
    services.rate_component(
        user=user, component=_component(ingested_run), label="Noise"
    )

    # Act
    logged_in.post(f"/runs/{ingested_run.pk}/ic/2/note/", {"note": "edge ring"})

    # Assert
    assert Classification.objects.get(reviewer__user=user, component__index=2).note == (
        "edge ring"
    )


def test_note_post_does_not_change_the_label(
    logged_in, ingested_run: Run, user
) -> None:
    # Arrange
    services.rate_component(
        user=user, component=_component(ingested_run), label="Noise"
    )

    # Act
    logged_in.post(f"/runs/{ingested_run.pk}/ic/2/note/", {"note": "edge ring"})

    # Assert
    assert (
        Classification.objects.get(reviewer__user=user, component__index=2).label
        == "Noise"
    )


def test_note_post_without_a_rating_stores_nothing(
    logged_in, ingested_run: Run, user
) -> None:
    # Act
    logged_in.post(f"/runs/{ingested_run.pk}/ic/2/note/", {"note": "edge ring"})

    # Assert: no label invented on the reviewer's behalf. Scoped to this human
    # -- an ingested run already carries a FIX classification per component.
    assert not Classification.objects.filter(
        reviewer__user=user, component__index=2
    ).exists()


def test_note_post_without_a_rating_answers_200(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/note/", {"note": "edge ring"}
    )

    # Assert: htmx does not swap a 4xx body, so a refusal would say nothing
    assert response.status_code == 200


def test_note_post_without_a_rating_says_it_was_not_saved(
    logged_in, ingested_run: Run
) -> None:
    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/note/", {"note": "edge ring"}
    )

    # Assert
    assert "not saved until you rate" in response.content.decode()


def test_note_post_confirms_a_note_that_landed(
    logged_in, ingested_run: Run, user
) -> None:
    # Arrange
    services.rate_component(
        user=user, component=_component(ingested_run), label="Noise"
    )

    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/note/", {"note": "edge ring"}
    )

    # Assert
    assert ">saved<" in response.content.decode()


def test_note_post_rejects_an_overlong_note(logged_in, ingested_run: Run, user) -> None:
    # Arrange
    services.rate_component(
        user=user, component=_component(ingested_run), label="Noise"
    )

    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/note/",
        {"note": "x" * (services.NOTE_MAX_LENGTH + 1)},
    )

    # Assert
    assert response.status_code == 400


def test_note_post_requires_login(client, ingested_run: Run) -> None:
    # Act
    response = client.post(f"/runs/{ingested_run.pk}/ic/2/note/", {"note": "hm"})

    # Assert
    assert response.url.startswith("/accounts/login/")


def test_default_axis_is_axial(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert
    assert 'class="axis-btn active" data-axis-btn="axial"' in response.content.decode()


def test_set_axis_stores_session_preference(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.post("/prefs/axis/", {"axis": "coronal"})

    # Assert
    assert (response.status_code, logged_in.session["montage_axis"]) == (204, "coronal")


def test_axis_preference_persists_across_components(
    logged_in, ingested_run: Run
) -> None:
    # Arrange: choose coronal while viewing one component
    logged_in.post("/prefs/axis/", {"axis": "coronal"})

    # Act: navigate to a different component
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/")

    # Assert
    assert (
        'class="axis-btn active" data-axis-btn="coronal"' in response.content.decode()
    )


def test_stale_session_axis_falls_back_to_axial(logged_in, ingested_run: Run) -> None:
    # Arrange: an invalid value left behind (e.g. by an older code version)
    session = logged_in.session
    session["montage_axis"] = "oblique"
    session.save()

    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert
    assert 'class="axis-btn active" data-axis-btn="axial"' in response.content.decode()


def test_set_axis_rejects_unknown_axis(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.post("/prefs/axis/", {"axis": "oblique"})

    # Assert
    assert response.status_code == 400


def test_set_axis_requires_login(client, ingested_run: Run) -> None:
    # Act
    response = client.post("/prefs/axis/", {"axis": "coronal"})

    # Assert
    assert response.url.startswith("/accounts/login/")


def _montage_url(run: Run) -> str:
    """The first montage's URL, digest and all."""
    return (
        f"/media/runs/{run.uuid}/{run.montage_digest}"
        f"/ic001_func_raw_axial.{run.montage_format}"
    )


def test_media_redirects_anonymous_to_login(client, ingested_run: Run) -> None:
    # Act
    response = client.get(_montage_url(ingested_run))

    # Assert
    assert response.url.startswith("/accounts/login/")


def test_media_serves_montage_when_logged_in(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get(_montage_url(ingested_run))

    # Assert: served by the Django view, so this works regardless of DEBUG
    assert response.status_code == 200


# --- security surface ---------------------------------------------------


def test_password_reset_is_not_routed(client) -> None:
    # Act: the view django.contrib.auth.urls used to mount here rendered the
    # admin's own template to anonymous visitors and then 500'd on submit
    response = client.get("/accounts/password_reset/")

    # Assert
    assert response.status_code == 404


def test_response_carries_a_content_security_policy(
    logged_in, ingested_run: Run
) -> None:
    # Act
    response = logged_in.get("/")

    # Assert
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_montage_response_is_cacheable_forever(logged_in, ingested_run: Run) -> None:
    # Arrange: the digest in the path makes the bytes at a URL immutable

    # Act
    response = logged_in.get(_montage_url(ingested_run))

    # Assert
    assert "immutable" in response.headers["Cache-Control"]


# --- run list at scale --------------------------------------------------


def _query_count(client) -> int:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        client.get("/")
    return len(captured)


def test_run_list_query_count_does_not_grow_with_runs(logged_in, bare_runs) -> None:
    # Arrange
    bare_runs(2)
    few = _query_count(logged_in)

    # Act
    bare_runs(8)
    many = _query_count(logged_in)

    # Assert
    assert many == few


def test_run_list_filters_by_entity(logged_in, bare_runs) -> None:
    # Arrange
    bare_runs(3)

    # Act
    response = logged_in.get("/", {"q": "001"})

    # Assert
    assert response.context["result"].n_matching == 1


def test_run_list_paginates(logged_in, bare_runs) -> None:
    # Arrange
    bare_runs(selectors.RUNS_PER_PAGE + 1)

    # Act
    response = logged_in.get("/")

    # Assert
    assert len(response.context["result"].rows) == selectors.RUNS_PER_PAGE


def test_run_list_hides_complete_runs_on_request(logged_in, bare_runs, user) -> None:
    # Arrange: rate every component of the only run
    runs = bare_runs(1)
    for component in runs[0].components.all():
        services.rate_component(user=user, component=component, label="Signal")

    # Act
    response = logged_in.get("/", {"hide_complete": "1"})

    # Assert
    assert response.context["result"].rows == []


def test_run_list_offers_a_resume_link(logged_in, bare_runs) -> None:
    # Arrange
    bare_runs(2)

    # Act
    response = logged_in.get("/")

    # Assert
    assert response.context["result"].resume.next_unrated == 1


# --- montage loading ----------------------------------------------------


def test_component_page_gives_only_the_active_axis_a_src(
    logged_in, ingested_run: Run
) -> None:
    # Act
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()

    # Assert: five of the six montages (two smoothing levels, three axes) wait
    # behind data-src until switched to
    assert body.count('data-src="/media/') == 5


def test_component_page_prefetches_the_next_montage(
    logged_in, ingested_run: Run
) -> None:
    # Act
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()

    # Assert
    assert 'rel="prefetch" as="image" href="/media/runs/' in body


def test_component_page_leaks_no_template_syntax(logged_in, ingested_run: Run) -> None:
    # Assert: Django's {# ... #} is single-line only, so a multi-line note
    # renders as visible page text instead of disappearing
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()

    assert "{#" not in body and "{%" not in body


# --- metric frame -------------------------------------------------------


def test_component_page_compares_metrics_within_the_scan_without_a_cohort(
    logged_in, ingested_run: Run
) -> None:
    # Act: nothing has built a cohort, so a run can only place itself
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()

    # Assert
    assert "z within this scan" in body


@pytest.fixture
def cohort_page(logged_in, ingested_run: Run) -> str:
    """The rating page once a cohort has been built from this database."""
    call_command("refresh_metric_cohort")
    return logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()


def test_component_page_says_how_many_scans_its_metrics_are_read_against(
    cohort_page: str,
) -> None:
    # Assert: the count is the claim, so the page states it rather than
    # leaving "unusual" to mean whichever frame the reader assumes
    assert "z vs 1 scan<" in cohort_page


def test_component_page_rails_the_scans_own_range_over_the_cohort(
    cohort_page: str,
) -> None:
    # Assert: the within-scan comparison survives the change of frame, as the
    # thin rail the glyph draws above the cohort bands
    assert 'stroke="#8b93a1" stroke-width="1.6"' in cohort_page


def test_component_page_keeps_the_within_scan_score_in_the_row_tooltip(
    cohort_page: str,
) -> None:
    # Assert: the number beside the strip is the cohort's, so this one moves
    # rather than disappearing
    assert "within this scan: z=" in cohort_page


def test_component_page_reports_the_motion_fit(logged_in, ingested_run: Run) -> None:
    # Act
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()

    # Assert: the FD trace is the same for every component of a run; this is
    # the part of that panel that is about the one on screen
    assert "motion R&#178;" in body


def test_component_page_says_nothing_about_motion_without_a_fit(
    logged_in, ingested_run: Run
) -> None:
    # Arrange: a run ingested before the fit existed
    ingested_run.components.update(motion_r2=None)

    # Act
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()

    # Assert
    assert "motion R&#178;" not in body


# --- help bubbles -------------------------------------------------------


def test_component_page_explains_a_toolbar_chip(logged_in, ingested_run: Run) -> None:
    # Act
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()

    # Assert
    assert 'id="help-field-tr"' in body


def test_component_page_gives_every_help_panel_a_unique_id(
    logged_in, ingested_run: Run
) -> None:
    # Assert: popovers are addressed by id, so a repeat would open the wrong one
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()
    ids = re.findall(r'id="(help-[^"]+)"', body)

    assert len(ids) == len(set(ids))


def test_component_page_omits_help_for_an_undocumented_family(
    logged_in, ingested_run: Run
) -> None:
    # Assert: the fixture's features are invented names, so the catalog has
    # nothing for them and the family bubbles render as nothing at all
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()

    assert "help-metric-" not in body


def test_rating_response_carries_the_verdict_help(logged_in, ingested_run: Run) -> None:
    # Act: the out-of-band swap replaces the whole verdict card
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/1/rate/", {"label": "Signal"}
    )

    # Assert
    assert 'id="help-field-fix-verdict"' in response.content.decode()


# --- montage background -------------------------------------------------


def test_default_background_is_the_functional(logged_in, anat_ingested_run) -> None:
    # Act
    response = logged_in.get(f"/runs/{anat_ingested_run.pk}/ic/1/")

    # Assert: the IC map's own space is what a reviewer lands on
    assert 'class="bg-btn active" data-bg-btn="func"' in response.content.decode()


def test_set_background_stores_the_session_preference(logged_in) -> None:
    # Act
    logged_in.post("/prefs/background/", {"background": "anat"})

    # Assert
    assert logged_in.session["montage_background"] == "anat"


def test_set_background_rejects_an_unknown_background(logged_in) -> None:
    # Act
    response = logged_in.post("/prefs/background/", {"background": "mni"})

    # Assert
    assert response.status_code == 400


def test_set_background_requires_login(client) -> None:
    # Act
    response = client.post("/prefs/background/", {"background": "anat"})

    # Assert
    assert response.status_code == 302


def test_background_preference_falls_back_for_a_run_without_one(
    logged_in, ingested_run: Run
) -> None:
    # Arrange: a reviewer who chose the anatomical on some other run
    session = logged_in.session
    session["montage_background"] = "anat"
    session.save()

    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert: the functional montage is the one that gets a src, not six
    # broken images
    assert (
        f'src="/media/runs/{ingested_run.uuid}/{ingested_run.montage_digest}'
        f'/ic001_func_raw_axial.{ingested_run.montage_format}"'
        in response.content.decode()
    )


def test_component_page_offers_no_background_switch_without_one(
    logged_in, ingested_run: Run
) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert: one background is not a choice
    assert "data-bg-btn" not in response.content.decode()


def test_component_page_gives_only_the_active_montage_a_src(
    logged_in, anat_ingested_run
) -> None:
    # Act
    response = logged_in.get(f"/runs/{anat_ingested_run.pk}/ic/1/")

    # Assert: twelve montages on the page, eleven of them deferred
    assert response.content.decode().count('data-src="/media/') == 11


def test_prefetch_follows_the_chosen_background(logged_in, anat_ingested_run) -> None:
    # Arrange
    session = logged_in.session
    session["montage_background"] = "anat"
    session.save()

    # Act
    response = logged_in.get(f"/runs/{anat_ingested_run.pk}/ic/1/")

    # Assert: warming the wrong background's image would be worse than not
    # warming one at all
    assert (
        f'rel="prefetch" as="image" href="/media/runs/{anat_ingested_run.uuid}'
        f"/{anat_ingested_run.montage_digest}"
        f'/ic002_anat_raw_axial.{anat_ingested_run.montage_format}"'
        in response.content.decode()
    )


# --- montage smoothing --------------------------------------------------


def test_default_smoothing_is_the_unsmoothed_map(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert: the map as MELODIC wrote it is what a reviewer lands on
    assert 'class="sm-btn active" data-sm-btn="raw"' in response.content.decode()


def test_set_smoothing_stores_the_session_preference(logged_in) -> None:
    # Act
    logged_in.post("/prefs/smoothing/", {"smoothing": "smooth"})

    # Assert
    assert logged_in.session["montage_smoothing"] == "smooth"


def test_set_smoothing_rejects_an_unknown_level(logged_in) -> None:
    # Act
    response = logged_in.post("/prefs/smoothing/", {"smoothing": "blur"})

    # Assert
    assert response.status_code == 400


def test_set_smoothing_requires_login(client) -> None:
    # Act
    response = client.post("/prefs/smoothing/", {"smoothing": "smooth"})

    # Assert
    assert response.status_code == 302


def test_smoothing_preference_falls_back_for_a_run_without_one(
    logged_in, ingested_run: Run
) -> None:
    # Arrange: a run rendered before the smoothed variant, and a reviewer who
    # chose it on some other run
    ingested_run.montage_smoothings = ["raw"]
    ingested_run.save(update_fields=["montage_smoothings"])
    session = logged_in.session
    session["montage_smoothing"] = "smooth"
    session.save()

    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert: the unsmoothed montage gets the src, not a broken image
    assert (
        f'src="/media/runs/{ingested_run.uuid}/{ingested_run.montage_digest}'
        f'/ic001_func_raw_axial.{ingested_run.montage_format}"'
        in response.content.decode()
    )


def test_component_page_offers_no_smoothing_switch_without_one(
    logged_in, ingested_run: Run
) -> None:
    # Arrange
    ingested_run.montage_smoothings = ["raw"]
    ingested_run.save(update_fields=["montage_smoothings"])

    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert: one level is not a choice
    assert "data-sm-btn" not in response.content.decode()


def test_prefetch_follows_the_chosen_smoothing(logged_in, ingested_run: Run) -> None:
    # Arrange
    session = logged_in.session
    session["montage_smoothing"] = "smooth"
    session.save()

    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert
    assert (
        f'rel="prefetch" as="image" href="/media/runs/{ingested_run.uuid}'
        f"/{ingested_run.montage_digest}"
        f'/ic002_func_smooth_axial.{ingested_run.montage_format}"'
        in response.content.decode()
    )


# --- slice labels -------------------------------------------------------


def test_component_page_labels_the_first_axial_slice(
    logged_in, ingested_run: Run
) -> None:
    # Act
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()

    # Assert: the montage is pixels only, so the label is the page's, placed
    # at the top-left of the first cell
    first = ingested_run.montage_picks["axial"][0]
    assert f'<span class="slice-label" style="left: 0%; top: 0%">{first}</span>' in body


def test_component_page_marks_the_sagittal_edges(logged_in, ingested_run: Run) -> None:
    # Act
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()

    # Assert
    assert '<span class="edge-label edge-left">P</span>' in body


def test_component_page_survives_a_run_without_slice_picks(
    logged_in, ingested_run: Run
) -> None:
    # Arrange: a run rendered before the picks were recorded
    ingested_run.montage_picks = {}
    ingested_run.save(update_fields=["montage_picks"])

    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert: no labels, rather than wrong ones or an error
    assert (response.status_code, "data-axis-labels" in response.content.decode()) == (
        200,
        False,
    )


def test_component_page_offers_the_decision_guide(logged_in, ingested_run: Run) -> None:
    # Act
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/1/").content.decode()

    # Assert: beside the rating buttons
    assert 'id="help-field-rating"' in body


def test_rating_response_carries_the_decision_guide(
    logged_in, ingested_run: Run
) -> None:
    # Act: the swap replaces the whole rate bar, guide included
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/1/rate/", {"label": "Signal"}
    )

    # Assert
    assert 'id="help-field-rating"' in response.content.decode()


# --- discussion ---------------------------------------------------------

#: A sentinel, for the reason OTHER_NOTE is one.
OTHER_POST = "narwhal-in-a-hammock"


def _discussion_card(body: str) -> str:
    """Just the `#discussion` card, so the CSRF token does not confuse a diff."""
    return body[body.index('id="discussion"') :]


def _colleague_posts(run: Run, other_rater, index: int = 2) -> None:
    component = _component(run, index)
    services.rate_component(user=other_rater, component=component, label="Noise")
    services.create_discussion_post(
        user=other_rater, component=component, body=OTHER_POST
    )


def test_discussion_page_is_identical_whether_or_not_anybody_has_posted(
    logged_in, ingested_run: Run, other_rater
) -> None:
    # Arrange: IC 2 has a thread, IC 3 has none, and this viewer has rated
    # neither. This is the gate, and a substring assertion is not: a count in
    # the unrated branch would keep every other test on this page green.
    _colleague_posts(ingested_run, other_rater)

    # Act
    with_posts = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/discussion/")
    without = logged_in.get(f"/runs/{ingested_run.pk}/ic/3/discussion/")

    # Assert
    assert _discussion_card(with_posts.content.decode()) == _discussion_card(
        without.content.decode()
    )


def test_discussion_page_hides_a_post_before_you_rate(
    logged_in, ingested_run: Run, other_rater
) -> None:
    # Arrange
    _colleague_posts(ingested_run, other_rater)

    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/discussion/")

    # Assert
    assert OTHER_POST not in response.content.decode()


def test_discussion_page_hides_that_anyone_posted_before_you_rate(
    logged_in, ingested_run: Run, other_rater
) -> None:
    # Arrange: half of what the delay protects is not learning that somebody
    # else had something to say about this one
    _colleague_posts(ingested_run, other_rater)

    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/discussion/")

    # Assert
    assert "colleague" not in response.content.decode()


def test_discussion_page_offers_no_composer_before_you_rate(
    logged_in, ingested_run: Run
) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/discussion/")

    # Assert
    assert 'id="post-body"' not in response.content.decode()


def test_discussion_page_answers_200_before_you_rate(
    logged_in, ingested_run: Run
) -> None:
    # Assert: a refusal status would itself be a signal, and there is nothing
    # to refuse -- the page is simply empty of everything but the explanation
    assert logged_in.get(f"/runs/{ingested_run.pk}/ic/2/discussion/").status_code == 200


def test_discussion_page_shows_a_post_once_you_have_rated(
    logged_in, ingested_run: Run, user, other_rater
) -> None:
    # Arrange
    _colleague_posts(ingested_run, other_rater)
    services.rate_component(
        user=user, component=_component(ingested_run), label="Signal"
    )

    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/discussion/")

    # Assert
    assert OTHER_POST in response.content.decode()


def test_discussion_page_escapes_a_post_body(
    logged_in, ingested_run: Run, user
) -> None:
    # Arrange: a post is the one place a reviewer's text is shown to somebody
    # else, so autoescaping is the thing under test
    component = _component(ingested_run)
    services.rate_component(user=user, component=component, label="Signal")
    services.create_discussion_post(
        user=user, component=component, body="<script>alert(1)</script>"
    )

    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/discussion/")

    # Assert
    assert "&lt;script&gt;" in response.content.decode()


def test_discussion_page_leaks_no_template_syntax(logged_in, ingested_run: Run) -> None:
    # Assert: Django's {# ... #} is single-line only, so a multi-line note
    # renders as visible page text instead of disappearing
    body = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/discussion/").content.decode()

    assert "{#" not in body and "{%" not in body


def test_discussion_page_redirects_anonymous_to_login(
    client, ingested_run: Run
) -> None:
    # Act
    response = client.get(f"/runs/{ingested_run.pk}/ic/2/discussion/")

    # Assert
    assert response.status_code == 302 and "/accounts/login/" in response["Location"]


def test_component_page_offers_the_discussion_tab(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/")

    # Assert
    assert f"/runs/{ingested_run.pk}/ic/2/discussion/" in response.content.decode()


def test_the_tab_pair_is_identical_whether_or_not_anybody_has_posted(
    logged_in, ingested_run: Run, other_rater
) -> None:
    # Arrange: the tab is on the rating page too, where it must not become a
    # badge that reports on a component this viewer has not rated
    _colleague_posts(ingested_run, other_rater)

    # Act
    with_posts = logged_in.get(f"/runs/{ingested_run.pk}/ic/2/").content.decode()
    without = logged_in.get(f"/runs/{ingested_run.pk}/ic/3/").content.decode()

    # Assert: the same markup up to the component number each links to
    assert _tabs(with_posts).replace("/ic/2/", "/ic/N/") == _tabs(without).replace(
        "/ic/3/", "/ic/N/"
    )


def _tabs(body: str) -> str:
    start = body.index('<span class="page-tabs">')
    return body[start : body.index("</span>", body.index("</a>", start))]


def test_discussion_post_stores_the_post(logged_in, ingested_run: Run, user) -> None:
    # Arrange
    services.rate_component(
        user=user, component=_component(ingested_run), label="Signal"
    )

    # Act
    logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/discussion/post/", {"body": "edge ring"}
    )

    # Assert
    assert DiscussionPost.objects.get(reviewer__user=user).body == "edge ring"


def test_discussion_post_answers_with_the_refreshed_panel(
    logged_in, ingested_run: Run, user
) -> None:
    # Arrange
    services.rate_component(
        user=user, component=_component(ingested_run), label="Signal"
    )

    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/discussion/post/", {"body": "edge ring"}
    )

    # Assert: one swap clears the composer and shows the new post
    body = response.content.decode()
    assert 'id="discussion"' in body and "edge ring" in body


def test_discussion_post_before_rating_is_forbidden(
    logged_in, ingested_run: Run
) -> None:
    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/discussion/post/", {"body": "edge ring"}
    )

    # Assert: unreachable through the UI, which is why it may name the refusal
    assert response.status_code == 403


def test_discussion_post_before_rating_stores_nothing(
    logged_in, ingested_run: Run
) -> None:
    # Act
    logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/discussion/post/", {"body": "edge ring"}
    )

    # Assert
    assert not DiscussionPost.objects.exists()


def test_an_empty_post_comes_back_as_the_panel_with_an_error(
    logged_in, ingested_run: Run, user
) -> None:
    # Arrange: `required` on the textarea is satisfied by one space, so this
    # is reachable by typing -- and htmx does not swap a 4xx body, so a 400
    # here would lose the refusal silently
    services.rate_component(
        user=user, component=_component(ingested_run), label="Signal"
    )

    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/discussion/post/", {"body": "   "}
    )

    # Assert
    assert response.status_code == 200 and "post-error" in response.content.decode()


def test_discussion_edit_updates_the_body(logged_in, ingested_run: Run, user) -> None:
    # Arrange
    component = _component(ingested_run)
    services.rate_component(user=user, component=component, label="Signal")
    post = services.create_discussion_post(user=user, component=component, body="first")

    # Act
    logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/discussion/{post.uuid}/edit/",
        {"body": "second"},
    )

    # Assert
    assert DiscussionPost.objects.get(pk=post.pk).body == "second"


def test_discussion_edit_answers_with_a_whole_row(
    logged_in, ingested_run: Run, user
) -> None:
    # Arrange: rendered from a bare model the row would silently come back
    # with an empty verdict chip and no edit controls
    component = _component(ingested_run)
    services.rate_component(user=user, component=component, label="Signal")
    post = services.create_discussion_post(user=user, component=component, body="first")

    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/discussion/{post.uuid}/edit/",
        {"body": "second"},
    )

    # Assert
    body = response.content.decode()
    assert "verdict-signal" in body and "post-edit" in body


def test_discussion_edit_refuses_another_reviewers_post(
    logged_in, ingested_run: Run, user, other_rater
) -> None:
    # Arrange
    component = _component(ingested_run)
    services.rate_component(user=other_rater, component=component, label="Noise")
    theirs = services.create_discussion_post(
        user=other_rater, component=component, body=OTHER_POST
    )
    services.rate_component(user=user, component=component, label="Signal")

    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/discussion/{theirs.uuid}/edit/",
        {"body": "not any more"},
    )

    # Assert: 404, the same answer a uuid that does not exist gets, so naming
    # a post cannot confirm that it is there
    assert response.status_code == 404


def test_discussion_delete_removes_your_own_post(
    logged_in, ingested_run: Run, user
) -> None:
    # Arrange
    component = _component(ingested_run)
    services.rate_component(user=user, component=component, label="Signal")
    post = services.create_discussion_post(user=user, component=component, body="oops")

    # Act
    logged_in.post(f"/runs/{ingested_run.pk}/ic/2/discussion/{post.uuid}/delete/")

    # Assert
    assert not DiscussionPost.objects.exists()


def test_discussion_delete_answers_an_empty_200(
    logged_in, ingested_run: Run, user
) -> None:
    # Arrange
    component = _component(ingested_run)
    services.rate_component(user=user, component=component, label="Signal")
    post = services.create_discussion_post(user=user, component=component, body="oops")

    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/discussion/{post.uuid}/delete/"
    )

    # Assert: not a 204 -- htmx does not swap one, so the row would stay put
    assert (response.status_code, response.content) == (200, b"")


def test_discussion_delete_refuses_another_reviewers_post(
    logged_in, ingested_run: Run, user, other_rater
) -> None:
    # Arrange
    component = _component(ingested_run)
    services.rate_component(user=other_rater, component=component, label="Noise")
    theirs = services.create_discussion_post(
        user=other_rater, component=component, body=OTHER_POST
    )
    services.rate_component(user=user, component=component, label="Signal")

    # Act
    response = logged_in.post(
        f"/runs/{ingested_run.pk}/ic/2/discussion/{theirs.uuid}/delete/"
    )

    # Assert
    assert response.status_code == 404


def test_every_signed_in_page_forbids_a_shared_cache(
    logged_in, ingested_run: Run
) -> None:
    # Arrange: each of these renders one reviewer's own state, and Django sends
    # no Cache-Control unless a view says so
    paths = (
        "/",
        f"/runs/{ingested_run.pk}/ic/2/",
        f"/runs/{ingested_run.pk}/ic/2/discussion/",
    )

    # Assert: `private`, not no-store -- the threat is an intermediary, and the
    # reviewer's own browser is wanted as a cache, as it is for the montages
    assert [
        p for p in paths if "private" not in logged_in.get(p)["Cache-Control"]
    ] == []
