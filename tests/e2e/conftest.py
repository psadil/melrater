"""Playwright end-to-end fixtures (run with: pixi run -e dev test-e2e)."""

import os

import pytest

# playwright's sync API drives an event loop; without this Django's async
# guard would refuse ORM calls made from the test body alongside it
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture
def logged_in_page(page, live_server, ingested_run, user):
    """A browser page authenticated through the real login form."""
    page.goto(f"{live_server.url}/accounts/login/")
    page.fill('input[name="username"]', "rater")
    page.fill('input[name="password"]', "pw")
    page.click('button[type="submit"]')
    page.wait_for_url(f"{live_server.url}/")
    return page
