import pytest

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


def test_media_redirects_anonymous_to_login(client, ingested_run: Run) -> None:
    # Act
    response = client.get(
        f"/media/runs/{ingested_run.pk}/ic001_axial.{ingested_run.montage_format}"
    )

    # Assert
    assert response.url.startswith("/accounts/login/")


def test_media_serves_montage_when_logged_in(logged_in, ingested_run: Run) -> None:
    # Act
    response = logged_in.get(
        f"/media/runs/{ingested_run.pk}/ic001_axial.{ingested_run.montage_format}"
    )

    # Assert: served by the Django view, so this works regardless of DEBUG
    assert response.status_code == 200
