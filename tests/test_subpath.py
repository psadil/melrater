"""With FORCE_SCRIPT_NAME set, every generated URL carries the prefix.

The deployment serves at ``https://<host>/melrater/`` behind a proxy that
forwards the prefixed path through UNstripped (Django strips it for routing —
under ASGI ``request.path`` comes straight from the scope, so a proxy-side
strip would leak into redirects and ``next=`` parameters). Unprefixed paths
have to keep resolving too: the container healthcheck dials
``/accounts/login/`` on 127.0.0.1 directly.
"""

import pytest
from django.urls import base

from melrater.core.models import Run

pytestmark = pytest.mark.django_db

PREFIX = "/melrater"


@pytest.fixture
def subpath(settings):
    """The deployed configuration: FORCE_SCRIPT_NAME plus the script prefix.

    Real handlers (WSGI/ASGI) set the thread-local script prefix from
    FORCE_SCRIPT_NAME per request; Django's test client does not, so the
    fixture sets it the way a handler would. Re-assigning STATIC_URL and
    MEDIA_URL — to the values settings.py already gives them — is what makes
    the override and its teardown fire setting_changed for the two: the
    staticfiles storage resolves its `base_url` once, at construction, so
    without this it would keep handing later tests a /melrater/ prefix.
    """
    settings.FORCE_SCRIPT_NAME = PREFIX
    base.set_script_prefix(PREFIX + "/")
    settings.STATIC_URL = "static/"
    settings.MEDIA_URL = "media/"
    yield
    base.clear_script_prefix()


def test_the_login_redirect_is_prefixed(subpath, client) -> None:
    # Act
    response = client.get("/")

    # Assert
    assert response.headers["Location"].startswith(f"{PREFIX}/accounts/login/")


def test_an_unprefixed_path_still_resolves(subpath, client) -> None:
    """The compose healthcheck reaches the container directly, prefixless."""
    # Act
    response = client.get("/accounts/login/")

    # Assert
    assert response.status_code == 200


def test_static_urls_are_prefixed(subpath, client, user) -> None:
    # Arrange
    client.force_login(user)

    # Act
    response = client.get("/")

    # Assert
    assert f'href="{PREFIX}/static/css/melrater.css"' in response.content.decode()


def test_montage_urls_are_prefixed(subpath, client, user, ingested_run: Run) -> None:
    # Arrange
    client.force_login(user)

    # Act
    response = client.get(f"/runs/{ingested_run.pk}/ic/1/")

    # Assert
    assert f'"{PREFIX}/media/runs/{ingested_run.uuid}/' in response.content.decode()
