from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseBase,
    HttpResponseForbidden,
)
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_http_methods
from django.views.static import serve

from melrater.core import charts, metrics, selectors, services
from melrater.core import help as help_catalog
from melrater.core.metrics import OUTLIER_Z, MetricFrame, family_of
from melrater.core.models import Component, DiscussionPost, Run
from melrater.core.montage import AXES, BACKGROUNDS, SMOOTHINGS
from melrater.core.schemas import CohortData, ComponentData, RunData

RATING_BUTTONS = [
    {"label": "Signal", "keys": ["1", "s"]},
    {"label": "Unknown", "keys": ["2", "u"]},
    {"label": "Noise", "keys": ["3", "n"]},
]

AXIS_SESSION_KEY = "montage_axis"
DEFAULT_AXIS = "axial"

BACKGROUND_SESSION_KEY = "montage_background"
DEFAULT_BACKGROUND = "func"

SMOOTHING_SESSION_KEY = "montage_smoothing"
DEFAULT_SMOOTHING = "raw"

# A montage URL contains the run's montage digest, so the bytes behind one
# never change — a re-render mints new URLs. That makes them safely immutable,
# which matters: a reviewer walking 96 components would otherwise revalidate
# every image.
MONTAGE_CACHE_CONTROL = "private, max-age=31536000, immutable"

# Every signed-in page renders one reviewer's own state -- their progress,
# their note, a thread they have earned access to -- so no shared cache may
# store one. `private` and not `no-store`: the threat is an intermediary, and
# the reviewer's own browser is wanted as a cache here, which is the same call
# MONTAGE_CACHE_CONTROL makes one line up for a year of montages. Django sends
# no Cache-Control of its own, and the Vary: Cookie that SessionMiddleware
# patches on is incidental -- it comes from touching the session -- so say it.
signed_in_page = cache_control(private=True)


def _authed_user(request: HttpRequest) -> AbstractBaseUser:
    user = request.user
    # login_required guarantees this; narrows away AnonymousUser for ty
    assert not isinstance(user, AnonymousUser)
    return user


@login_required
@signed_in_page
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
    # the row's title= tooltip: 175 of these ship with the page, so the
    # definition goes in the attribute rather than in 175 more popovers
    help_title: str


@dataclass(frozen=True)
class MetricFamily:
    name: str
    rows: list[MetricRow]
    max_z_fmt: str
    color: str
    help_key: str


def _metric_title(name: str, frame: MetricFrame) -> str:
    """The row's `title=` text: the catalog's definition, then the scan's own z.

    The score beside the strip is the cohort's whenever there is one, so the
    within-scan score moves here rather than disappearing. It is still the
    comparison that answers "does this component stand out among its
    siblings", which is a different question from "is this value unusual at
    all" and occasionally the more useful one.
    """
    described = help_catalog.metric_tooltip(name)
    if not frame.cohort:
        return described
    return f"{described}\nwithin this scan: z={frame.scan_z:+.1f}"


def _frame_label(cohort: CohortData | None) -> str:
    """What the panel's z-scores are measured against, in a few words."""
    if cohort is None:
        return "within this scan"
    return f"vs {cohort.n_runs} scan{'' if cohort.n_runs == 1 else 's'}"


def _metric_panel(
    run_data: RunData, comp: ComponentData, cohort: CohortData | None
) -> dict:
    stats = run_data.metric_stats
    norms = cohort.norms if cohort is not None else {}
    rows: list[MetricRow] = []
    for name in stats.names:
        value = comp.metrics[name]
        frame = metrics.metric_frame(
            value=value, stat=stats.stats[name], norm=norms.get(name)
        )
        z = frame.z
        rows.append(
            MetricRow(
                name=name,
                z=z,
                abs_z=abs(z),
                raw_fmt=f"{value.raw:.3g}",
                z_fmt=f"{z:+.1f}",
                severity=charts.severity_color(z),
                glyph=charts.metric_glyph_svg(frame),
                help_title=_metric_title(name, frame),
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
                help_key=help_catalog.family_key(fam_name),
            )
        )
    return {
        "metric_outliers": outliers,
        "metric_families": families,
        "n_outliers": len(outliers),
        "outlier_z": OUTLIER_Z,
        # What every z on this page is measured against. Said out loud beside
        # the outlier count, because the count means a different thing in each
        # frame: unusual for the study, or merely unusual for this scan.
        "metric_frame_label": _frame_label(cohort),
    }


