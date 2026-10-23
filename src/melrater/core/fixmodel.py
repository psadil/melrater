"""FIX's reasons: how much each feature moved a pyFIX model's call.

Every model pyFIX ships is an XGBoost classifier, a boosted tree ensemble with a
logistic link, and for trees XGBoost computes Shapley values exactly (TreeSHAP;
Lundberg et al. 2020) through ``Booster.predict(..., pred_contribs=True)``. A
component's contributions are additive in log-odds. They start from the model's
baseline -- its average output over the data it was trained on -- and sum to
the log-odds whose logistic is the P(signal) pyFIX wrote into fix4melview.

That sum is what makes the result checkable. :func:`explain` refuses any set of
contributions that does not reproduce the probability FIX itself reported, and
it is the only guard needed against a wrong model file or a wrong column order:
either mistake moves a component's log-odds by far more than rounding can.

Django-free, like :mod:`melodic`. xgboost is in the ``render`` pixi feature and
not in the runtime image, so it is imported inside the functions that need it.
:func:`explains`, the check the server applies to a pushed payload, needs
nothing but arithmetic.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from melrater.core.melodic import MelodicSource

#: What pyFIX's ``classify`` drops before the table reaches the classifier: the
#: number of components and the image geometry. The models never saw them, in
#: training or since, so they are never an input.
PYFIX_DROPPED = frozenset(
    {"nic", *(f"dim:{i}" for i in range(4)), *(f"pixdim:{i}" for i in range(4))}
)

#: pyFIX names a model by its file's stem: ``UKBiobank.pyfix_model`` is the
#: ``UKBiobank`` of ``fix4melview_UKBiobank_thr1.txt``.
MODEL_SUFFIX = ".pyfix_model"

_HEADER = b"PYFIX\n"

#: Decimal places a stored contribution keeps. A ten-thousandth of a log-odds is
#: far below anything a strip can draw, and summed over every input of a model
#: the rounding stays well inside LOGIT_TOLERANCE.
DECIMALS = 4

#: How closely contributions must reproduce FIX's own probability. The log-odds
#: may differ by LOGIT_TOLERANCE: rounding DECIMALS over the ~177 inputs of a
#: built-in model moves the sum by under 0.009. Or the probabilities may differ
#: by PROBABILITY_TOLERANCE, which is the test that binds for a very small
#: P(signal): fix4melview prints six decimals, so below about 1e-4 the printed
#: value pins the log-odds down only loosely.
LOGIT_TOLERANCE = 0.03
PROBABILITY_TOLERANCE = 2e-6


class ModelError(ValueError):
    """A model file melrater cannot, or will not, explain."""


class ContributionsMismatch(ValueError):
    """Contributions that do not add up to the probability FIX reported."""


@dataclass(frozen=True)
class FixModel:
    name: str  # the file's stem, which is how fix4melview names the model
    booster: Any  # an xgboost.Booster; Any so that importing this needs no xgboost
    #: The feature list the file declares (pyFIX's format 0.5.0), or None for
    #: an older file, which was trained on every feature the table carries.
    features: tuple[str, ...] | None


@dataclass(frozen=True)
class FixContributions:
    """One model's reasons for each component of a run, in component order."""

    baseline: float  # log-odds before any feature: the model's training average
    #: Per component, feature -> log-odds. Only the features some tree splits
    #: on: every other one contributes exactly nothing, to every component.
    contributions: list[dict[str, float]]


def model_path(models_dir: Path, model: str) -> Path:
    return models_dir / f"{model}{MODEL_SUFFIX}"


def load_model(path: Path) -> FixModel:
    """Read a pyFIX model file (format 0.4.0 or later) holding an XGBoost model.

    Only XGBoost. A scikit-learn model in this format is a joblib pickle, and
    so is every file pyFIX wrote before 0.4.0. Unpickling runs whatever code
    the file names, and ingest has no business doing that with a file it was
    merely pointed at.
    """
    data = path.read_bytes()
    if not data.startswith(_HEADER):
        raise ModelError(f"{path.name} is not a pyFIX 0.4.0+ model file")
    meta = json.loads(data[len(_HEADER) :])
    model_type = meta.get("model_type")
    if model_type != "xgboost":
        raise ModelError(
            f"{path.name} holds a {model_type!r} model; only xgboost models "
            "can be explained"
        )
    import xgboost  # deferred: the render feature has it, the runtime image does not

    booster = xgboost.Booster()
    booster.load_model(bytearray(base64.b64decode(meta["model_contents"])))
    if booster.feature_names is not None:
        # pyFIX trains on a bare array, so its models never carry names, and
        # one that does was not trained the way model_columns assumes
        raise ModelError(f"{path.name} carries feature names; pyFIX models do not")
    features = meta.get("features")
    return FixModel(
        name=path.name.removesuffix(MODEL_SUFFIX),
        booster=booster,
        features=tuple(features) if features is not None else None,
    )


