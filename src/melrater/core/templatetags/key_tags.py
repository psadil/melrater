"""`{% key_hint "next" %}` and friends — keyboard hints read from keys.py.

Tags rather than context keys, for the same reason as help_tags: the hints sit
in partials that several views render, and keys.py is the only place a key is
named.
"""

from __future__ import annotations

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

from melrater.core import keys

register = template.Library()


@register.simple_tag
def key_hint(action: str, css: str = "") -> SafeString:
    """The `<kbd>` for `action`'s first key; `css` is its class, if any."""
    found = keys.binding(action)
    attrs = format_html(' class="{}"', css) if css else ""
    return format_html(
        '<kbd{} data-key-action="{}">{}</kbd>', attrs, found.action, found.keys[0].label
    )


@register.simple_tag
def key_label(action: str) -> str:
    """Every key for `action`, for a `title=`: "Space / →"."""
    return " / ".join(k.label for k in keys.binding(action).keys)


@register.inclusion_tag("core/partials/key_sheet.html")
def key_sheet(page: str) -> dict[str, object]:
    """One page's key map, as the data block melrater.js dispatches on."""
    where = keys.page(page)
    return {"groups": keys.groups(where), "keymap": keys.keymap(where)}