def _component_context(run: Run, component: Component, user: AbstractBaseUser) -> dict:
    # typed projections at the ORM boundary (see schemas.py)
    run_data = selectors.run_data(run)
    comp = selectors.component_data(component)
    index = comp.index
    indices = selectors.component_indices(run)
    n_total = len(indices)
    user_labels = selectors.user_labels_for_run(run, user)
    notes = selectors.notes_for_component(component, user)
    fix = selectors.fix_reference_for_component(component, user_labels)
    prob_strip = (
        charts.prob_strip_svg(
            fix.strip, index, fix.threshold if fix.threshold is not None else 0.01
        )
        if fix.strip
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
        "montage_frames": selectors.montage_frames(component),
        "axes": selectors.AXIS_ORDER,
        "backgrounds": list(run.montage_backgrounds),
        "background_buttons": selectors.background_buttons(run),
        "smoothings": list(run.montage_smoothings),
        "smoothing_buttons": selectors.smoothing_buttons(run),
        "fix": fix,
        "prob_strip": prob_strip,
        "user_label": user_labels.get(index),
        "notes": notes,
        # The standing pre-rating warning. `component_note` overrides it with
        # the transient "saved" after a write that landed.
        "note_state": "" if notes.rated else "pending",
        "note_max_length": services.NOTE_MAX_LENGTH,
        "n_rated": len(user_labels),
        "tc_fd_svg": charts.timecourse_fd_svg(
            comp.timecourse, run_data.fd, run_data.tr, motion_r2=comp.motion_r2
        ),
        "spectrum_svg": charts.spectrum_svg(comp.spectrum, run_data.frequencies),
        "rating_buttons": RATING_BUTTONS,
        "expl_var_fmt": f"{comp.explained_var:.2f}",
        "total_var_fmt": f"{comp.total_var:.2f}",
        "tr_fmt": f"{run_data.tr:g}",
    }
    context.update(_metric_panel(run_data, comp, selectors.metric_cohort()))
    return context


def _active_axis(request: HttpRequest) -> str:
    axis = request.session.get(AXIS_SESSION_KEY, DEFAULT_AXIS)
    return axis if axis in AXES else DEFAULT_AXIS


def _active_background(request: HttpRequest, run: Run) -> str:
    """The chosen background, clamped to what this run actually has.

    Run-aware, unlike `_active_axis`: every run has all three axes, but only a
    registered one has an anatomical. A reviewer whose preference is `anat`
    therefore lands on `func` for a run without one, rather than on a page of
    broken images, and the preference itself is left alone so the next
    registered run honours it again.
    """
    choice = request.session.get(BACKGROUND_SESSION_KEY, DEFAULT_BACKGROUND)
    available = list(run.montage_backgrounds) or [DEFAULT_BACKGROUND]
    return choice if choice in available else DEFAULT_BACKGROUND


def _active_smoothing(request: HttpRequest, run: Run) -> str:
    """The chosen smoothing level, clamped to what this run has.

    Run-aware like `_active_background`: a run rendered before the smoothed
    variant declares `["raw"]` alone, and a reviewer whose preference is
    `smooth` lands on the unsmoothed map there rather than on a broken image.
    """
    choice = request.session.get(SMOOTHING_SESSION_KEY, DEFAULT_SMOOTHING)
    available = list(run.montage_smoothings) or [DEFAULT_SMOOTHING]
    return choice if choice in available else DEFAULT_SMOOTHING


