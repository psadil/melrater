from pathlib import Path

import numpy as np
import pytest

from melrater.core import melodic
from tests.conftest import FD_STEP, N_BINS, N_COMPONENTS, N_TIMEPOINTS, TR


def test_load_source_shapes_and_metadata(melodic_dir: Path) -> None:
    # Act
    source = melodic.load_source(melodic_dir)

    # Assert
    assert source.tr == pytest.approx(TR)
    assert source.label == melodic_dir.name
    assert source.mix.shape == (N_TIMEPOINTS, N_COMPONENTS)
    assert source.ftmix.shape == (N_BINS, N_COMPONENTS)
    assert source.n_components == N_COMPONENTS
    assert source.n_timepoints == N_TIMEPOINTS


def test_frequency_axis_ends_at_nyquist(melodic_dir: Path) -> None:
    # Act
    source = melodic.load_source(melodic_dir)

    # Assert: k/(2*n_bins*TR) for k=1..n_bins ends at 1/(2*TR)
    assert source.frequencies[-1] == pytest.approx(1 / (2 * TR))
    assert len(source.frequencies) == N_BINS


def test_load_fd_from_translation_ramp(melodic_dir: Path) -> None:
    # Act
    fd = melodic.load_fd(melodic_dir)

    # Assert: first frame is 0, every later frame equals the ramp step
    assert len(fd) == N_TIMEPOINTS
    assert fd[0] == 0.0
    assert np.allclose(fd[1:], FD_STEP)


def test_parse_fix_file(melodic_dir: Path) -> None:
    # Act
    result = melodic.parse_fix_file(melodic_dir / "fix4melview_TestModel_thr5.txt")

    # Assert
    assert result.model == "TestModel"
    assert result.threshold == 5
    assert result.reviewer_name == "TestModel @ thr5"
    assert [v.label for v in result.verdicts] == ["Signal", "Noise", "Noise"]
    assert result.verdicts[0].p_signal == pytest.approx(0.9)


def test_load_source_rejects_component_mismatch(melodic_dir: Path) -> None:
    # Arrange: drop one verdict row
    fix_file = melodic_dir / "fix4melview_TestModel_thr5.txt"
    lines = fix_file.read_text().splitlines()
    fix_file.write_text("\n".join(lines[:3] + [lines[4]]) + "\n")

    # Act / Assert
    with pytest.raises(ValueError, match="verdicts"):
        melodic.load_source(melodic_dir)
