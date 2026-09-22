"""Read-side queries (HackSoft-style selectors)."""

from __future__ import annotations

import array
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

import numpy as np
from django.contrib.auth.models import AbstractBaseUser
from django.core.paginator import Page, Paginator
from django.db.models import Count, Min, Q

from melrater.core import montage, transfer
from melrater.core import storage as montage_store
from melrater.core.charts import ProbEntry
from melrater.core.models import (
    Classification,
    Component,
    DiscussionPost,
    MetricCohort,
    Reviewer,
    Run,
)
from melrater.core.schemas import (
    CohortData,
    ComponentData,
    ComponentPayload,
    FixReviewerPayload,
    FixVerdictPayload,
    RunData,
    RunPayload,
    RunSummary,
)

#: Display order of the montage axes (montage.AXES is keyed by array axis).
AXIS_ORDER = ("axial", "coronal", "sagittal")

#: Display order of the montage backgrounds. A run carries its own subset in
#: `Run.montage_backgrounds`; this is only the order they are offered in.
BACKGROUND_ORDER = ("func", "anat")

#: What to call each background in the UI. The stored keys are abbreviations
#: because they are in every montage filename; buttons are not.
BACKGROUND_LABELS = {"func": "functional", "anat": "anatomical"}


def background_buttons(run: Run) -> list[dict[str, str]]:
    """This run's backgrounds as ``{key, label}``, in display order."""
    have = set(run.montage_backgrounds)
    return [
        {"key": background, "label": BACKGROUND_LABELS[background]}
        for background in BACKGROUND_ORDER
        if background in have
    ]


#: Display order of the overlay smoothing levels, and what to call them. Same
#: arrangement as the backgrounds: the stored keys are in every filename.
SMOOTHING_ORDER = ("raw", "smooth")
SMOOTHING_LABELS = {"raw": "unsmoothed", "smooth": "smoothed"}


def smoothing_buttons(run: Run) -> list[dict[str, str]]:
    """This run's smoothing levels as ``{key, label}``, in display order."""
    have = set(run.montage_smoothings)
    return [
        {"key": smoothing, "label": SMOOTHING_LABELS[smoothing]}
        for smoothing in SMOOTHING_ORDER
        if smoothing in have
    ]


#: The two edge marks drawn at the bottom of a lightbox's first cell, per
#: display axis: axial and coronal are neurological (subject left on the left),
#: sagittal puts posterior on the left.
EDGE_LABELS = {"axial": ("L", "R"), "coronal": ("L", "R"), "sagittal": ("P", "A")}


@dataclass(frozen=True)
class CellLabel:
    """One slice-index label and where it sits on the montage."""

    index: int
    left: str  # CSS percentage of the montage's width
    top: str  # CSS percentage of the montage's height


@dataclass(frozen=True)
class AxisLabels:
    """Everything the page draws over one axis' lightbox."""

    axis: str
    cols: int
    rows: int
    cells: list[CellLabel]
    edge_left: str
    edge_right: str


def _percent(part: int, whole: int) -> str:
    return f"{part / whole * 100:.4g}%"


