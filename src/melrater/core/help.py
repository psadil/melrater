"""The help catalog: definitions for everything the rating screen shows.

`help.toml` is the single source, and this module is deliberately Django-free
so anything that can import it — a documentation build, a notebook — can render
the same text the popovers show. Nothing here knows about templates.

The catalog is parsed and cross-checked once, at import: an unknown reference
key or a `columns` array whose length disagrees with the family's column count
in `[legacy]` is a startup error rather than a wrong tooltip.
"""

from __future__ import annotations

import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from melrater.core.metrics import family_of

CATALOG_PATH = Path(__file__).with_name("help.toml")

# the sections of help.toml that hold entries, in the order a docs page would
# want them; everything else at the top level is lookup data
SECTIONS = ("chart", "field", "metric")


class Link(BaseModel):
    """One outbound citation, already resolved to text and href."""

    model_config = ConfigDict(frozen=True)

    text: str
    url: str


class _RawEntry(BaseModel):
    """One `[chart.*]`/`[field.*]`/`[metric.*]` table as written in the file.

    `extra="forbid"` so a misspelled field is caught at import instead of
    silently dropping its prose.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    body: str
    bullets: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()
    source: str | None = None
    columns: tuple[str, ...] | None = None


class HelpEntry(BaseModel):
    """One entry, with its references resolved and an id-safe slug."""

    model_config = ConfigDict(frozen=True)

    key: str
    slug: str
    title: str
    body: str
    bullets: tuple[str, ...]
    links: tuple[Link, ...]
    columns: tuple[str, ...] | None


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    entries: dict[str, HelpEntry]
    legacy: dict[str, str]


def _slug(key: str) -> str:
    return key.replace(".", "-").replace("_", "-")


def _links(key: str, raw: _RawEntry, refs: dict[str, Link]) -> tuple[Link, ...]:
    links = []
    for name in raw.refs:
        if name not in refs:
            raise ValueError(f"{key}: unknown ref {name!r}")
        links.append(refs[name])
    if raw.source is not None:
        pyfix = refs["pyfix"]
        links.append(Link(text=f"{pyfix.text} :: {raw.source}", url=pyfix.url))
    return tuple(links)


def build(raw: dict[str, Any]) -> Catalog:
    """Validate one parsed catalog. Separate from `load` so tests can feed it."""
    refs = {name: Link(**value) for name, value in raw["refs"].items()}
    legacy: dict[str, str] = raw["legacy"]
    # a family's column count is whatever [legacy] says it is — that table is
    # copied from pyfix, so it is the one part of the file that cannot drift
    widths = Counter(family_of(name) for name in legacy)

    entries: dict[str, HelpEntry] = {}
    for section in SECTIONS:
        for name, payload in raw[section].items():
            key = f"{section}.{name}"
            entry = _RawEntry(**payload)
            if section == "metric":
                if name not in widths:
                    raise ValueError(f"{key}: no such pyFIX feature family")
                if entry.columns is not None and len(entry.columns) != widths[name]:
                    raise ValueError(
                        f"{key}: {len(entry.columns)} columns described, "
                        f"but pyFIX writes {widths[name]}"
                    )
            entries[key] = HelpEntry(
                key=key,
                slug=_slug(key),
                title=entry.title,
                body=entry.body,
                bullets=entry.bullets,
                links=_links(key, entry, refs),
                columns=entry.columns,
            )
    return Catalog(entries=entries, legacy=legacy)


def load(path: Path = CATALOG_PATH) -> Catalog:
    return build(tomllib.loads(path.read_text(encoding="utf-8")))


CATALOG = load()


def entry(key: str) -> HelpEntry | None:
    """One entry by namespaced key, or None — callers render nothing for None.

    Never raises on an unknown key: metric names come from a run's features
    file, so the catalog can always be behind the data.
    """
    return CATALOG.entries.get(key)


def family_key(name: str) -> str:
    """The catalog key for the family a metric or family name belongs to."""
    return f"metric.{family_of(name)}"


def family(name: str) -> HelpEntry | None:
    """The entry for the family a metric name belongs to, if the catalog has one."""
    return entry(family_key(name))


def _column_text(name: str, fam: HelpEntry) -> str | None:
    _, sep, index = name.partition(":")
    if not sep or fam.columns is None or not index.isdigit():
        return None
    position = int(index)
    if position >= len(fam.columns):
        return None
    return fam.columns[position]


def metric_tooltip(name: str) -> str:
    """The `title=` text for one metric row: identity, then what it measures.

    Two lines — the pyFIX name beside the label the original R/MATLAB FIX used,
    then the description of that specific column (or of the family, when it has
    only one). Degrades to the bare name for a feature the catalog has never
    heard of.
    """
    legacy = CATALOG.legacy.get(name)
    head = f"{name} · {legacy}" if legacy else name
    fam = family(name)
    if fam is None:
        return head
    return f"{head}\n{_column_text(name, fam) or fam.body}"
