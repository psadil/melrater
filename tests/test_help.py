import re

import pytest

from melrater.core import help as help_catalog


@pytest.fixture
def raw() -> dict:
    """A minimal catalog in the shape help.toml is parsed into."""
    return {
        "refs": {
            "pyfix": {"text": "pyFIX feature.py", "url": "https://example.invalid"}
        },
        "legacy": {"widget:0": "old.name", "widget:1": "other.old.name"},
        "chart": {},
        "field": {},
        "metric": {
            "widget": {
                "title": "widgets",
                "body": "what a widget is",
                "columns": ["the first", "the second"],
            }
        },
    }


# --- the shipped catalog --------------------------------------------------


def test_catalog_documents_every_pyfix_family() -> None:
    families = {help_catalog.family_key(name) for name in help_catalog.CATALOG.legacy}

    assert families <= set(help_catalog.CATALOG.entries)


def test_every_metric_entry_links_to_its_pyfix_source() -> None:
    metrics = [
        e for k, e in help_catalog.CATALOG.entries.items() if k.startswith("metric.")
    ]

    assert all(any("::" in link.text for link in e.links) for e in metrics)


def test_the_rating_guide_cites_griffanti() -> None:
    # Assert: the decision procedure is theirs, and the link says so
    links = help_catalog.CATALOG.entries["field.rating"].links

    assert any("10.1016/j.neuroimage.2016.12.036" in link.url for link in links)


def test_slugs_are_valid_html_ids() -> None:
    slugs = [e.slug for e in help_catalog.CATALOG.entries.values()]

    assert all(re.fullmatch(r"[a-z0-9-]+", slug) for slug in slugs)


# --- tooltips -------------------------------------------------------------


def test_tooltip_names_the_column_it_describes() -> None:
    tooltip = help_catalog.metric_tooltip("masktscorrandoverlap:1")

    assert tooltip == (
        "masktscorrandoverlap:1 · gm.ts.coef\n"
        "regression weight on the mean grey-matter timecourse"
    )


def test_tooltip_falls_back_to_the_family_body_without_columns() -> None:
    # smoothest has a single column and so describes itself, not its columns
    assert help_catalog.metric_tooltip("smoothest:0").endswith(
        help_catalog.CATALOG.entries["metric.smoothest"].body
    )


def test_tooltip_degrades_to_the_bare_name_for_an_unknown_feature() -> None:
    # feature names come from a run's features.csv, so the catalog can lag data
    assert help_catalog.metric_tooltip("featA") == "featA"


def test_entry_returns_none_for_an_unknown_key() -> None:
    assert help_catalog.entry("chart.nonesuch") is None


# --- load-time validation -------------------------------------------------


def test_build_accepts_a_well_formed_catalog(raw: dict) -> None:
    assert help_catalog.build(raw).entries["metric.widget"].columns == (
        "the first",
        "the second",
    )


def test_build_rejects_a_wrong_column_count(raw: dict) -> None:
    raw["metric"]["widget"]["columns"] = ["only one"]

    with pytest.raises(ValueError, match="pyFIX writes 2"):
        help_catalog.build(raw)


def test_build_rejects_an_unknown_ref(raw: dict) -> None:
    raw["metric"]["widget"]["refs"] = ["nosuchpaper"]

    with pytest.raises(ValueError, match="unknown ref"):
        help_catalog.build(raw)


def test_build_rejects_a_family_pyfix_does_not_produce(raw: dict) -> None:
    raw["metric"]["gadget"] = {"title": "gadgets", "body": "invented"}

    with pytest.raises(ValueError, match="no such pyFIX feature family"):
        help_catalog.build(raw)


def test_build_rejects_a_misspelled_field(raw: dict) -> None:
    raw["metric"]["widget"]["bullet"] = ["typo for bullets"]

    with pytest.raises(ValueError, match="bullet"):
        help_catalog.build(raw)
