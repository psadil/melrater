"""Give every FIX verdict already in the database its reasons, once.

Rows ingested before contributions existed carry FIX's probability and
nothing to explain it. Here the reasons are recomputed from the model and the
run's own features.csv. They are kept only where they add up to the
probability already stored, and that check is what ties them to the call FIX
actually made.

It can only compute where `xgboost` imports (the render and dev pixi
environments), MELRATER_FIX_MODELS names pyFIX's trained-model directory, and
the runs' derivative files still sit at `Run.path` -- that is, in a source
checkout. A deployment has none of those by design, so there it says so and
returns. The rows reach it afterwards as a fixture: see the README's migration
note.

A source checkout that cannot compute refuses instead, and names the command
to run. Otherwise a `migrate` in the default environment would record this as
applied with nothing filled, and nothing would ever fill it after that. The
reverse is a no-op, so migrating back to 0008 and forward again re-runs it.

Spelled out rather than imported from `fixmodel`, as 0007 spells out its
formula: a data migration has to keep meaning what it means today after the
module has moved on.
"""

import base64
import csv
import json
import math
import os
from pathlib import Path
from typing import Any

from django.db import migrations

#: What pyFIX's classify() drops before the table reaches the classifier.
PYFIX_DROPPED = frozenset(
    {"nic", *(f"dim:{i}" for i in range(4)), *(f"pixdim:{i}" for i in range(4))}
)
DECIMALS = 4
LOGIT_TOLERANCE = 0.03
PROBABILITY_TOLERANCE = 2e-6


def _logistic(logit: float) -> float:
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    odds = math.exp(logit)
    return odds / (1.0 + odds)


def _adds_up(probability: float, baseline: float, contributions: dict) -> bool:
    logit = baseline + math.fsum(contributions.values())
    if abs(_logistic(logit) - probability) <= PROBABILITY_TOLERANCE:
        return True
    if not 0.0 < probability < 1.0:
        return False
    stored = math.log(probability / (1.0 - probability))
    return abs(logit - stored) <= LOGIT_TOLERANCE


def _load_model(path: Path):
    """An xgboost Booster and its feature list, from a pyFIX 0.4.0+ file."""
    import xgboost

    data = path.read_bytes()
    if not data.startswith(b"PYFIX\n"):
        raise ValueError(f"{path.name} is not a pyFIX 0.4.0+ model file")
    meta = json.loads(data[6:])
    if meta.get("model_type") != "xgboost":
        # anything else is a pickle, and unpickling runs code
        raise ValueError(f"{path.name} does not hold an xgboost model")
    booster = xgboost.Booster()
    booster.load_model(bytearray(base64.b64decode(meta["model_contents"])))
    if booster.feature_names is not None:
        raise ValueError(f"{path.name} carries feature names; pyFIX models do not")
    features = meta.get("features")
    return booster, (frozenset(features) if features is not None else None)


def _contributions(booster, wanted, path: Path) -> tuple[float, list[dict]]:
    """Every component's contributions, from one run's features.csv.

    pyFIX's own column order: the file's, less the columns outside a 0.5.0
    model's list, less PYFIX_DROPPED. Only the features some tree splits on are
    kept, since every other one contributes exactly nothing.
    """
    import numpy as np
    import xgboost

    with open(path) as handle:
        reader = csv.reader(handle)
        names = next(reader)
        table = np.asarray([[float(v) for v in row] for row in reader if row])
    columns = [
        name
        for name in names
        if name not in PYFIX_DROPPED and (wanted is None or name in wanted)
    ]
    if len(columns) != booster.num_features():
        raise ValueError(
            f"the model takes {booster.num_features()} features and "
            f"{path.name} supplies {len(columns)}"
        )
    position = {name: j for j, name in enumerate(names)}
    inputs = table[:, [position[name] for name in columns]]
    shap = booster.predict(xgboost.DMatrix(inputs), pred_contribs=True)
    split = sorted(
        int(key.removeprefix("f"))
        for key in booster.get_score(importance_type="weight")
    )
    baseline = round(float(shap[0, -1]), DECIMALS)
    rows = [{columns[j]: round(float(row[j]), DECIMALS) for j in split} for row in shap]
    return baseline, rows


def fill_contributions(apps, schema_editor) -> None:
    Classification = apps.get_model("core", "Classification")
    Run = apps.get_model("core", "Run")
    pending = Classification.objects.filter(
        reviewer__kind="fix", contributions__isnull=True
    )
    n_pending = pending.count()
    if not n_pending:
        return

    models_dir = os.environ.get("MELRATER_FIX_MODELS", "")
    try:
        import xgboost  # noqa: F401
    except ImportError:
        missing = "xgboost (the render or dev pixi environment)"
    else:
        missing = "" if models_dir else "MELRATER_FIX_MODELS"
    if missing:
        message = (
            f"0009: {n_pending} FIX verdicts have no reasons yet, and computing "
            f"them needs {missing}."
        )
        if os.environ.get("MELRATER_DEV"):
            raise RuntimeError(
                f"{message} Run `MELRATER_FIX_MODELS=<pyfix>/resources/models "
                "pixi run -e render manage migrate`."
            )
        print(f"{message} Skipped here: load them as a fixture (README).")
        return

    directory = Path(models_dir).expanduser()
    models: dict[str, tuple[Any, frozenset[str] | None] | Exception] = {}
    filled: list = []
    skipped: list[str] = []
    run_ids = list(pending.order_by().values_list("component__run_id", flat=True))
    for run in Run.objects.filter(pk__in=set(run_ids)).order_by("label"):
        rows = list(
            pending.filter(component__run=run).select_related("reviewer", "component")
        )
        by_model: dict[str, list] = {}
        for row in rows:
            by_model.setdefault(str(row.reviewer.fix_model), []).append(row)
        for model, model_rows in by_model.items():
            try:
                if model not in models:
                    try:
                        models[model] = _load_model(directory / f"{model}.pyfix_model")
                    except (OSError, ValueError) as exc:
                        models[model] = exc
                loaded = models[model]
                if isinstance(loaded, Exception):
                    raise loaded
                booster, wanted = loaded
                baseline, explained = _contributions(
                    booster, wanted, Path(run.path) / "fix" / "features.csv"
                )
                if len(explained) != run.components.count():
                    raise ValueError(
                        f"features.csv has {len(explained)} rows for "
                        f"{run.components.count()} components"
                    )
                for row in model_rows:
                    contributions = explained[row.component.index - 1]
                    if row.probability is None or not _adds_up(
                        row.probability, baseline, contributions
                    ):
                        raise ValueError(
                            f"component {row.component.index}'s reasons do not "
                            "add up to the stored P(signal)"
                        )
            except (OSError, ValueError) as exc:
                skipped.append(f"{run.label} ({model}): {exc}")
                continue
            for row in model_rows:
                row.baseline = baseline
                row.contributions = explained[row.component.index - 1]
            filled.extend(model_rows)
    Classification.objects.bulk_update(
        filled, ("baseline", "contributions"), batch_size=500
    )
    n_runs = len({row.component.run_id for row in filled})
    print(f"0009: filled {len(filled)} FIX verdicts across {n_runs} runs")
    for reason in skipped:
        print(f"0009: skipped {reason}")


class Migration(migrations.Migration):
    dependencies = (("core", "0008_fix_contributions"),)

    operations = (migrations.RunPython(fill_contributions, migrations.RunPython.noop),)
