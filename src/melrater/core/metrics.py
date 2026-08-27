"""Robust z-scoring of pyFIX features across a run's components.

Each feature is z-scored across components with z = (x - median)/(1.4826*MAD)
(std fallback when MAD is 0; constant features are dropped), so any one
component's value can be shown against the run-wide distribution.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from melrater.core.schemas import MetricStat, MetricStats, MetricValue

OUTLIER_Z = 3.0  # |robust z| above which a metric counts as an outlier


@dataclass(frozen=True)
class MetricTable:
    names: list[str]  # kept feature names
    raw: np.ndarray  # (n_components, n_kept)
    z: np.ndarray  # (n_components, n_kept), unclipped
    dropped: list[str]  # constant features excluded from z-scoring


def build_metric_table(names: list[str], raw: np.ndarray) -> MetricTable:
    med = np.median(raw, axis=0)
    mad = np.median(np.abs(raw - med), axis=0)
    scale = 1.4826 * mad
    std = raw.std(axis=0)
    scale = np.where(scale > 0, scale, std)
    keep = scale > 0
    z = (raw[:, keep] - med[keep]) / scale[keep]
    return MetricTable(
        names=[n for n, k in zip(names, keep) if k],
        raw=raw[:, keep],
        z=z,
        dropped=[n for n, k in zip(names, keep) if not k],
    )


def family_of(name: str) -> str:
    return name.split(":")[0]


def compute_metric_stats(table: MetricTable, signal_rows: list[int]) -> MetricStats:
    """Per-metric distribution bands and the robust-z positions of the
    FIX-labeled Signal components (the glyphs' green ticks)."""
    p5, p25, p75, p95 = np.percentile(table.z, (5, 25, 75, 95), axis=0)
    return MetricStats(
        names=table.names,
        dropped=table.dropped,
        stats={
            name: MetricStat(
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