def model_columns(model: FixModel, names: Sequence[str]) -> list[str]:
    """The model's inputs, in the order pyFIX hands them to it.

    pyFIX keeps features.csv's own column order. It drops the columns outside
    a 0.5.0 model's list, then PYFIX_DROPPED (``pyfix.io.read_features``, then
    ``pyfix.classify.classify``). That is the order the model was trained and
    is run on, and so the only order whose contributions mean anything.
    """
    wanted = set(model.features) if model.features is not None else None
    columns = [
        name
        for name in names
        if name not in PYFIX_DROPPED and (wanted is None or name in wanted)
    ]
    expected = model.booster.num_features()
    if len(columns) != expected:
        raise ModelError(
            f"{model.name} takes {expected} features, and this table supplies "
            f"{len(columns)} of them"
        )
    return columns


def _split_features(booster: Any) -> list[int]:
    """Column positions that at least one tree splits on, in column order.

    "weight" counts each feature's splits, and a feature no tree splits on is
    simply absent from it. The keys are "f<position>" because pyFIX trains on
    an unnamed array (load_model refuses a named one).
    """
    scores = booster.get_score(importance_type="weight")
    return sorted(int(key.removeprefix("f")) for key in scores)


def explain(
    model: FixModel,
    names: Sequence[str],
    features: np.ndarray,
    probabilities: Sequence[float],
) -> FixContributions:
    """Each component's log-odds contributions, checked against FIX's own call.

    `features` is features.csv as read (components x `names`), and
    `probabilities` is the P(signal) fix4melview gives each component, in the
    same order. Raises ContributionsMismatch if a single component's
    contributions fail to reproduce its probability.
    """
    import xgboost  # deferred, as in load_model

    columns = model_columns(model, names)
    position = {name: j for j, name in enumerate(names)}
    table = np.asarray(features, dtype=float)
    inputs = table[:, [position[name] for name in columns]]
    shap = model.booster.predict(xgboost.DMatrix(inputs), pred_contribs=True)
    if shap.ndim != 2:
        raise ModelError(f"{model.name} is not a two-class model")
    split = _split_features(model.booster)
    # the last column is the bias term, the same for every row
    baseline = round(float(shap[0, -1]), DECIMALS)
    contributions = [
        {columns[j]: round(float(row[j]), DECIMALS) for j in split} for row in shap
    ]
    for i, (probability, row) in enumerate(
        zip(probabilities, contributions, strict=True)
    ):
        if not explains(probability, baseline, row):
            reached = logistic(baseline + math.fsum(row.values()))
            raise ContributionsMismatch(
                f"{model.name}: component {i + 1}'s contributions add up to "
                f"P(signal) {reached:.6f}, but FIX reported {probability:.6f}"
            )
    return FixContributions(baseline=baseline, contributions=contributions)


def explains(
    probability: float, baseline: float, contributions: Mapping[str, float]
) -> bool:
    """Whether these contributions add up to this probability (see the tolerances)."""
    logit = baseline + math.fsum(contributions.values())
    if abs(logistic(logit) - probability) <= PROBABILITY_TOLERANCE:
        return True
    if not 0.0 < probability < 1.0:
        return False
    return abs(logit - log_odds(probability)) <= LOGIT_TOLERANCE


def logistic(logit: float) -> float:
    """The probability a log-odds stands for, without overflow in either tail."""
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    odds = math.exp(logit)
    return odds / (1.0 + odds)


def log_odds(probability: float) -> float:
    """The log-odds of a probability strictly between 0 and 1."""
    return math.log(probability / (1.0 - probability))


class ModelShelf:
    """The pyFIX models in one directory, each read the first time a run names it.

    A failure is remembered as well, so a missing or unreadable model is
    reported once per run that names it rather than re-read for every one.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._loaded: dict[str, FixModel | ModelError] = {}

    def get(self, name: str) -> FixModel:
        if name not in self._loaded:
            path = model_path(self.directory, name)
            try:
                self._loaded[name] = load_model(path)
            except FileNotFoundError:
                self._loaded[name] = ModelError(f"no {path.name} in {self.directory}")
            except ModelError as exc:
                self._loaded[name] = exc
        loaded = self._loaded[name]
        if isinstance(loaded, ModelError):
            raise loaded
        return loaded


def explain_source(
    source: MelodicSource, shelf: ModelShelf
) -> tuple[MelodicSource, list[str]]:
    """`source` with each FIX result's reasons attached, and what could not be.

    A result whose model is missing or whose contributions do not add up keeps
    no reasons: the run still ingests, because FIX's verdict is still FIX's
    verdict. Each such result is described in the returned list so ingest can
    say so out loud.
    """
    problems: list[str] = []
    results = []
    for result in source.fix_results:
        contributions = None
        try:
            contributions = explain(
                shelf.get(result.model),
                source.feature_names,
                source.features,
                [verdict.p_signal for verdict in result.verdicts],
            )
        except (ModelError, ContributionsMismatch) as exc:
            problems.append(f"{result.reviewer_name}: {exc}")
        results.append(dataclasses.replace(result, contributions=contributions))
    return dataclasses.replace(source, fix_results=results), problems
