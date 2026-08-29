from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseBase,
)
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods
from django.views.static import serve

from melrater.core import charts, selectors, services
from melrater.core.metrics import OUTLIER_Z, family_of
from melrater.core.models import Component, Run
from melrater.core.montage import AXES
from melrater.core.schemas import ComponentData, RunData

RATING_BUTTONS = [
    {"label": "Signal", "keys": ["1", "s"]},
    {"label": "Unknown", "keys": ["2", "u"]},
    {"label": "Noise", "keys": ["3", "n"]},
]

AXIS_SESSION_KEY = "montage_axis"
DEFAULT_AXIS = "axial"

# A montage URL contains the run's montage digest, so the bytes behind one
# never change — a re-render mints new URLs. That makes them safely immutable,
# which matters: a reviewer walking 96 components would otherwise revalidate
# every image.
MONTAGE_CACHE_CONTROL = "private, max-age=31536000, immutable"


def _authed_user(request: HttpRequest) -> AbstractBaseUser:
    user = request.user
    # login_required guarantees this; narrows away AnonymousUser for ty
    assert not isinstance(user, AnonymousUser)
    return user


@login_required
def run_list(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    hide_complete = request.GET.get("hide_complete") == "1"
    result = selectors.run_list_page(
        _authed_user(request),
        query=query,
        hide_complete=hide_complete,
        page_number=request.GET.get("page"),
    )
    # everything except `page`, so the pager keeps the current filter
    filter_qs = urlencode(
        {
            k: v
            for k, v in (("q", query), ("hide_complete", "1" if hide_complete else ""))
            if v
        }
    )
    return render(
        request,
        "core/run_list.html",
        {
            "result": result,
            "q": query,
            "hide_complete": hide_complete,
            "filter_qs": filter_qs,
        },
    )


@login_required
def media_file(request: HttpRequest, path: str) -> HttpResponseBase:
    """Serve one montage. Login-required because these are subject-derived.

    HttpResponseBase, not HttpResponse: `serve` answers with a FileResponse
    for a hit and an HttpResponseNotModified for a conditional request, and
    only their common base covers both.
    """
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response.headers["Cache-Control"] = MONTAGE_CACHE_CONTROL
    return response


@dataclass(frozen=True)
class MetricRow:
    name: str
    z: float
    abs_z: float
    raw_fmt: str
    z_fmt: str
    severity: str
    glyph: str


@dataclass(frozen=True)
class MetricFamily:
    name: str
    rows: list[MetricRow]
    max_z_fmt: str
    color: str


def _metric_panel(run_data: RunData, comp: ComponentData) -> dict:
    stats = run_data.metric_stats
    rows: list[MetricRow] = []
    for name in stats.names:
        s = stats.stats[name]
        z = comp.metrics[name].z
        rows.append(
            MetricRow(
                name=name,
                z=z,
                abs_z=abs(z),
                raw_fmt=f"{comp.metrics[name].raw:.3g}",
                z_fmt=f"{z:+.1f}",
                severity=charts.severity_color(z),
                glyph=charts.metric_glyph_svg(z, s.p5, s.p25, s.p75, s.p95, s.signal_z),
            )
        )
    outliers = sorted((r for r in rows if r.abs_z > OUTLIER_Z), key=lambda r: -r.abs_z)
    grouped: dict[str, list[MetricRow]] = {}
    for r in rows:
        grouped.setdefault(family_of(r.name), []).append(r)
    families = []
    for fam_name, members in sorted(grouped.items()):
        max_z = max(m.abs_z for m in members)
        families.append(
            MetricFamily(
                name=fam_name,
                rows=members,
                max_z_fmt=f"{max_z:.1f}",
                color=charts.severity_color(max_z),
            )
        )
    return {
        "metric_outliers": outliers,
        "metric_families": families,
        "n_outliers": len(outliers),
        "outlier_z": OUTLIER_Z,
    }


def _component_context(run: Run, component: Component, user: AbstractBaseUser) -> dict:
    # typed projections at the ORM boundary (see schemas.py)
    run_data = selectors.run_data(run)
    comp = selectors.component_data(component)
    index = comp.index
    indices = list(run.components.values_list("index", flat=True).order_by("index"))
    n_total = len(indices)
    user_labels = selectors.user_labels_for_run(run, user)
    prob_entries, threshold = selectors.prob_entries_for_run(run, user_labels)
    prob_strip = (
        charts.prob_strip_svg(
            prob_entries, index, threshold if threshold is not None else 0.01
        )
        if prob_entries
        else None
    )
    unrated_after = [i for i in indices if i not in user_labels and i != index]
    next_index = index + 1 if index < n_total else None
    context = {
        "run": run,
        "component": component,
        "n_total": n_total,
        "prev_index": index - 1 if index > 1 else None,
        "next_index": next_index,
        "next_unrated": next((i for i in unrated_after if i > index), None)
        or (unrated_after[0] if unrated_after else None),
        "montages": selectors.montage_urls(component),
        "fix_rows": selectors.fix_verdicts_for_component(component),
        "prob_strip": prob_strip,
        "user_label": user_labels.get(index),
        "n_rated": len(user_labels),
        "tc_fd_svg": charts.timecourse_fd_svg(
            comp.timecourse, run_data.fd, run_data.tr
        ),
        "spectrum_svg": charts.spectrum_svg(comp.spectrum, run_data.frequencies),
        "rating_buttons": RATING_BUTTONS,
        "expl_var_fmt": f"{comp.explained_var:.2f}",
        "total_var_fmt": f"{comp.total_var:.2f}",
        "tr_fmt": f"{run_data.tr:g}",
    }
    context.update(_metric_panel(run_data, comp))
    return context


def _active_axis(request: HttpRequest) -> str:
    axis = request.session.get(AXIS_SESSION_KEY, DEFAULT_AXIS)
    return axis if axis in AXES else DEFAULT_AXIS


@login_required
def component_detail(request: HttpRequest, run_id: int, index: int) -> HttpResponse:
    run = get_object_or_404(Run, pk=run_id)
    component = get_object_or_404(Component, run=run, index=index)
    context = _component_context(run, component, _authed_user(request))
    axis = _active_axis(request)
    context["active_axis"] = axis
    # Warm the next component's montage while this one is being judged. Only
    # the visible axis: the other two are not fetched for this component
    # either (the template gives them data-src, not src).
    next_index = context["next_index"]
    context["prefetch_url"] = (
        selectors.montage_url(run, next_index, axis) if next_index else None
    )
    return render(request, "core/component_detail.html", context)


@login_required
@require_http_methods(["POST"])
def set_axis(request: HttpRequest) -> HttpResponse:
    """Persist the montage-axis choice so it survives component navigation."""
    axis = request.POST.get("axis", "")
    if axis not in AXES:
        return HttpResponseBadRequest(
            f"invalid axis: {axis!r}", content_type="text/plain"
        )
    request.session[AXIS_SESSION_KEY] = axis
    return HttpResponse(status=204)


@login_required
@require_http_methods(["POST"])
def component_rate(request: HttpRequest, run_id: int, index: int) -> HttpResponse:
    user = _authed_user(request)
    run = get_object_or_404(Run, pk=run_id)
    component = get_object_or_404(Component, run=run, index=index)
    try:
        services.rate_component(
            user=user, component=component, label=request.POST.get("label", "")
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc), content_type="text/plain")
    context = _component_context(run, component, user)
    context["active_axis"] = _active_axis(request)
    return render(request, "core/partials/rate_response.html", context)
