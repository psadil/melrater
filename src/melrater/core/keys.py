"""Keyboard shortcuts: the one table every consumer reads.

Three things read it, and none of them names a key on its own: melrater.js
dispatches on the `#keymap` data block that `{% key_sheet %}` renders from it,
every `<kbd>` hint on a page comes from `{% key_hint %}`, and the `?` sheet
lists it. A key a page advertises and a key it answers to cannot differ.

A chord is a physical key -- `KeyboardEvent.code`, optionally prefixed
`Shift+` -- so Caps Lock and the keyboard layout never change what a key does.
The one exception is a lone printable character such as `?`, matched on
`KeyboardEvent.key`, because which physical key prints it depends on the
layout. Code names are never one character long, so the two cannot collide.

Django-free, like help.py, and checked once at import: a chord bound twice on
one page is a startup error rather than a key that silently does one of two
things.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal, get_args

Page = Literal["rate", "discussion"]
Group = Literal["rate", "look", "move", "other"]

PAGES: tuple[Page, ...] = get_args(Page)
RATE: frozenset[Page] = frozenset({"rate"})
BOTH: frozenset[Page] = frozenset(PAGES)

# the sheet's sections, in the order it lists them
GROUP_TITLES: dict[Group, str] = {
    "rate": "rate · reveals FIX's verdict",
    "look": "look",
    "move": "move",
    "other": "other",
}


@dataclass(frozen=True)
class Key:
    """One chord and what a `<kbd>` shows for it."""

    chord: str
    label: str


@dataclass(frozen=True)
class Binding:
    """One action and every key that fires it; `keys[0]` is the one a hint shows."""

    action: str
    keys: tuple[Key, ...]
    does: str
    group: Group
    pages: frozenset[Page]


@dataclass(frozen=True)
class KeyGroup:
    title: str
    bindings: tuple[Binding, ...]


BINDINGS: tuple[Binding, ...] = (
    Binding(
        "rate-signal", (Key("Digit1", "1"), Key("KeyS", "s")), "Signal", "rate", RATE
    ),
    Binding(
        "rate-unknown", (Key("Digit2", "2"), Key("KeyU", "u")), "Unknown", "rate", RATE
    ),
    Binding(
        "rate-noise", (Key("Digit3", "3"), Key("KeyN", "n")), "Noise", "rate", RATE
    ),
    Binding("cycle-plane", (Key("KeyO", "o"),), "cycle the plane", "look", RATE),
    Binding(
        "cycle-background", (Key("KeyB", "b"),), "swap the background", "look", RATE
    ),
    Binding("cycle-smoothing", (Key("KeyM", "m"),), "swap the smoothing", "look", RATE),
    Binding("next", (Key("ArrowRight", "→"),), "next component", "move", BOTH),
    Binding("prev", (Key("ArrowLeft", "←"),), "previous component", "move", BOTH),
    Binding("other-tab", (Key("KeyD", "d"),), "rate ↔ discussion", "move", BOTH),
    Binding(
        "focus-text", (Key("KeyC", "c"),), "write in this tab's box", "other", BOTH
    ),
    Binding(
        "focus-jump", (Key("KeyG", "g"),), "go to a component by number", "other", RATE
    ),
    Binding("leave-text", (Key("Escape", "Esc"),), "leave a text box", "other", BOTH),
)


def check(bindings: tuple[Binding, ...]) -> None:
    """Refuse an action defined twice, or a chord bound twice on one page."""
    actions = Counter(b.action for b in bindings)
    if twice := sorted(a for a, n in actions.items() if n > 1):
        raise ValueError(f"actions defined twice: {twice}")
    for where in PAGES:
        chords = Counter(k.chord for b in bindings if where in b.pages for k in b.keys)
        if twice := sorted(c for c, n in chords.items() if n > 1):
            raise ValueError(f"{where}: chords bound twice: {twice}")


check(BINDINGS)
_BY_ACTION = {b.action: b for b in BINDINGS}


def page(name: str) -> Page:
    """`name` as a Page, or ValueError: template tags take plain strings."""
    for known in PAGES:
        if known == name:
            return known
    raise ValueError(f"no such page: {name!r}")


def binding(action: str) -> Binding:
    """One binding by action; KeyError on a typo, so a template fails at render."""
    return _BY_ACTION[action]


def on_page(where: Page) -> tuple[Binding, ...]:
    return tuple(b for b in BINDINGS if where in b.pages)


def keymap(where: Page) -> dict[str, str]:
    """Chord -> action for one page: what melrater.js dispatches on."""
    return {k.chord: b.action for b in on_page(where) for k in b.keys}


def groups(where: Page) -> tuple[KeyGroup, ...]:
    """One page's bindings in the sheet's sections, empty sections dropped."""
    bound = on_page(where)
    sections = (
        KeyGroup(title, tuple(b for b in bound if b.group == group))
        for group, title in GROUP_TITLES.items()
    )
    return tuple(s for s in sections if s.bindings)
