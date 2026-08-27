"""Read-side queries (HackSoft-style selectors)."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser

from melrater.core.charts import ProbEntry
from melrater.core.models import Classification, Component, Reviewer, Run


@dataclass(frozen=True)
class RunProgress:
    run: Run
    n_components: int
    n_rated: int
    next_unrated: int | None  # 1-based index to continue at
    fix_reviewers: list[str]


def runs_with_progress(user: AbstractBaseUser) -> list[RunProgress]:
    rows: list[RunProgress] = []
    for run in Run.objects.all().order_by("label"):
        indices = list(run.components.values_list("index", flat=True).order_by("index"))
        rated = set(
            Classification.objects.filter(
                component__run=run, reviewer__user=user
            ).values_list("component__index", flat=True)
        )
        unrated = [i for i in indices if i not in rated]
        fix_names = list(
            Reviewer.objects.filter(
                kind=Reviewer.Kind.FIX, classifications__component__run=run
            )
            .distinct()
            .values_list("name", flat=True)
        )
        rows.append(
            RunProgress(
                run=run,
                n_components=len(indices),
                n_rated=len(rated),
                next_unrated=unrated[0] if unrated else None,
                fix_reviewers=fix_names,
            )
        )
    return rows


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
            p_signal=by_index[i][0],
            fix_label=by_index[i][1],
            user_label=user_labels.get(i),
        )
        for i in sorted(by_index)
    ]
    thr = fix_reviewer.fix_threshold
    return entries, (thr / 100.0) if thr is not None else None


def montage_urls(component: Component) -> dict[str, str]:
    run = component.run
    return {
        axis: (
            f"{settings.MEDIA_URL}runs/{run.pk}/"
            f"ic{component.index:03d}_{axis}.{run.montage_format}"
        )
        for axis in ("axial", "coronal", "sagittal")
    }


def user_label_for_component(
    component: Component, user: AbstractBaseUser
) -> str | None:
    cls = Classification.objects.filter(
        component=component, reviewer__user=user
    ).first()
    return str(cls.label) if cls else None
