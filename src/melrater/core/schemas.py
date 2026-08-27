"""Typed schemas for JSONField payloads and read-side projections.

Pydantic models form the boundary between the ORM (whose field descriptors
static checkers cannot see through) and typed domain code: services serialize
these into JSONFields at ingest, selectors re-validate rows on read, and
everything past the selector layer works with genuinely typed objects.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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
