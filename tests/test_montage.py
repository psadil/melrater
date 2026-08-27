import numpy as np
import pytest

from melrater.core import montage


def test_axis_picks_spans_mask_coverage() -> None:
    # Arrange: mask occupies slices 2..7 along z
    mask = np.zeros((10, 10, 10), dtype="float32")
    mask[:, :, 2:8] = 1.0

    # Act
    picks = montage.axis_picks(mask, axis=2, n=4)

    # Assert
    assert picks[0] == 2
    assert picks[-1] == 7


def test_axis_picks_rejects_empty_coverage() -> None:
    # Arrange: a blob too small to reach MIN_SLICE_COVERAGE on any slice
    mask = np.zeros((20, 20, 20), dtype="float32")
    mask[:2, :2, :2] = 1.0

    # Act / Assert
    with pytest.raises(ValueError, match="mask coverage"):
        montage.axis_picks(mask, axis=0)
