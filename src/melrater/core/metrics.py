"""Robust z-scoring of pyFIX features, in two reference frames.

Within one scan, z = (x - median)/(1.4826*MAD) across that scan's components
(std fallback when MAD is 0; constant features are dropped). That is what
:func:`build_metric_table` computes at ingest and ``Run.metric_stats`` stores,
and it says how unusual a component is among its own siblings.

Across the cohort, the same robust centre and scale are taken over every
component of every scan this database holds (:func:`build_metric_norms`,
rebuilt by the ``refresh_metric_cohort`` command). One scan holds a few dozen
components, which is thin evidence for what "unusual" means; the cohort is
where a value can be placed against the study.

:func:`metric_frame` picks between them for one displayed row -- the cohort
when it knows the metric, the scan when it does not -- and restates the scan's
own bands in whichever frame it chose, so a single strip can carry both.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from melrater.core.schemas import (
    MetricNorm,
    MetricStat,
    MetricStats,
    MetricValue,
)

OUTLIER_Z = 3.0  # |robust z| above which a metric counts as an outlier


@dataclass(frozen=True)
class MetricTable:
    names: list[str]  # kept feature names
    raw: np.ndarray  # (n_components, n_kept)
    z: np.ndarray  # (n_components, n_kept), unclipped
    median: np.ndarray  # (n_kept,) the centre each kept column was scored on
    scale: np.ndarray  # (n_kept,) and the scale, both in raw units
    dropped: list[str]  # constant features excluded from z-scoring


def _robust_scale(values: np.ndarray, median: np.ndarray, axis: int) -> np.ndarray:
    """1.4826*MAD, falling back to the standard deviation where MAD is 0."""
    scale = 1.4826 * np.median(np.abs(values - median), axis=axis)
    return np.where(scale > 0, scale, values.std(axis=axis))


def build_metric_table(names: list[str], raw: np.ndarray) -> MetricTable:
    med = np.median(raw, axis=0)
    scale = _robust_scale(raw, med, axis=0)
    keep = scale > 0
    z = (raw[:, keep] - med[keep]) / scale[keep]
    return MetricTable(
        names=[n for n, k in zip(names, keep) if k],
        raw=raw[:, keep],
        z=z,
        median=med[keep],
        scale=scale[keep],
        dropped=[n for n, k in zip(names, keep) if not k],
    )


def family_of(name: str) -> str:
    return name.split(":")[0]


def compute_metric_stats(table: MetricTable, signal_rows: list[int]) -> MetricStats:
    """Per-metric distribution bands and the robust-z positions of the
    FIX-labeled Signal components (the glyphs' green ticks).

    The centre and scale travel with the bands. They are what makes the stored
    z units convertible: a cohort z-score is a different scale over the same
    raw values, and without them a band could only ever be drawn in the frame
    it was computed in.
    """
    p5, p25, p75, p95 = np.percentile(table.z, (5, 25, 75, 95), axis=0)
    return MetricStats(
        names=table.names,
        dropped=table.dropped,
        stats={
            name: MetricStat(
                median=float(table.median[j]),
                scale=float(table.scale[j]),
                p5=float(p5[j]),
                p25=float(p25[j]),
                p75=float(p75[j]),
                p95=float(p95[j]),
                signal_z=[float(table.z[i, j]) for i in signal_rows],
            )
            for j, name in enumerate(table.names)
        },
    )


def compute_component_metrics(table: MetricTable, row: int) -> dict[str, MetricValue]:
    """Raw value and robust z for each of one component's metrics."""
    return {
        name: MetricValue(raw=float(table.raw[row, j]), z=float(table.z[row, j]))
        for j, name in enumerate(table.names)
    }


def build_metric_norms(
    values: Mapping[str, np.ndarray],
) -> dict[str, MetricNorm]:
    """Each metric's cohort distribution, from every raw value ever stored.

    The same robust centre and scale as :func:`build_metric_table`, over the
    pooled values rather than one scan's. A metric that is constant across the
    whole cohort is left out, for the reason a constant one is dropped within a
    scan: with no spread there is no scale to state a position in.
    """
    norms: dict[str, MetricNorm] = {}
    for name, pooled in values.items():
        v = np.asarray(pooled, dtype=float)
        if v.size == 0:
            continue
        median = np.median(v)
        scale = float(_robust_scale(v, median, axis=0))
        if scale <= 0:
            continue
        p5, p25, p75, p95 = np.percentile((v - median) / scale, (5, 25, 75, 95))
        norms[name] = MetricNorm(
            median=float(median),
            scale=scale,
            p5=float(p5),
            p25=float(p25),
            p75=float(p75),
            p95=float(p95),
            n=int(v.size),
        )
    return norms


@dataclass(frozen=True)
class ScanRange:
    """Where one scan's own components sit, in the displayed frame's units."""

    p25: float
    p75: float
    median: float


@dataclass(frozen=True)
class MetricFrame:
    """One metric row's position and bands, in the frame being displayed.

    Every number is in that one frame's z units, so a caller never has to know
    which it got -- except through `cohort`, which is what the page says out
    loud so a reviewer knows what the strip is comparing against.
    """

    cohort: bool  # bands are the cohort's; False means this scan's components
    z: float  # this component, in the frame's units
    scan_z: float  # this component among its own siblings, always
    p5: float
    p25: float
    p75: float
    p95: float
    signal_z: list[float]  # this scan's FIX Signal components, in frame units
    # This scan's own spread -- the rail the glyph draws above the cohort
    # bands. None when the bands already are this scan, because then the rail
    # would be a copy of them.
    scan_range: ScanRange | None


def metric_frame(
    *, value: MetricValue, stat: MetricStat, norm: MetricNorm | None
) -> MetricFrame:
    """One row's numbers: against the cohort when there is one for this metric.

    Falls back to the scan frame per *metric*, not per page: a cohort built
    before a pyFIX version that writes a new feature simply has nothing to say
    about that feature, and the scan can still place it.
    """
    if norm is None:
        return MetricFrame(
            cohort=False,
            z=value.z,
            scan_z=value.z,
            p5=stat.p5,
            p25=stat.p25,
            p75=stat.p75,
            p95=stat.p95,
            signal_z=list(stat.signal_z),
            scan_range=None,
        )

    def in_cohort(scan_z: float) -> float:
        """A position in this scan's z units, restated in the cohort's."""
        return (scan_z * stat.scale + stat.median - norm.median) / norm.scale

    return MetricFrame(
        cohort=True,
        z=(value.raw - norm.median) / norm.scale,
        scan_z=value.z,
        p5=norm.p5,
        p25=norm.p25,
        p75=norm.p75,
        p95=norm.p95,
        signal_z=[in_cohort(s) for s in stat.signal_z],
        scan_range=ScanRange(
            p25=in_cohort(stat.p25),
            p75=in_cohort(stat.p75),
            # the scan's median is z = 0 in its own frame, by construction
            median=in_cohort(0.0),
        ),
    )
