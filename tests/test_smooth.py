"""The smoothed overlay: a Gaussian in millimetres, inside the mask, rescaled."""

import numpy as np
import pytest

from melrater.core import smooth


def test_the_default_kernel_is_the_papers() -> None:
    # Assert: Griffanti et al.'s 9.4 mm FWHM is a 4 mm sigma
    assert smooth.SMOOTH_FWHM_MM * smooth.FWHM_TO_SIGMA == pytest.approx(4.0, abs=0.01)


def test_gaussian_within_mask_keeps_a_plateau_at_the_mask_edge() -> None:
    # Arrange: a flat map filling the mask, which a plain filter would darken
    # at the edge by averaging in the zeros outside
    z = np.full((20, 20, 20), 5.0, dtype="float32")
    mask = np.ones_like(z)

    # Act
    out = smooth.gaussian_within_mask(z, mask, zooms_mm=(2.0, 2.0, 2.0), fwhm_mm=6.0)

    # Assert: normalised convolution — the edge voxel averages over its
    # inside neighbours only, so a motion ring at the brain edge survives
    assert out[0, 10, 10] == pytest.approx(5.0, abs=1e-3)


def test_gaussian_within_mask_is_zero_outside_the_mask() -> None:
    # Arrange
    z = np.full((20, 20, 20), 5.0, dtype="float32")
    mask = np.ones_like(z)
    mask[10:] = 0.0

    # Act
    out = smooth.gaussian_within_mask(z, mask, zooms_mm=(2.0, 2.0, 2.0), fwhm_mm=6.0)

    # Assert
    assert out[15, 10, 10] == 0.0


def test_gaussian_within_mask_is_isotropic_in_millimetres() -> None:
    # Arrange: a point source in a volume whose middle axis has voxels four
    # times as long as the others
    z = np.zeros((21, 21, 21), dtype="float32")
    z[10, 10, 10] = 100.0
    mask = np.ones_like(z)

    # Act
    out = smooth.gaussian_within_mask(z, mask, zooms_mm=(1.0, 4.0, 1.0), fwhm_mm=6.0)

    # Assert: two voxels along the coarse axis is 8 mm, along a fine one 2 mm,
    # so the spread reaches further in voxels along the fine axis
    assert out[12, 10, 10] > out[10, 12, 10]


def test_smooth_zmap_rescales_the_bulk_to_unit_robust_sd() -> None:
    # Arrange: a noise field, which smoothing shrinks well below unit scale
    rng = np.random.default_rng(11)
    z = rng.normal(size=(24, 24, 24)).astype("float32")
    mask = np.ones_like(z)

    # Act
    out = smooth.smooth_zmap(z, mask, zooms_mm=(2.4, 2.4, 2.4))

    # Assert: the same opacity ramp then applies to the smoothed map
    assert smooth.robust_sd(out[mask > 0]) == pytest.approx(1.0, rel=1e-5)


def test_smooth_zmap_of_a_flat_map_is_zero_not_nan() -> None:
    # Arrange: nothing to rescale by
    z = np.zeros((8, 8, 8), dtype="float32")
    mask = np.ones_like(z)

    # Act
    out = smooth.smooth_zmap(z, mask, zooms_mm=(2.0, 2.0, 2.0))

    # Assert
    assert np.array_equal(out, np.zeros_like(z))
