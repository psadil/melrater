"""The key map itself: the layout's rules, checked without a browser or a DB."""

import re
from pathlib import Path

import pytest

from melrater.core import help as help_catalog
from melrater.core import keys

MELRATER_JS = Path(__file__).parents[1] / "src" / "static" / "js" / "melrater.js"


def all_chords() -> set[str]:
    return {k.chord for b in keys.BINDINGS for k in b.keys}


# --- the check run at import ----------------------------------------------


def test_check_refuses_a_chord_bound_twice_on_one_page() -> None:
    # Arrange
    twice = (
        keys.Binding("a", (keys.Key("KeyA", "A"),), "a", "other", keys.RATE),
        keys.Binding("b", (keys.Key("KeyA", "A"),), "b", "other", keys.BOTH),
    )

    # Act / Assert
    with pytest.raises(ValueError, match="KeyA"):
        keys.check(twice)


def test_check_allows_one_chord_on_two_different_pages() -> None:
    # Arrange
    apart = (
        keys.Binding("a", (keys.Key("KeyA", "A"),), "a", "other", frozenset({"rate"})),
        keys.Binding(
            "b", (keys.Key("KeyA", "A"),), "b", "other", frozenset({"discussion"})
        ),
    )

    # Act / Assert: returns rather than raising
    assert keys.check(apart) is None


def test_check_refuses_an_action_defined_twice() -> None:
    # Arrange
    twice = (
        keys.Binding("a", (keys.Key("KeyA", "A"),), "a", "other", keys.RATE),
        keys.Binding("a", (keys.Key("KeyB", "B"),), "a", "other", keys.RATE),
    )

    # Act / Assert
    with pytest.raises(ValueError, match="actions defined twice"):
        keys.check(twice)


# --- the layout's rules ---------------------------------------------------


def test_only_the_three_digits_rate() -> None:
    # Act
    rating = {k.chord for b in keys.BINDINGS if b.group == "rate" for k in b.keys}

    # Assert
    assert rating == {"Digit1", "Digit2", "Digit3"}


def test_the_row_between_the_view_keys_and_the_digits_is_unbound() -> None:
    # Act: Q W E R, bare or shifted
    bound = {c.removeprefix("Shift+") for c in all_chords()}

    # Assert
    assert bound.isdisjoint({"KeyQ", "KeyW", "KeyE", "KeyR"})


def test_the_retired_letters_stay_unbound() -> None:
    """An old habit should do nothing, never something else."""
    # Act
    bound = {c.removeprefix("Shift+") for c in all_chords()}

    # Assert
    assert bound.isdisjoint({"KeyU", "KeyN", "KeyO", "KeyB", "KeyM"})


def test_every_chord_is_a_key_code_or_one_character() -> None:
    # Act
    odd = {
        c
        for c in all_chords()
        if len(c) != 1 and not re.fullmatch(r"(Shift\+)?[A-Z][A-Za-z0-9]+", c)
    }

    # Assert
    assert odd == set()


def test_every_action_has_a_handler_in_melrater_js() -> None:
    # Arrange
    source = MELRATER_JS.read_text(encoding="utf-8")

    # Act
    missing = {b.action for b in keys.BINDINGS if f'"{b.action}"' not in source}

    # Assert
    assert missing == set()


def test_help_prose_presses_only_keys_that_exist() -> None:
    """help.toml says "press S to ..." by hand; a remap must not leave it lying."""
    # Arrange
    labels = {k.label.lower() for b in keys.BINDINGS for k in b.keys}
    prose = help_catalog.CATALOG_PATH.read_text(encoding="utf-8")

    # Act
    pressed = {m.lower() for m in re.findall(r"\bpress (\S+?)[ ,;:)]", prose)}

    # Assert
    assert pressed <= labels


# --- lookups ----------------------------------------------------------------


def test_page_refuses_an_unknown_name() -> None:
    # Act / Assert
    with pytest.raises(ValueError, match="no such page"):
        keys.page("run-list")


def test_binding_refuses_an_unknown_action() -> None:
    # Act / Assert
    with pytest.raises(KeyError):
        keys.binding("rate-maybe")


def test_the_discussion_page_has_no_rating_or_view_keys() -> None:
    # Act
    titles = [g.title for g in keys.groups("discussion")]

    # Assert: the sections with nothing in them are dropped
    assert titles == [keys.GROUP_TITLES["move"], keys.GROUP_TITLES["other"]]


def test_keymap_maps_every_chord_of_the_page() -> None:
    # Act
    mapped = keys.keymap("rate")

    # Assert
    assert mapped["Shift+Space"] == "prev"
