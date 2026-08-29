import pytest

from melrater.core import selectors, services
from melrater.core.models import Classification, Run

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
    assert f"ic001_axial.{ingested_run.montage_format}" in response.content.decode()


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
        f"/media/runs/{run.uuid}/{run.montage_digest}/ic001_axial.{run.montage_format}"
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

    # Assert: two of the three montages wait behind data-src until switched to
    assert body.count('data-src="/media/') == 2


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