@login_required
@signed_in_page
def component_detail(request: HttpRequest, run_id: int, index: int) -> HttpResponse:
    run = get_object_or_404(Run, pk=run_id)
    component = get_object_or_404(Component, run=run, index=index)
    context = _component_context(run, component, _authed_user(request))
    axis = _active_axis(request)
    background = _active_background(request, run)
    smoothing = _active_smoothing(request, run)
    context["active_axis"] = axis
    context["active_background"] = background
    context["active_smoothing"] = smoothing
    # Warm the next component's montage while this one is being judged. Only
    # the visible variant: the others are not fetched for this component
    # either (the template gives them data-src, not src).
    next_index = context["next_index"]
    context["prefetch_url"] = (
        selectors.montage_url(run, next_index, background, smoothing, axis)
        if next_index
        else None
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
def set_background(request: HttpRequest) -> HttpResponse:
    """Persist the montage-background choice, as `set_axis` does the axis.

    Validated against the vocabulary rather than against a run: the preference
    is global, and clamping it to a run that has no anatomical is
    `_active_background`'s job at render time.
    """
    background = request.POST.get("background", "")
    if background not in BACKGROUNDS:
        return HttpResponseBadRequest(
            f"invalid background: {background!r}", content_type="text/plain"
        )
    request.session[BACKGROUND_SESSION_KEY] = background
    return HttpResponse(status=204)


@login_required
@require_http_methods(["POST"])
def set_smoothing(request: HttpRequest) -> HttpResponse:
    """Persist the overlay-smoothing choice, as `set_background` does the background."""
    smoothing = request.POST.get("smoothing", "")
    if smoothing not in SMOOTHINGS:
        return HttpResponseBadRequest(
            f"invalid smoothing: {smoothing!r}", content_type="text/plain"
        )
    request.session[SMOOTHING_SESSION_KEY] = smoothing
    return HttpResponse(status=204)


@login_required
@require_http_methods(["POST"])
def component_rate(request: HttpRequest, run_id: int, index: int) -> HttpResponse:
    user = _authed_user(request)
    run = get_object_or_404(Run, pk=run_id)
    component = get_object_or_404(Component, run=run, index=index)
    try:
        services.rate_component(
            user=user,
            component=component,
            label=request.POST.get("label", ""),
            # .get("note"), not .get("note", ""): absent means "leave it
            # alone", and a default of "" would blank the note on every
            # rating POST that did not happen to include the textarea.
            note=request.POST.get("note"),
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc), content_type="text/plain")
    context = _component_context(run, component, user)
    context["active_axis"] = _active_axis(request)
    context["active_background"] = _active_background(request, run)
    context["active_smoothing"] = _active_smoothing(request, run)
    return render(request, "core/partials/rate_response.html", context)


@login_required
@require_http_methods(["POST"])
def component_note(request: HttpRequest, run_id: int, index: int) -> HttpResponse:
    """Save the note on its own, when the textarea loses focus.

    Its own endpoint so a note can be revised without re-rating. The rating
    POST carries the note too, which is what covers the reviewer who types and
    then rates in one motion: the blur save that click sets off can reach the
    server before the rating does, and then has no row to land on.

    Answers with the status span alone: re-rendering the card would rebuild
    the whole component context -- charts, montage frames, the metric panel --
    to say one word, and would replace a textarea the reviewer may be back
    inside. "No rating yet" is a 200, not a conflict status, because htmx does
    not swap a 4xx body: a refusal would show the reviewer nothing at all,
    which is the silent loss this status exists to prevent.
    """
    run = get_object_or_404(Run, pk=run_id)
    component = get_object_or_404(Component, run=run, index=index)
    try:
        stored = services.set_component_note(
            user=_authed_user(request),
            component=component,
            note=request.POST.get("note", ""),
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc), content_type="text/plain")
    return render(
        request,
        "core/partials/note_status.html",
        {"note_state": "saved" if stored is not None else "pending"},
    )


def _discussion_context(run: Run, component: Component, user: AbstractBaseUser) -> dict:
    """Everything the discussion page needs, and nothing the rating page does.

    Deliberately not `_component_context`: that one reads six JSONFields,
    renders three SVG charts and the whole metric panel, and builds up to
    twelve montage URLs, none of which this page shows.
    """
    indices = selectors.component_indices(run)
    index = component.index
    at = indices.index(index)
    return {
        "run": run,
        "component": component,
        "n_total": len(indices),
        "prev_index": indices[at - 1] if at > 0 else None,
        "next_index": indices[at + 1] if at + 1 < len(indices) else None,
        "thread": selectors.discussion_for_component(component, user),
        "user_label": selectors.user_label_for_component(component, user),
        "post_max_length": services.POST_MAX_LENGTH,
    }


