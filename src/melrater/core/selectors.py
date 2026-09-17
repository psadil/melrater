"""Read-side queries (HackSoft-style selectors)."""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from django.contrib.auth.models import AbstractBaseUser
from django.core.paginator import Page, Paginator
from django.db.models import Count, Min, Q

from melrater.core import montage, transfer
from melrater.core import storage as montage_store
from melrater.core.charts import ProbEntry
from melrater.core.models import Classification, Component, Reviewer, Run
from melrater.core.schemas import (
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


def montage_url(run: Run, index: int, background: str, axis: str) -> str:
    """URL of one rendered montage.

    The run's montage digest is part of the path, so a re-render mints new
    URLs rather than changing what the old ones mean — which is what lets the
    media view answer with an immutable cache header instead of a
    revalidation per image per component.
    """
    name = montage_store.run_prefix(
        run.uuid, str(run.montage_digest)
    ) + montage.montage_name(index, background, axis, str(run.montage_format))
    return montage_store.url(name)


def montage_urls(component: Component) -> dict[str, dict[str, str]]:
    """``{background: {axis: url}}``, for the backgrounds this run has.

    Filtered rather than exhaustive: an unregistered run has no anatomical
    montages, and offering their URLs would put six broken images on the page.
    """
    run = component.run
    have = set(run.montage_backgrounds)
    return {
        background: {
            axis: montage_url(run, component.index, background, axis)
            for axis in AXIS_ORDER
        }
        for background in BACKGROUND_ORDER
        if background in have
    }


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
