"""FIX's reasons: reading pyFIX models and explaining their calls exactly.

Database-free. The model is the small one `fix_models_dir` trains on the
fixture's four features, and the probabilities are the ones
`explained_melodic_dir` wrote from its predictions, which is what pyFIX does.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from melrater.core import fixmodel, melodic
from tests.conftest import FIX_MODEL, run_inputs, write_pyfix_model


@pytest.fixture
def model(fix_models_dir: Path) -> fixmodel.FixModel:
    return fixmodel.load_model(fix_models_dir / f"{FIX_MODEL}.pyfix_model")


@pytest.fixture
def table(explained_melodic_dir: Path) -> tuple[list[str], np.ndarray]:
    return melodic.load_features(explained_melodic_dir / "fix" / "features.csv")


@pytest.fixture
def probabilities(explained_melodic_dir: Path) -> list[float]:
    result = melodic.parse_fix_file(
        explained_melodic_dir / f"fix4melview_{FIX_MODEL}_thr5.txt"
    )
    return [verdict.p_signal for verdict in result.verdicts]


@pytest.fixture
def explained(model, table, probabilities) -> fixmodel.FixContributions:
    names, features = table
    return fixmodel.explain(model, names, features, probabilities)


# --- reading a model file -------------------------------------------------


def test_load_model_names_the_model_after_its_file(model) -> None:
    assert model.name == FIX_MODEL


def test_load_model_reads_no_feature_list_from_a_0_4_0_file(model) -> None:
    assert model.features is None


def test_load_model_reads_the_feature_list_of_a_0_5_0_file(
    model, tmp_path: Path
) -> None:
    # Arrange
    path = write_pyfix_model(
        tmp_path / "Listed.pyfix_model",
        model.booster,
        features=["featA", "featB", "const", "featC"],
    )

    # Act
    listed = fixmodel.load_model(path)

    # Assert
    assert listed.features == ("featA", "featB", "const", "featC")


def test_load_model_refuses_an_sklearn_model(model, tmp_path: Path) -> None:
    # Arrange: in pyFIX's format, a scikit-learn model is a joblib pickle
    path = write_pyfix_model(
        tmp_path / "Forest.pyfix_model", model.booster, model_type="sklearn"
    )

    # Act / Assert
    with pytest.raises(fixmodel.ModelError, match="only xgboost"):
        fixmodel.load_model(path)


def test_load_model_refuses_a_file_from_before_pyfix_0_4_0(tmp_path: Path) -> None:
    # Arrange: those were bare joblib pickles, with no PYFIX header
    path = tmp_path / "Old.pyfix_model"
    path.write_bytes(b"\x80\x04\x95 not a header")

    # Act / Assert
    with pytest.raises(fixmodel.ModelError, match="not a pyFIX"):
        fixmodel.load_model(path)


def test_importing_fixmodel_leaves_xgboost_unimported() -> None:
    # Arrange: in a fresh interpreter, since this one has long since loaded it
    probe = "import sys, melrater.core.fixmodel; print('xgboost' in sys.modules)"

    # Act
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    # Assert: the server imports this module and has no xgboost at all
    assert out.stdout.strip() == "False"


# --- the columns a model takes --------------------------------------------


def test_model_columns_leave_out_what_pyfix_drops(model) -> None:
    # Act
    columns = fixmodel.model_columns(
        model, ["nic", "featA", "featB", "dim:0", "const", "featC", "pixdim:3"]
    )

    # Assert
    assert columns == ["featA", "featB", "const", "featC"]


def test_model_columns_keep_only_a_0_5_0_models_features(model) -> None:
    # Arrange
    listed = fixmodel.FixModel(
        name="Listed",
        booster=model.booster,
        features=("featA", "featB", "const", "featC"),
    )

    # Act
    columns = fixmodel.model_columns(
        listed, ["featA", "featB", "extra", "const", "featC"]
    )

    # Assert
    assert columns == ["featA", "featB", "const", "featC"]


def test_model_columns_refuse_a_table_of_the_wrong_width(model) -> None:
    # Act / Assert
    with pytest.raises(fixmodel.ModelError, match="takes 4 features"):
        fixmodel.model_columns(model, ["featA", "featB"])


# --- explaining its calls -------------------------------------------------


def test_explain_reproduces_fixs_probabilities(
    explained: fixmodel.FixContributions, probabilities
) -> None:
    # Act
    reached = [
        fixmodel.logistic(explained.baseline + sum(row.values()))
        for row in explained.contributions
    ]

    # Assert
    assert reached == pytest.approx(probabilities, abs=1e-4)


def test_explain_adds_up_to_the_models_own_log_odds(
    explained: fixmodel.FixContributions, model, table
) -> None:
    # Arrange
    import xgboost

    margin = model.booster.predict(xgboost.DMatrix(table[1]), output_margin=True)

    # Act
    summed = [explained.baseline + sum(row.values()) for row in explained.contributions]

    # Assert
    assert summed == pytest.approx(margin.tolist(), abs=1e-3)


def test_explain_keeps_only_features_some_tree_splits_on(
    explained: fixmodel.FixContributions,
) -> None:
    # Assert: no tree can split on a constant column
    assert "const" not in explained.contributions[0]


def test_explain_rounds_what_it_keeps(explained: fixmodel.FixContributions) -> None:
    # Act
    row = explained.contributions[0]

    # Assert
    assert all(round(v, fixmodel.DECIMALS) == v for v in row.values())


def test_explain_refuses_columns_in_the_wrong_order(
    model, table, probabilities
) -> None:
    # Arrange: featA and featC swapped in the table but not in its header
    names, features = table
    swapped = features[:, [3, 1, 2, 0]]

    # Act / Assert
    with pytest.raises(fixmodel.ContributionsMismatch, match="FIX reported"):
        fixmodel.explain(model, names, swapped, probabilities)


def test_explain_refuses_a_probability_the_model_did_not_give(
    model, table, probabilities
) -> None:
    # Arrange: what a different model, under the same name, would have said
    names, features = table
    other = [0.5, *probabilities[1:]]

    # Act / Assert
    with pytest.raises(fixmodel.ContributionsMismatch):
        fixmodel.explain(model, names, features, other)


# --- the check both ends apply --------------------------------------------


def test_explains_allows_the_rounding_of_stored_contributions() -> None:
    # Act: 0.005 log-odds off at P = 0.5
    ok = fixmodel.explains(0.5, 0.0, {"featA": 0.005})

    # Assert
    assert ok is True


def test_explains_refuses_a_tenth_of_a_log_odds() -> None:
    # Act
    ok = fixmodel.explains(0.5, 0.0, {"featA": 0.1})

    # Assert
    assert ok is False


def test_explains_allows_a_tiny_probability_its_print_precision() -> None:
    # Arrange: fix4melview prints six decimals, so a true P of 3.4e-6 reads
    # 0.000003, which is 0.125 off in log-odds
    logit = fixmodel.log_odds(3.4e-6)

    # Act
    ok = fixmodel.explains(0.000003, logit, {})

    # Assert
    assert ok is True


def test_explains_refuses_even_odds_for_a_printed_zero() -> None:
    # Act: 0.000000 has no log-odds to compare against, only a probability
    ok = fixmodel.explains(0.0, 0.0, {})

    # Assert
    assert ok is False


# --- a whole run --------------------------------------------------------


def test_model_shelf_says_which_model_is_missing(tmp_path: Path) -> None:
    # Arrange
    shelf = fixmodel.ModelShelf(tmp_path)

    # Act / Assert
    with pytest.raises(fixmodel.ModelError, match="no Nowhere.pyfix_model"):
        shelf.get("Nowhere")


def test_explain_source_attaches_the_reasons(
    explained_melodic_dir: Path, fix_models_dir: Path
) -> None:
    # Arrange
    source = melodic.load_run(run_inputs(explained_melodic_dir))

    # Act
    explained, _ = fixmodel.explain_source(source, fixmodel.ModelShelf(fix_models_dir))

    # Assert
    assert explained.fix_results[0].contributions is not None


def test_explain_source_reports_a_model_it_cannot_find(
    explained_melodic_dir: Path, tmp_path: Path
) -> None:
    # Arrange: an empty model directory
    source = melodic.load_run(run_inputs(explained_melodic_dir))
    empty = tmp_path / "empty"
    empty.mkdir()

    # Act
    _, problems = fixmodel.explain_source(source, fixmodel.ModelShelf(empty))

    # Assert
    assert problems == [f"{FIX_MODEL} @ thr5: no {FIX_MODEL}.pyfix_model in {empty}"]


def test_explain_source_keeps_the_verdict_it_cannot_explain(
    explained_melodic_dir: Path, tmp_path: Path
) -> None:
    # Arrange
    source = melodic.load_run(run_inputs(explained_melodic_dir))
    empty = tmp_path / "empty"
    empty.mkdir()

    # Act
    explained, _ = fixmodel.explain_source(source, fixmodel.ModelShelf(empty))

    # Assert: FIX's call is still FIX's call
    assert explained.fix_results[0].verdicts == source.fix_results[0].verdicts
