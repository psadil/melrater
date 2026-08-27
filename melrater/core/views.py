from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from melrater.core import charts, selectors, services
from melrater.core.metrics import OUTLIER_Z, family_of
from melrater.core.models import Component, Run

RATING_BUTTONS = [
    {"label": "Signal", "keys": ["1", "s"]},
    {"label": "Unknown", "keys": ["2", "u"]},
    {"label": "Noise", "keys": ["3", "n"]},
]


@login_required
def run_list(request: HttpRequest) -> HttpResponse:
    rows = selectors.runs_with_progress(request.user)
    return render(request, "core/run_list.html", {"rows": rows})


def _metric_panel(run: Run, component: Component) -> dict:
    stats = run.metric_stats["stats"]
    rows = []
    for name in run.metric_stats["names"]:
        s = stats[name]
        m = component.metrics[name]
        z = float(m["z"])
        rows.append(
            {
                "name": name,
                "z": z,
                "abs_z": abs(z),
                "raw_fmt": f"{float(m['raw']):.3g}",
                "z_fmt": f"{z:+.1f}",
                "severity": charts.severity_color(z),
                "glyph": charts.metric_glyph_svg(
                    z, s["p5"], s["p25"], s["p75"], s["p95"], s["signal_z"]
                ),
            }
        )
    outliers = sorted(
        (r for r in rows if r["abs_z"] > OUTLIER_Z), key=lambda r: -r["abs_z"]
    )
    families: dict[str, dict] = {}
    for r in rows:
        fam = families.setdefault(
            family_of(r["name"]),
            {"name": family_of(r["name"]), "rows": [], "max_z": 0.0},
        )
        fam["rows"].append(r)
        fam["max_z"] = max(fam["max_z"], r["abs_z"])
    family_list = [
        {
            **f,
            "max_z_fmt": f"{f['max_z']:.1f}",
            "color": charts.severity_color(f["max_z"]),
        }
        for f in sorted(families.values(), key=lambda f: str(f["name"]))
    ]
    return {
        "metric_outliers": outliers,
        "metric_families": family_list,
        "n_outliers": len(outliers),
        "outlier_z": OUTLIER_Z,
    }


def _component_context(run: Run, component: Component, user) -> dict:
    index = int(component.index)
    indices = list(run.components.values_list("index", flat=True).order_by("index"))
    n_total = len(indices)
    user_labels = selectors.user_labels_for_run(run, user)
    prob_entries, threshold = selectors.prob_entries_for_run(run, user_labels)
    prob_strip = (
        charts.prob_strip_svg(prob_entries, index - 1, threshold or 0.01)
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
        "tc_fd_svg": charts.timecourse_fd_svg(component.timecourse, run.fd, run.tr),
        "spectrum_svg": charts.spectrum_svg(component.spectrum, run.frequencies),
        "rating_buttons": RATING_BUTTONS,
        "expl_var_fmt": f"{component.explained_var:.2f}",
        "total_var_fmt": f"{component.total_var:.2f}",
        "tr_fmt": f"{run.tr:g}",
    }
    context.update(_metric_panel(run, component))
    return context


@login_required
def component_detail(request: HttpRequest, run_id: int, index: int) -> HttpResponse:
    run = get_object_or_404(Run, pk=run_id)
    component = get_object_or_404(Component, run=run, index=index)
    context = _component_context(run, component, request.user)
    return render(request, "core/component_detail.html", context)


@login_required
@require_POST
def component_rate(request: HttpRequest, run_id: int, index: int) -> HttpResponse:
    run = get_object_or_404(Run, pk=run_id)
    component = get_object_or_404(Component, run=run, index=index)
    try:
        services.rate_component(
            user=request.user, component=component, label=request.POST.get("label", "")
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    context = _component_context(run, component, request.user)
    return render(request, "core/partials/rate_response.html", context)
