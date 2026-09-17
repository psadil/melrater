import dataclasses
from pathlib import Path

import numpy as np
import pytest

from melrater.core import melodic
from tests.conftest import (
    FD_STEP,
    N_BINS,
    N_COMPONENTS,
    N_TIMEPOINTS,
    TR,
    anat_run_inputs,
    run_inputs,
)


@pytest.fixture
def source(melodic_dir: Path) -> melodic.MelodicSource:
    return melodic.load_run(run_inputs(melodic_dir))


def test_load_run_reads_tr(source: melodic.MelodicSource) -> None:
    assert source.tr == pytest.approx(TR)


def test_load_run_labels_run_after_directory(
    source: melodic.MelodicSource, melodic_dir: Path
) -> None:
    assert source.label == melodic_dir.name


def test_load_run_mix_shape(source: melodic.MelodicSource) -> None:
    assert source.mix.shape == (N_TIMEPOINTS, N_COMPONENTS)


def test_load_run_ftmix_shape(source: melodic.MelodicSource) -> None:
    assert source.ftmix.shape == (N_BINS, N_COMPONENTS)


def test_load_run_counts_components(source: melodic.MelodicSource) -> None:
    assert source.n_components == N_COMPONENTS


def test_frequency_axis_ends_at_nyquist(source: melodic.MelodicSource) -> None:
    # k/(2*n_bins*TR) for k=1..n_bins ends at 1/(2*TR)
    assert source.frequencies[-1] == pytest.approx(1 / (2 * TR))


def test_fd_has_one_value_per_timepoint(melodic_dir: Path) -> None:
    # Arrange
    params = np.loadtxt(melodic_dir / "mc" / "prefiltered_func_data_mcf.par")

    # Act
    fd = melodic.fd_power(params)

    # Assert
    assert len(fd) == N_TIMEPOINTS


def test_fd_starts_at_zero(melodic_dir: Path) -> None:
    params = np.loadtxt(melodic_dir / "mc" / "prefiltered_func_data_mcf.par")

    fd = melodic.fd_power(params)

    assert fd[0] == 0.0


def test_fd_equals_translation_ramp_step(melodic_dir: Path) -> None:
    params = np.loadtxt(melodic_dir / "mc" / "prefiltered_func_data_mcf.par")

    fd = melodic.fd_power(params)

    assert np.allclose(fd[1:], FD_STEP)


def test_parse_fix_file_identifies_reviewer(melodic_dir: Path) -> None:
    # Act
    result = melodic.parse_fix_file(melodic_dir / "fix4melview_TestModel_thr5.txt")

    # Assert
    assert (result.model, result.threshold, result.reviewer_name) == (
        "TestModel",
        5,
        "TestModel @ thr5",
    )


def test_parse_fix_file_reads_labels_in_order(melodic_dir: Path) -> None:
    # Act
    result = melodic.parse_fix_file(melodic_dir / "fix4melview_TestModel_thr5.txt")

    # Assert
    assert [v.label for v in result.verdicts] == ["Signal", "Noise", "Noise"]


def test_parse_fix_file_reads_probability(melodic_dir: Path) -> None:
    # Act
    result = melodic.parse_fix_file(melodic_dir / "fix4melview_TestModel_thr5.txt")

    # Assert
    assert result.verdicts[0].p_signal == pytest.approx(0.9)


def test_parse_fix_file_rejects_renumbered_lines(melodic_dir: Path) -> None:
    # Arrange: swap the component numbers of the two Noise lines
    fix_file = melodic_dir / "fix4melview_TestModel_thr5.txt"
    fix_file.write_text(
        "filtered_func_data.ica\n"
        "1, Signal, False, 0.9\n"
        "3, Noise, True, 0.002\n"
        "2, Noise, True, 0.01\n"
        "[2, 3]\n"
    )

    # Act / Assert
    with pytest.raises(ValueError, match="positionally"):
        melodic.parse_fix_file(fix_file)


def test_load_run_rejects_component_mismatch(melodic_dir: Path) -> None:
    # Arrange: drop the last verdict row
    fix_file = melodic_dir / "fix4melview_TestModel_thr5.txt"
    lines = fix_file.read_text().splitlines()
    fix_file.write_text("\n".join(lines[:3] + [lines[4]]) + "\n")

    # Act / Assert
    with pytest.raises(ValueError, match="verdicts"):
        melodic.load_run(run_inputs(melodic_dir))


def test_load_run_rejects_motion_volume_mismatch(melodic_dir: Path) -> None:
    # Arrange: one motion row too few for the mix's volumes
    inputs = run_inputs(melodic_dir)
    short = dataclasses.replace(inputs, motion=inputs.motion[:-1])

    # Act / Assert
    with pytest.raises(ValueError, match="motion rows"):
        melodic.load_run(short)


def test_load_run_rejects_icstats_component_mismatch(melodic_dir: Path) -> None:
    # Arrange: one ICstats row too few for the mix's components
    inputs = run_inputs(melodic_dir)
    short = dataclasses.replace(inputs, icstats=inputs.icstats[:-1])

    # Act / Assert
    with pytest.raises(ValueError, match="component counts"):
        melodic.load_run(short)


def test_fix_file_with_an_unknown_label_is_refused(tmp_path) -> None:
    # Arrange: SQLite does not enforce max_length, so an unexpected token in
    # column two would otherwise be stored verbatim as a Classification label
    path = tmp_path / "fix4melview_M_thr5.txt"
    path.write_text("ica\n1, Maybe, False, 0.9\n")

    # Act / Assert
    with pytest.raises(ValueError, match="Maybe"):
        melodic.parse_fix_file(path)


def test_a_registration_without_its_transform_is_not_used(
    anat_melodic_dir: Path,
) -> None:
    # Arrange: the structural resolved, the .mat did not
    inputs = dataclasses.replace(anat_run_inputs(anat_melodic_dir), highres2func=None)

    # Act
    source = melodic.load_run(inputs)

    # Assert: both or neither — an anatomical with no way onto the functional
    # grid is not a background, and the pairing is enforced in one place
    assert source.anat_path is None