def slice_labels(run: Run) -> dict[str, AxisLabels]:
    """Where each slice label goes, per axis, as CSS percentages.

    The lightbox tiles its cells with no gap, so cell ``i`` starts at
    ``(i % cols) / cols`` of the width and ``(i // cols) / rows`` of the height
    whatever the image's pixel size — which is what lets one label layer serve
    both backgrounds and both smoothing levels, and stay sharp however the
    browser scales the image. Axes the run has no picks for (rendered before
    picks were recorded) get no labels rather than wrong ones.
    """
    cols = montage.LIGHTBOX_COLS
    labels: dict[str, AxisLabels] = {}
    for axis in AXIS_ORDER:
        indices = [int(i) for i in run.montage_picks.get(axis, [])]
        if not indices:
            continue
        rows = -(-len(indices) // cols)
        left, right = EDGE_LABELS[axis]
        labels[axis] = AxisLabels(
            axis=axis,
            cols=cols,
            rows=rows,
            cells=[
                CellLabel(
                    index=index,
                    left=_percent(i % cols, cols),
                    top=_percent(i // cols, rows),
                )
                for i, index in enumerate(indices)
            ],
            edge_left=left,
            edge_right=right,
        )
    return labels


#: Runs per page of the run list.
RUNS_PER_PAGE = 50

#: Columns the run list needs. Run also carries three JSONFields — fd,
#: frequencies and metric_stats — that are tens of kilobytes each; fetching
#: them for 300 rows nobody charts is megabytes of pure waste.
_LIST_FIELDS = (
    "id",
    "uuid",
    "label",
    "sub",
    "ses",
    "task",
    "run",
    "tr",
    "montage_format",
    "montage_digest",
)


def run_ingested(root: Path) -> bool:
    """Whether the run rooted at ``root`` is already in the database."""
    return Run.objects.filter(path=str(root.resolve())).exists()


def run_data(run: Run) -> RunData:
    """Typed projection of a Run row (validates the stored JSON payloads)."""
    return RunData.model_validate(run)


def component_data(component: Component) -> ComponentData:
    """Typed projection of a Component row."""
    return ComponentData.model_validate(component)


def metric_cohort() -> CohortData | None:
    """The stored cohort distributions, or None before one has been built.

    None is an ordinary state, not an error: a fresh database has no cohort
    until `refresh_metric_cohort` has run, and every metric strip falls back
    to the scan it belongs to meanwhile.
    """
    row = MetricCohort.objects.first()
    return CohortData.model_validate(row) if row is not None else None


@dataclass(frozen=True)
class MetricSweep:
    """Every stored raw metric value, pooled by metric name."""

    values: dict[str, np.ndarray]
    n_runs: int
    n_components: int


def metric_raw_values(*, chunk_size: int = 2000) -> MetricSweep:
    """Read every component's raw metric values, for a cohort rebuild.

    Streamed and accumulated as float32 rather than materialised: the full
    campaign is a few hundred thousand components with ~186 features each, and
    the same numbers as Python floats in lists would be gigabytes where these
    are a couple of hundred megabytes. `.iterator()` is what keeps the
    queryset from also holding every row it has already handed over.

    A metric missing from a component simply contributes no value — features
    that are constant within one scan are dropped there, and a scan that
    dropped one is not evidence about it either way.
    """
    pooled: dict[str, array.array[float]] = {}
    n_components = 0
    stored = Component.objects.values_list("metrics", flat=True)
    for row in stored.iterator(chunk_size=chunk_size):
        n_components += 1
        for name, value in row.items():
            pooled.setdefault(name, array.array("f")).append(value["raw"])
    return MetricSweep(
        values={
            name: np.frombuffer(buffer, dtype=np.float32)
            for name, buffer in pooled.items()
        },
        n_runs=Run.objects.count(),
        n_components=n_components,
    )


@dataclass(frozen=True)
class RunProgress:
    run: Run
    n_components: int
    n_rated: int
    next_unrated: int | None  # 1-based index to continue at
    fix_reviewers: list[str]

    @property
    def complete(self) -> bool:
        return self.next_unrated is None


@dataclass(frozen=True)
class RunListPage:
    """One page of the run list, plus the totals the header shows."""

    page: Page
    rows: list[RunProgress]
    n_matching: int
    n_runs: int
    resume: RunProgress | None  # first run anywhere with work left


def run_list_page(
    user: AbstractBaseUser,
    *,
    query: str = "",
    hide_complete: bool = False,
    page_number: int | str | None = 1,
) -> RunListPage:
    """The run list, with per-run progress, filtered and paginated.

    Query count is bounded and independent of how many runs exist: three
    grouped aggregates produce one row per run, and only the visible page pays
    for the FIX-reviewer lookup, which is the one query that scales with
    components rather than runs.
    """
    runs = Run.objects.only(*_LIST_FIELDS).order_by("label")
    n_runs = runs.count()
    if query:
        runs = runs.filter(
            Q(label__icontains=query)
            | Q(sub__icontains=query)
            | Q(ses__icontains=query)
            | Q(task__icontains=query)
            | Q(run__icontains=query)
        )

    matched = list(runs)
    totals = _component_counts()
    rated = _rated_counts(user)
    unrated = _first_unrated(user)

    def progress(run: Run, fix_reviewers: list[str]) -> RunProgress:
        return RunProgress(
            run=run,
            n_components=totals.get(run.pk, 0),
            n_rated=rated.get(run.pk, 0),
            next_unrated=unrated.get(run.pk),
            fix_reviewers=fix_reviewers,
        )

    rows = [progress(run, []) for run in matched]
    if hide_complete:
        rows = [row for row in rows if not row.complete]

    paginator = Paginator(rows, RUNS_PER_PAGE)
    page = paginator.get_page(page_number)
    fix_names = _fix_reviewer_names([row.run.pk for row in page.object_list])
    page_rows = [
        progress(row.run, fix_names.get(row.run.pk, [])) for row in page.object_list
    ]

    # The first run with work left *in the current view*: filtering to one
    # subject and clicking resume should land in that subject. Ordered by
    # label, like the list itself, and unaffected by which page is showing.
    resume = next(
        (progress(run, []) for run in matched if unrated.get(run.pk) is not None), None
    )
    return RunListPage(
        page=page,
        rows=page_rows,
        n_matching=len(rows),
        n_runs=n_runs,
        resume=resume,
    )


def _component_counts() -> dict[int, int]:
    """run id -> number of components. One row per run."""
    return dict(
        Component.objects.order_by()
        .values("run_id")
        .annotate(n=Count("pk"))
        .values_list("run_id", "n")
    )


def _rated_counts(user: AbstractBaseUser) -> dict[int, int]:
    """run id -> number of components this user has rated. One row per run."""
    return dict(
        Classification.objects.filter(reviewer__user=user)
        .order_by()
        .values("component__run_id")
        .annotate(n=Count("pk"))
        .values_list("component__run_id", "n")
    )


def _first_unrated(user: AbstractBaseUser) -> dict[int, int]:
    """run id -> lowest component index this user has not rated."""
    return dict(
        Component.objects.exclude(classifications__reviewer__user=user)
        .order_by()  # Component.Meta.ordering would otherwise join the GROUP BY
        .values("run_id")
        .annotate(first=Min("index"))
        .values_list("run_id", "first")
    )


def _fix_reviewer_names(run_ids: Sequence[int]) -> dict[int, list[str]]:
    """run id -> FIX reviewer names, for the runs on one page only."""
    names: dict[int, list[str]] = {}
    if not run_ids:
        return names
    rows = (
        Classification.objects.filter(
            component__run_id__in=list(run_ids), reviewer__kind=Reviewer.Kind.FIX
        )
        .order_by("reviewer__name")
        .values_list("component__run_id", "reviewer__name")
        .distinct()
    )
    for run_id, name in rows:
        names.setdefault(run_id, []).append(name)
    return names


@dataclass(frozen=True)
class FixVerdictRow:
    reviewer: str
    label: str
    probability: float | None
    probability_fmt: str
    threshold: float | None  # decision threshold as a probability (thrN -> N/100)


def fix_verdicts_for_component(component: Component) -> list[FixVerdictRow]:
    rows: list[FixVerdictRow] = []
    qs = (
        Classification.objects.filter(
            component=component, reviewer__kind=Reviewer.Kind.FIX
        )
        .select_related("reviewer")
        .order_by("reviewer__name")
    )
    for cls in qs:
        thr = cls.reviewer.fix_threshold
        prob = cls.probability
        rows.append(
            FixVerdictRow(
                reviewer=str(cls.reviewer.name),
                label=str(cls.label),
                probability=prob,
                probability_fmt=f"{prob:.3g}" if prob is not None else "",
                threshold=(thr / 100.0) if thr is not None else None,
            )
        )
    return rows


@dataclass(frozen=True)
class NotePanel:
    """The note card's whole state for one viewer on one component.

    A note is its author's alone: nobody else reads it, now or later. It was
    briefly shared with reviewers who had already rated, and the half of that
    which was worth keeping -- reviewers reading each other's reasoning -- is
    now `DiscussionThread`, where it can be answered instead of just seen.
    """

    mine: str
    rated: bool


def notes_for_component(component: Component, user: AbstractBaseUser) -> NotePanel:
    mine = Classification.objects.filter(
        component=component, reviewer__user=user
    ).first()
    return NotePanel(mine=str(mine.note) if mine else "", rated=mine is not None)


@dataclass(frozen=True)
class DiscussionPostRow:
    """One message in a thread, as the page renders it."""

    uuid: UUID
    reviewer: str
    label: str
    body: str
    created_at: datetime
    edited_at: datetime | None
    #: Whether the viewer wrote this one, and so may edit or delete it.
    #: Decided here so no template ever compares users.
    mine: bool


@dataclass(frozen=True)
class DiscussionThread:
    """A component's whole thread for one viewer.

    `posts` stays empty until the viewer has rated this component themselves,
    and `rated` is the only thing the page may say about it before then -- not
    a post, not an author, not a count, not that anybody has written at all.
    The gate lives here rather than in the template, as the note reveal used
    to: a judgement formed after reading somebody else's reasoning is not an
    independent one, which is what any later measure of agreement between
    reviewers rests on.
    """

    rated: bool
    posts: list[DiscussionPostRow]


def _post_row(post: DiscussionPost, *, mine: bool) -> DiscussionPostRow:
    return DiscussionPostRow(
        uuid=post.uuid,
        reviewer=str(post.reviewer.name),
        label=str(post.label),
        body=str(post.body),
        created_at=post.created_at,
        edited_at=post.edited_at,
        mine=mine,
    )


def discussion_for_component(
    component: Component, user: AbstractBaseUser
) -> DiscussionThread:
    rating = Classification.objects.filter(
        component=component, reviewer__user=user
    ).first()
    # The gate, and the whole of it: nothing below this line runs for a viewer
    # who has not rated, so there is no post, no name and no count to leak.
    if rating is None:
        return DiscussionThread(rated=False, posts=[])
    # Meta.ordering is oldest-first, so no order_by here. Two queries, both
    # constant in the number of posts; the label is on the row rather than
    # joined from Classification, so a reviewer changing their mind cannot
    # rewrite the verdict beside everything they have already written.
    posts = DiscussionPost.objects.filter(component=component).select_related(
        "reviewer"
    )
    return DiscussionThread(
        rated=True,
        posts=[_post_row(p, mine=p.reviewer_id == rating.reviewer_id) for p in posts],
    )


def discussion_post_row(
    post: DiscussionPost, user: AbstractBaseUser
) -> DiscussionPostRow:
    """One post in the same shape the thread renders, for a single-row swap.

    The edit endpoint re-renders one row on its own, and a template resolves a
    missing attribute to the empty string -- handed a bare model it would
    quietly come back with an empty verdict chip and no edit controls.
    """
    return _post_row(post, mine=post.reviewer.user_id == user.pk)


def component_indices(run: Run) -> list[int]:
    """This run's component numbers, in order.

    Materialised rather than assumed contiguous: both the rating page and
    the discussion page derive their previous/next links from it.
    """
    return list(run.components.values_list("index", flat=True).order_by("index"))


def user_labels_for_run(run: Run, user: AbstractBaseUser) -> dict[int, str]:
    """1-based component index -> this user's label."""
    return {
        int(index): str(label)
        for index, label in Classification.objects.filter(
            component__run=run, reviewer__user=user
        ).values_list("component__index", "label")
    }


def prob_entries_for_run(
    run: Run, user_labels: dict[int, str]
) -> tuple[list[ProbEntry], float | None]:
    """Ticks for the P(signal) strip from the run's primary FIX reviewer.

    Returns ([] , None) when the run has no FIX classifications.
    """
    fix_reviewer = (
        Reviewer.objects.filter(
            kind=Reviewer.Kind.FIX, classifications__component__run=run
        )
        .order_by("name")
        .first()
    )
    if fix_reviewer is None:
        return [], None
    by_index = {
        int(index): (float(prob) if prob is not None else 0.0, str(label))
        for index, label, prob in Classification.objects.filter(
            component__run=run, reviewer=fix_reviewer
        ).values_list("component__index", "label", "probability")
    }
    entries = [
        ProbEntry(
            index=i,
            p_signal=by_index[i][0],
            fix_label=by_index[i][1],
            user_label=user_labels.get(i),
        )
        for i in sorted(by_index)
    ]
    thr = fix_reviewer.fix_threshold
    return entries, (thr / 100.0) if thr is not None else None


def montage_url(
    run: Run, index: int, background: str, smoothing: str, axis: str
) -> str:
    """URL of one rendered montage.

    The run's montage digest is part of the path, so a re-render mints new
    URLs rather than changing what the old ones mean — which is what lets the
    media view answer with an immutable cache header instead of a
    revalidation per image per component.
    """
    name = montage_store.run_prefix(
        run.uuid, str(run.montage_digest)
    ) + montage.montage_name(
        index, background, smoothing, axis, str(run.montage_format)
    )
    return montage_store.url(name)


def montage_urls(component: Component) -> dict[str, dict[str, dict[str, str]]]:
    """``{background: {smoothing: {axis: url}}}``, for what this run has.

    Filtered rather than exhaustive: an unregistered run has no anatomical
    montages, and a run rendered before the smoothed variant has none of
    those; offering their URLs would put a screen of broken images on the page.
    """
    run = component.run
    backgrounds = set(run.montage_backgrounds)
    smoothings = set(run.montage_smoothings)
    return {
        background: {
            smoothing: {
                axis: montage_url(run, component.index, background, smoothing, axis)
                for axis in AXIS_ORDER
            }
            for smoothing in SMOOTHING_ORDER
            if smoothing in smoothings
        }
        for background in BACKGROUND_ORDER
        if background in backgrounds
    }


@dataclass(frozen=True)
class MontageImage:
    background: str
    smoothing: str
    url: str


@dataclass(frozen=True)
class MontageFrame:
    """One axis' images and its label layer, as the template lays them out."""

    axis: str
    images: list[MontageImage]
    labels: AxisLabels | None


def montage_frames(component: Component) -> list[MontageFrame]:
    """``montage_urls`` regrouped by axis, which is how the page nests them.

    One frame per axis holds every background x smoothing image for that axis
    and the one label layer they share; the page shows one frame and, within
    it, one image at a time.
    """
    urls = montage_urls(component)
    labels = slice_labels(component.run)
    return [
        MontageFrame(
            axis=axis,
            images=[
                MontageImage(background=background, smoothing=smoothing, url=url)
                for background, by_smoothing in urls.items()
                for smoothing, by_axis in by_smoothing.items()
                for url in (by_axis[axis],)
            ],
            labels=labels.get(axis),
        )
        for axis in AXIS_ORDER
    ]


def user_label_for_component(
    component: Component, user: AbstractBaseUser
) -> str | None:
    cls = Classification.objects.filter(
        component=component, reviewer__user=user
    ).first()
    return str(cls.label) if cls else None


# --- the ingest API's read side ------------------------------------------


def run_summaries() -> list[RunSummary]:
    """Every run this database holds, as the ingest index reports them.

    Deliberately three columns. A client only has to answer "do I need to send
    this run", and anything more would be telling an ingest credential about
    the ratings.
    """
    return [
        RunSummary(uuid=uuid, label=label, montage_digest=digest)
        for uuid, label, digest in Run.objects.order_by("label").values_list(
            "uuid", "label", "montage_digest"
        )
    ]


def run_payload(run: Run) -> RunPayload:
    """One run as the ingest API takes it: rows, components, FIX verdicts.

    Human reviewers have no representation in ``RunPayload`` at all, so this
    cannot carry a rating even by mistake — the same property `export_runs`
    used to provide by filtering, now provided by the type.
    """
    components = [
        ComponentPayload.model_validate(component, from_attributes=True)
        for component in run.components.order_by("index")
    ]
    montage_format = str(run.montage_format)
    if montage_format not in ("avif", "png"):
        raise ValueError(f"unknown montage format: {montage_format!r}")
    return RunPayload(
        uuid=run.uuid,
        label=str(run.label),
        path=str(run.path),
        sub=str(run.sub),
        ses=str(run.ses),
        task=str(run.task),
        run=str(run.run),
        tr=float(run.tr),
        n_timepoints=int(run.n_timepoints),
        fd=list(run.fd),
        frequencies=list(run.frequencies),
        metric_stats=run_data(run).metric_stats,
        montage_format=montage_format,
        montage_digest=str(run.montage_digest),
        backgrounds=tuple(run.montage_backgrounds),
        smoothings=tuple(run.montage_smoothings),
        picks={
            axis: [int(i) for i in picks] for axis, picks in run.montage_picks.items()
        },
        components=components,
        fix=_fix_payloads(run),
    )


def _fix_payloads(run: Run) -> list[FixReviewerPayload]:
    """This run's FIX reviewers, each with its verdicts in component order."""
    rows = (
        Classification.objects.filter(
            component__run=run, reviewer__kind=Reviewer.Kind.FIX
        )
        .select_related("reviewer")
        .order_by("reviewer__name", "component__index")
    )
    by_reviewer: dict[int, list[FixVerdictPayload]] = {}
    reviewers: dict[int, Reviewer] = {}
    for row in rows:
        label = str(row.label)
        if label not in ("Signal", "Noise", "Unknown"):
            raise ValueError(f"unknown classification label: {label!r}")
        reviewers.setdefault(row.reviewer.pk, row.reviewer)
        by_reviewer.setdefault(row.reviewer.pk, []).append(
            FixVerdictPayload(label=label, probability=row.probability)
        )
    return [
        FixReviewerPayload(
            model=str(reviewers[pk].fix_model),
            threshold=reviewers[pk].fix_threshold,
            verdicts=verdicts,
        )
        for pk, verdicts in by_reviewer.items()
    ]


def montage_members(run: Run) -> list[tuple[str, bytes]]:
    """One run's stored montages as ``(basename, bytes)``, for the push tar."""
    storage = montage_store.montage_storage()
    prefix = montage_store.run_prefix(run.uuid, str(run.montage_digest))
    members = []
    for name in montage_store.names_for_run(run.uuid, str(run.montage_digest)):
        with storage.open(name) as handle:
            members.append((name.removeprefix(prefix), handle.read()))
    return members


def run_by_uuid(run_uuid: UUID) -> Run | None:
    return Run.objects.filter(uuid=run_uuid).first()


def run_bundle(run: Run) -> tuple[bytes, bytes]:
    """One run as the two file parts of a push: its JSON and its montage tar.

    The tar holds bare montage names — the uuid and digest are in the payload,
    not the archive — so the receiver decides where the files land and nothing
    from this side can name a path over there.
    """
    payload = run_payload(run).model_dump_json().encode()
    buffer = io.BytesIO()
    transfer.write_montage_tar(montage_members(run), buffer)
    return payload, buffer.getvalue()
