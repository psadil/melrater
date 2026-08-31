"""`{% help_bubble "chart.spectrum" %}` — a `?` and the panel it opens.

A tag rather than context keys: the component page needs a dozen of these, and
threading a dozen entries through `views._component_context` would put catalog
lookups in a view whose job is assembling one component's data.
"""

from __future__ import annotations

from django import template

from melrater.core import help as help_catalog

register = template.Library()


@register.inclusion_tag("core/partials/help_bubble.html")
def help_bubble(key: str) -> dict[str, help_catalog.HelpEntry | None]:
    """Render help for `key`, or nothing at all when the catalog lacks it."""
    return {"entry": help_catalog.entry(key)}
