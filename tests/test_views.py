from pathlib import Path

import pytest

from melrater.core import services
from melrater.core.models import Classification, Run


@pytest.fixture
def ingested_run(melodic_dir: Path, media_root: Path) -> Run:
    return services.ingest_run(path=melodic_dir)


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user("rater", password="pw")


@pytest.mark.django_db
def test_run_list_requires_login(client, ingested_run: Run) -> None:
    # Act
    response = client.get("/")

    # Assert
    assert response.status_code == 302
    assert response.url.startswith("/accounts/login/")


@pytest.mark.django_db
def test_run_list_shows_progress(client, ingested_run: Run, user) -> None:
    # Arrange
    client.force_login(user)

    # Act
    response = client.get("/")

    # Assert
    html = response.content.decode()
    assert response.status_code == 200
    assert ingested_run.label in html
    assert "0 / 3 rated by you" in html
    assert "TestModel @ thr5" in html


@pytest.mark.django_db
def test_component_detail_renders_variant_c_panels(
    client, ingested_run: Run, user
) -> None:
    # Arrange
    client.force_login(user)

    # Act
    response = client.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert
    html = response.content.decode()
    assert response.status_code == 200
    assert "IC 1 / 3" in html
    assert f"ic001_axial.{ingested_run.montage_format}" in html
    assert "FIX verdict" in html
    assert "P(signal) = 0.9" in html
    assert "FIX metrics" in html
    assert "data-axis-btn" in html


@pytest.mark.django_db
def test_rate_post_records_classification(client, ingested_run: Run, user) -> None:
    # Arrange
    client.force_login(user)

    # Act
    response = client.post(f"/runs/{ingested_run.pk}/ic/2/rate/", {"label": "Noise"})

    # Assert: htmx fragment has the button selected + oob verdict card
    html = response.content.decode()
    assert response.status_code == 200
    assert "rate-noise selected" in html
    assert 'hx-swap-oob="true"' in html
    assert "1 / 3 rated" in html
    assert Classification.objects.filter(
        reviewer__user=user, component__index=2, label="Noise"
    ).exists()


@pytest.mark.django_db
def test_rate_post_rejects_bad_label(client, ingested_run: Run, user) -> None:
    # Arrange
    client.force_login(user)

    # Act
    response = client.post(f"/runs/{ingested_run.pk}/ic/2/rate/", {"label": "Bogus"})

    # Assert
    assert response.status_code == 400
    assert not Classification.objects.filter(reviewer__user=user).exists()
