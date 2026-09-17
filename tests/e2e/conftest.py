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


@pytest.fixture
def documented_run(ingested_run):
    """`ingested_run` with its invented feature names swapped for real ones.

    The shared fixture uses featA/featB/featC, which the help catalog has
    never heard of — deliberately, since that is the graceful-degradation
    case. These tests need the opposite: names whose families the catalog
    documents, so the per-family bubbles actually render.
    """
    real = {"featA": "tsjump:0", "featB": "motioncorrelation:0", "featC": "smoothest:0"}

    stats = ingested_run.metric_stats
    stats["names"] = [real[n] for n in stats["names"]]
    stats["stats"] = {real[n]: s for n, s in stats["stats"].items()}
    ingested_run.metric_stats = stats
    ingested_run.save(update_fields=["metric_stats"])

    for component in ingested_run.components.all():
        component.metrics = {real[n]: v for n, v in component.metrics.items()}
        component.save(update_fields=["metrics"])
    return ingested_run


@pytest.fixture
def logged_in_anat_page(page, live_server, anat_ingested_run, user):
    """`logged_in_page` for a run that has both montage backgrounds."""
    page.goto(f"{live_server.url}/accounts/login/")
    page.fill('input[name="username"]', "rater")
    page.fill('input[name="password"]', "pw")
    page.click('button[type="submit"]')
    page.wait_for_url(f"{live_server.url}/")
    return page
