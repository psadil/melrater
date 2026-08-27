from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from melrater.core import charts, selectors, services
from melrater.core.metrics import OUTLIER_Z, family_of
from melrater.core.models import Component, Run
from melrater.core.schemas import ComponentData, RunData

RATING_BUTTONS = [
    {"label": "Signal", "keys": ["1", "s"]},
    {"label": "Unknown", "keys": ["2", "u"]},
    {"label": "Noise", "keys": ["3", "n"]},
]


def _authed_user(request: HttpRequest) -> AbstractBaseUser:
    user = request.user
    # login_required guarantees this; narrows away AnonymousUser for ty
    assert not isinstance(user, AnonymousUser)
    return user


@login_required
def run_list(request: HttpRequest) -> HttpResponse:
    rows = selectors.runs_with_progress(_authed_user(request))
    return render(request, "core/run_list.html", {"rows": rows})


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
    context = {
        "run": run,
        "component": component,
        "n_total": n_total,
        "prev_index": index - 1 if index > 1 else None,
        "next_index": index + 1 if index < n_total else None,
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


@login_required
def component_detail(request: HttpRequest, run_id: int, index: int) -> HttpResponse:
    run = get_object_or_404(Run, pk=run_id)
    component = get_object_or_404(Component, run=run, index=index)
    context = _component_context(run, component, _authed_user(request))
    return render(request, "core/component_detail.html", context)


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
    return render(request, "core/partials/rate_response.html", context)
