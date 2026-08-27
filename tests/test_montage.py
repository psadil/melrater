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
    assert (picks[0], picks[-1]) == (2, 7)


def test_slice_rgb_zero_stat_shows_pure_background() -> None:
    # Arrange: mid-gray background, no statistic anywhere
    bg = np.full((4, 4), 100.0)
    ov = np.zeros((4, 4))

    # Act
    out = montage.slice_rgb(bg, ov, window=(0.0, 200.0))

    # Assert
    assert tuple(out[0, 0]) == (127, 127, 127)


def test_slice_rgb_suprathreshold_is_fully_opaque() -> None:
    # Arrange: |z| at the color saturation point
    bg = np.full((4, 4), 100.0)
    ov = np.full((4, 4), montage.OVERLAY_VMAX)

    # Act
    out = montage.slice_rgb(bg, ov, window=(0.0, 200.0))

    # Assert: pure yellow, no background bleed-through
    assert tuple(out[0, 0]) == (255, 255, 0)


def test_slice_rgb_subthreshold_blends_quadratically() -> None:
    # Arrange: |z| = 1.5 -> alpha = (1.5/3)^2 = 0.25, color t = 0.15
    bg = np.full((4, 4), 100.0)
    ov = np.full((4, 4), 1.5)

    # Act
    out = montage.slice_rgb(bg, ov, window=(0.0, 200.0))

    # Assert: 0.75*gray + 0.25*(1, 0.15, 0), scaled to bytes
    assert tuple(out[0, 0]) == (159, 105, 95)


def test_slice_rgb_nan_shows_pure_background() -> None:
    # Arrange: a NaN voxel (nonstandard derivatives mask with NaN)
    bg = np.full((4, 4), 100.0)
    ov = np.full((4, 4), np.nan)

    # Act
    out = montage.slice_rgb(bg, ov, window=(0.0, 200.0))

    # Assert
    assert tuple(out[0, 0]) == (127, 127, 127)


def test_slice_rgb_negative_uses_cool_colors() -> None:
    # Arrange
    bg = np.full((4, 4), 100.0)
    ov = np.full((4, 4), -montage.OVERLAY_VMAX)

    # Act
    out = montage.slice_rgb(bg, ov, window=(0.0, 200.0))

    # Assert: fully saturated light blue
    assert tuple(out[0, 0]) == (153, 255, 255)


def test_axis_picks_rejects_empty_coverage() -> None:
    # Arrange: a blob too small to reach MIN_SLICE_COVERAGE on any slice
    mask = np.zeros((20, 20, 20), dtype="float32")
    mask[:2, :2, :2] = 1.0

    # Act / Assert
    with pytest.raises(ValueError, match="mask coverage"):
        montage.axis_picks(mask, axis=0)
