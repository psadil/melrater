"""Typed schemas for JSONField payloads, read-side projections, and the wire.

Pydantic models form the boundary between the ORM (whose field descriptors
static checkers cannot see through) and typed domain code: services serialize
these into JSONFields at ingest, selectors re-validate rows on read, and
everything past the selector layer works with genuinely typed objects.

The ``*Payload`` models at the bottom are the ingest API's contract, and they
are the same objects at both ends — ``selectors.run_payload`` builds one on the
laptop, ``api.py`` validates one on the server — so the two cannot drift.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

#: A montage background, mirroring montage.BACKGROUNDS. Spelled out rather than
#: derived from it: schemas.py is the wire contract, and a vocabulary the two
#: sides negotiate should not change silently when a constant does.
Background = Literal["func", "anat"]


class MetricValue(BaseModel):
    """One pyFIX metric for one component."""

    raw: float
    z: float


class MetricStat(BaseModel):
    """One metric's distribution across a run's components (robust-z units)."""

    p5: float
    p25: float
    p75: float
    p95: float
    signal_z: list[float]  # z of the FIX-labeled Signal components


class MetricStats(BaseModel):
    """Run-level metric distributions (stored in Run.metric_stats)."""

    names: list[str]
    dropped: list[str]
    stats: dict[str, MetricStat]


class RunData(BaseModel):
    """Typed projection of a Run row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    tr: float
    n_timepoints: int
    fd: list[float]
    frequencies: list[float]
    metric_stats: MetricStats
    montage_format: str
    montage_backgrounds: tuple[Background, ...]


class ComponentData(BaseModel):
    """Typed projection of a Component row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    index: int
    explained_var: float
    total_var: float
    timecourse: list[float]
    spectrum: list[float]
    metrics: dict[str, MetricValue]


# --- the ingest API's wire contract --------------------------------------


class FixVerdictPayload(BaseModel):
    """One FIX classifier's call on one component, in component order."""

    label: Literal["Signal", "Noise", "Unknown"]
    probability: float | None = None


class FixReviewerPayload(BaseModel):
    """One pyFIX model+threshold and its verdicts for a whole run.

    Only FIX reviewers travel. There is no representation for a human one, so
    a payload is structurally incapable of overwriting anybody's ratings.
    """

    model: str
    threshold: int | None = None
    verdicts: list[FixVerdictPayload]


class ComponentPayload(BaseModel):
    index: int
    explained_var: float
    total_var: float
    timecourse: list[float]
    spectrum: list[float]
    metrics: dict[str, MetricValue]


class RunPayload(BaseModel):
    """One run on the wire: its row, its components, and its FIX verdicts.

    ``extra="forbid"`` is load-bearing rather than tidiness. It makes a sender
    that invents a field — most plausibly a stale client still trying to set a
    montage revision — fail loudly instead of being silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    uuid: UUID
    label: str
    path: str
    sub: str = ""
    ses: str = ""
    task: str = ""
    run: str = ""
    tr: float
    n_timepoints: int
    fd: list[float]
    frequencies: list[float]
    metric_stats: MetricStats
    montage_format: Literal["avif", "png"]
    #: Fingerprint of the montage tar travelling with this payload. The
    #: receiver recomputes it from the bytes it actually got and refuses a
    #: mismatch, so a truncated upload cannot be committed as a whole run.
    montage_digest: str
    #: Which backgrounds the tar carries, in display order. Defaulted rather
    #: than required so a payload written before the anatomical background
    #: still validates; `extra="forbid"` makes the other direction — a new
    #: client against an old server — the loud one, which is the right way
    #: round for a push that would otherwise store a half set.
    backgrounds: tuple[Background, ...] = ("func",)
    components: list[ComponentPayload]
    fix: list[FixReviewerPayload]


class RunSummary(BaseModel):
    """What the ingest index tells a client about a run the server holds.

    Carries no ratings, and no counter: two databases agree on
    ``montage_digest`` without either being told, so "already present,
    unchanged" is an exact comparison rather than an approximate one.
    """

    uuid: UUID
    label: str
    montage_digest: str


class PushResult(BaseModel):
    """What the server reports back about a run it has just taken."""

    uuid: UUID
    label: str
    created: bool
    montage_digest: str
    n_montages: int
