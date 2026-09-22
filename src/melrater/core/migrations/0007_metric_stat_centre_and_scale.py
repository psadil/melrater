"""Give every stored metric band the raw centre and scale it was measured on.

`Run.metric_stats` holds each metric's distribution in that run's own robust-z
units, and until now nothing recorded what those units were — so a band could
be drawn in the frame it was computed in and in no other. The cohort frame
needs exactly that: a way back to raw values.

Both numbers are recoverable, and exactly: they are the median and
1.4826*MAD (std where MAD is 0) of the raw values the components already
store, which is what `metrics.build_metric_table` computed at ingest. The
formula is spelled out here rather than imported, as a data migration's
always must be: it has to keep meaning what it meant today when
`metrics.py` has moved on.

A full pass over every component, so at campaign scale expect minutes rather
than seconds.
"""

import numpy as np
from django.db import migrations


def add_centre_and_scale(apps, schema_editor) -> None:
    Run = apps.get_model("core", "Run")
    Component = apps.get_model("core", "Component")
    for run in Run.objects.iterator():
        stored = run.metric_stats or {}
        bands = stored.get("stats") or {}
        if not bands or all("scale" in band for band in bands.values()):
            continue
        raw: dict[str, list[float]] = {}
        for metrics in Component.objects.filter(run=run).values_list(
            "metrics", flat=True
        ):
            for name, value in metrics.items():
                raw.setdefault(name, []).append(value["raw"])
        for name, band in bands.items():
            band["median"], band["scale"] = _centre_and_scale(raw.get(name, []))
        run.metric_stats = stored
        run.save(update_fields=("metric_stats",))


def _centre_and_scale(values: list[float]) -> tuple[float, float]:
    """`build_metric_table`'s robust centre and scale, as it computed them.

    (0, 1) for a metric with bands but no stored values — which needs a
    component row to have gone missing under a run whose stats survived. That
    pair leaves the bands exactly where they are drawn today and declines to
    invent a raw scale for them.
    """
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return 0.0, 1.0
    median = float(np.median(v))
    scale = 1.4826 * float(np.median(np.abs(v - median)))
    if scale <= 0:
        scale = float(v.std())
    return median, scale if scale > 0 else 1.0


class Migration(migrations.Migration):
    dependencies = (("core", "0006_metriccohort_component_motion_r2"),)

    # Reversing leaves the two keys in place: nothing reads a key it does not
    # know about, and stripping them would mean another full pass to undo a
    # change that costs a reverted deployment nothing.
    operations = (
        migrations.RunPython(add_centre_and_scale, migrations.RunPython.noop),
    )