def _component_of(run_id: int, index: int) -> tuple[Run, Component]:
    run = get_object_or_404(Run, pk=run_id)
    return run, get_object_or_404(Component, run=run, index=index)


def _your_post(
    component: Component, post_uuid: UUID, user: AbstractBaseUser
) -> DiscussionPost:
    """One of the viewer's own posts on this component, or a 404.

    Scoped by author on purpose. "No such post", "somebody else's post" and
    "you have not rated this component" then answer identically, so a uuid --
    which is not a secret: it is the natural key, and it travels in every
    fixture and every pasted URL — cannot be used to confirm that a post
    exists on a component the viewer has no access to.
    """
    return get_object_or_404(
        DiscussionPost.objects.select_related("reviewer"),
        uuid=post_uuid,
        component=component,
        reviewer__user=user,
    )


@login_required
@signed_in_page
def component_discussion(request: HttpRequest, run_id: int, index: int) -> HttpResponse:
    run, component = _component_of(run_id, index)
    context = _discussion_context(run, component, _authed_user(request))
    return render(request, "core/discussion.html", context)


@login_required
@require_http_methods(["POST"])
def discussion_post(request: HttpRequest, run_id: int, index: int) -> HttpResponse:
    """Add one message to the thread, answering with the whole panel.

    One `#discussion` swap rather than an out-of-band pair: it clears the
    composer and refreshes the thread in the same response, which is also what
    makes the "nothing here yet" line disappear.
    """
    user = _authed_user(request)
    run, component = _component_of(run_id, index)
    error = ""
    try:
        services.create_discussion_post(
            user=user, component=component, body=request.POST.get("body", "")
        )
    except services.EmptyPost:
        # A 200, not a 400: `required` on the textarea is satisfied by a single
        # space, so this is reachable by typing, and htmx does not swap a 4xx
        # body — a refusal would show the reviewer nothing at all. The same
        # reasoning `component_note` records for its own refusal.
        error = "a post needs some words in it"
    except services.NotRated:
        return HttpResponseForbidden(
            "rate this component before joining its discussion",
            content_type="text/plain",
        )
    except ValueError as exc:
        # Over-length only, and unreachable through the UI: the textarea
        # carries maxlength. A forged post gets told why.
        return HttpResponseBadRequest(str(exc), content_type="text/plain")
    context = _discussion_context(run, component, user)
    # No draft to hand back: the only error this re-renders is an empty post,
    # so whatever was typed was whitespace, and putting it back would cost the
    # composer its placeholder for nothing.
    context["post_error"] = error
    return render(request, "core/partials/discussion.html", context)


@login_required
@require_http_methods(["POST"])
def discussion_post_edit(
    request: HttpRequest, run_id: int, index: int, post_uuid: UUID
) -> HttpResponse:
    """Rewrite one of your own posts, answering with that row alone."""
    user = _authed_user(request)
    run, component = _component_of(run_id, index)
    post = _your_post(component, post_uuid, user)
    try:
        services.edit_discussion_post(
            user=user, post=post, body=request.POST.get("body", "")
        )
    except services.EmptyPost:
        return HttpResponseBadRequest(
            "a post needs some words in it", content_type="text/plain"
        )
    except services.NotYours:
        return HttpResponseForbidden("not your post", content_type="text/plain")
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc), content_type="text/plain")
    return render(
        request,
        "core/partials/post_row.html",
        {
            "p": selectors.discussion_post_row(post, user),
            "run": run,
            "component": component,
            "post_max_length": services.POST_MAX_LENGTH,
        },
    )


@login_required
@require_http_methods(["POST"])
def discussion_post_delete(
    request: HttpRequest, run_id: int, index: int, post_uuid: UUID
) -> HttpResponse:
    """Withdraw one of your own posts.

    An empty 200, not a 204: htmx's response handling swaps `[23]..` and
    explicitly does not swap a 204, so a 204 would leave the deleted row on
    screen until the page was reloaded.
    """
    user = _authed_user(request)
    _, component = _component_of(run_id, index)
    post = _your_post(component, post_uuid, user)
    try:
        services.delete_discussion_post(user=user, post=post)
    except services.NotYours:
        return HttpResponseForbidden("not your post", content_type="text/plain")
    return HttpResponse(status=200)
