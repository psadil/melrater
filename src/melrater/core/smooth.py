"""Spatial smoothing of an IC z-map, for the montage's ``smooth`` variant.

Griffanti et al. (2017, doi:10.1016/j.neuroimage.2016.12.036) recommend, for
data that were not smoothed before ICA, "an additional version of the ICs that
have been spatially smoothed (after generation by the ICA processing) for
display/identification purposes only": an unsmoothed map carries many small
scattered clusters whether or not the component is noise, and the smoothed map
is where "a low number of large clusters" — or its absence — can be read off.
The unsmoothed map stays, because it is where a multiband checkerboard, an
alternating-sign stripe and the exact voxel a peak sits in are visible.

Kept out of :mod:`montage` for the same reason as :mod:`resample`: this needs
scipy, which lives in the ``render`` pixi feature rather than in the runtime
image. It is imported lazily inside the render worker, so it must import
neither Django nor anything that does.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy import ndimage

#: The kernel Griffanti et al. applied to IC maps for display (their Fig. 3:
#: "a smoothing of FWHM = 9.4 mm"), i.e. sigma = 4 mm. A render constant like
#: Z_THRESH rather than a setting: changing it is a re-render, and every run of
#: a campaign should be smoothed alike.
SMOOTH_FWHM_MM = 9.4

#: sigma = FWHM / (2 * sqrt(2 * ln 2))
FWHM_TO_SIGMA = 1.0 / (2.0 * np.sqrt(2.0 * np.log(2.0)))

#: Scales a median absolute deviation to the SD of a normal distribution.
MAD_TO_SD = 1.4826


def gaussian_within_mask(
    z: np.ndarray,
    mask: np.ndarray,
    zooms_mm: Sequence[float] | np.ndarray,
    fwhm_mm: float = SMOOTH_FWHM_MM,
) -> np.ndarray:
    """Gaussian-smooth ``z`` inside ``mask`` only, with the kernel in millimetres.

    Normalised convolution: the map and the mask are smoothed alike and the
    quotient taken, so a voxel at the brain edge averages over its inside
    neighbours rather than over the zeros outside. That is what keeps a motion
    ring — which lives at the edge — from being damped away. Outside the mask
    the result is zero. ``zooms_mm`` are the voxel sizes of ``z``'s own axes,
    so the kernel is isotropic in millimetres whatever the voxels are.
    """
    inside = np.asarray(mask) > 0
    sigma = fwhm_mm * FWHM_TO_SIGMA / np.asarray(zooms_mm, dtype=float)
    values = np.where(inside, np.nan_to_num(np.asarray(z, dtype=np.float32)), 0.0)
    weights = inside.astype(np.float32)
    num = ndimage.gaussian_filter(values, sigma, mode="constant")
    den = ndimage.gaussian_filter(weights, sigma, mode="constant")
    out = np.divide(num, den, out=np.zeros_like(num), where=den > 0)
    out[~inside] = 0.0
    return out.astype(np.float32)


def robust_sd(values: np.ndarray) -> float:
    """1.4826 x the median absolute deviation: the SD of the bulk, tails ignored."""
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return 0.0
    return float(MAD_TO_SD * np.median(np.abs(v - np.median(v))))


def rescale_robust(s: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Divide by the in-mask robust SD, so the bulk of the map is unit scale again.

    Smoothing shrinks everything: after the 9.4 mm kernel a signal component's
    peak is |z| 3-5 (measured on real data) and the montage is nearly blank on
    the shared colour scale. Rescaling by the robust SD of what is left puts
    the bulk back at unit scale, so the same opacity ramp applies and what
    stands out is what is coherent at the kernel's scale. The scale is per
    component, which is why the caption says to read cluster count and size
    rather than absolute colour. A map with no spread stays zero.
    """
    inside = np.asarray(mask) > 0
    scale = robust_sd(np.asarray(s)[inside])
    if scale == 0.0:
        return np.zeros(np.shape(s), dtype=np.float32)
    return (np.asarray(s, dtype=np.float32) / scale).astype(np.float32)


def smooth_zmap(
    z: np.ndarray,
    mask: np.ndarray,
    zooms_mm: Sequence[float] | np.ndarray,
    fwhm_mm: float = SMOOTH_FWHM_MM,
) -> np.ndarray:
    """The montage's ``smooth`` overlay: smoothed within the mask, then rescaled."""
    return rescale_robust(gaussian_within_mask(z, mask, zooms_mm, fwhm_mm), mask)
