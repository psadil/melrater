"""Sampling the anatomical background onto the montage's own pixel grid.

A FLIRT ``.mat`` does *not* map between two images' world (qform/sform)
frames. It maps between FSL's *scaled-millimetre* frames: voxel indices times
voxel sizes, with x flipped for an image stored neurologically. Turning one
into a world-space transform means sandwiching it between the two images'
scaled frames, which is what :func:`flirt_world` does and is the only subtle
thing in this module.

The anatomical is sampled only at the pixel centres of the 75 cells a montage
actually shows (25 slices x 3 axes), never as a volume: those planes are the
same for every component of the run, and sampling them directly means the
through-plane coordinate is the pick's own index, so there is no "which fine
slice" rule to get wrong.

Kept out of :mod:`montage` on purpose. This runs once in the parent process and
is the one module the deployment does not have the dependencies for — it needs
scipy, which lives in the ``render`` pixi feature rather than in the runtime
image, so :mod:`services` imports this lazily.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy import ndimage

from melrater.core import montage

#: Ceiling on how finely the anatomical may subdivide the functional voxel
#: grid. Without it a 0.7 mm T1 under a 4 mm functional would mint montages
#: several times the intended size for detail no reviewer asked for.
MAX_FACTOR = 3.0


class RegistrationError(Exception):
    """A registration that is present but unusable.

    Distinct from a registration that is simply absent: a run without one
    renders the functional background alone, whereas this is a corrupt input
    and is reported like any other.
    """


def fsl_scale(img: nib.nifti1.Nifti1Image) -> np.ndarray:
    """One image's FSL scaled-millimetre frame: scaled mm <- voxel index.

    ``diag(zooms)``, except that FSL flips x for an image whose affine has a
    positive determinant ("neurological" storage), which is the detail that
    makes a hand-composed FLIRT transform either right or silently mirrored.
    """
    zx, zy, zz = (float(z) for z in img.header.get_zooms()[:3])
    if np.linalg.det(img.affine) > 0:
        nx = img.shape[0]
        return np.array(
            [
                [-zx, 0.0, 0.0, (nx - 1) * zx],
                [0.0, zy, 0.0, 0.0],
                [0.0, 0.0, zz, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
    return np.diag([zx, zy, zz, 1.0])


def flirt_world(
    mat: np.ndarray,
    *,
    src: nib.nifti1.Nifti1Image,
    ref: nib.nifti1.Nifti1Image,
) -> np.ndarray:
    """``world(ref) <- world(src)`` for a FLIRT ``src2ref`` matrix."""
    return (
        ref.affine
        @ np.linalg.inv(fsl_scale(ref))
        @ mat
        @ fsl_scale(src)
        @ np.linalg.inv(src.affine)
    )


def read_flirt_mat(path: Path) -> np.ndarray:
    """A FLIRT ``.mat`` as a 4x4. Raises rather than guessing at anything else."""
    try:
        mat = np.loadtxt(path, dtype=float)
    except (OSError, ValueError) as exc:
        raise RegistrationError(
            f"{path} is not a readable FLIRT matrix: {exc}"
        ) from exc
    if mat.shape != (4, 4):
        raise RegistrationError(f"{path} is {mat.shape}, not a 4x4 FLIRT matrix")
    if not np.isfinite(mat).all():
        raise RegistrationError(f"{path} holds a non-finite value")
    return mat


def sample(vol: np.ndarray, ijk: np.ndarray, *, order: int = 1) -> np.ndarray:
    """Interpolate ``vol`` at fractional indices ``ijk``, shaped ``(..., 3)``.

    Out of bounds is 0, not a clamped edge value, so a functional field of view
    reaching past a brain-extracted T1 renders as background rather than as a
    smear of the nearest surviving voxel.
    """
    return ndimage.map_coordinates(
        vol,
        np.moveaxis(ijk, -1, 0),
        order=order,
        mode="constant",
        cval=0.0,
        prefilter=order > 1,
    )


def anat_factor(
    anat_img: nib.nifti1.Nifti1Image, func_img: nib.nifti1.Nifti1Image
) -> float:
    """How finely to subdivide the functional grid to carry the anatomical.

    Its own resolution, floored at ``montage.UPSCALE`` (so the anatomical is
    never sampled coarser than the functional grid) and capped at
    ``MAX_FACTOR``.
    """
    func = min(float(z) for z in func_img.header.get_zooms()[:3])
    anat = min(float(z) for z in anat_img.header.get_zooms()[:3])
    if not anat > 0:
        raise RegistrationError(f"anatomical has a non-positive voxel size: {anat}")
    return float(np.clip(func / anat, montage.UPSCALE, MAX_FACTOR))


def anat_planes(
    *,
    anat_img: nib.nifti1.Nifti1Image,
    func_img: nib.nifti1.Nifti1Image,
    anat_to_func: np.ndarray,
    picks_by_axis: Mapping[str, Sequence[int]],
    factor: float,
) -> dict[str, np.ndarray]:
    """The anatomical sampled at every montage cell's pixel centres."""
    # canonical anat index <- world(anat) <- world(func) <- canonical func index
    to_anat = (
        np.linalg.inv(montage.canonical_affine(anat_img))
        @ np.linalg.inv(flirt_world(anat_to_func, src=anat_img, ref=func_img))
        @ montage.canonical_affine(func_img)
    )
    vol = montage.canonical_vol(anat_img)
    shape = nib.as_closest_canonical(func_img).shape[:3]
    planes: dict[str, np.ndarray] = {}
    for name, axis in montage.AXES.items():
        # one plane at a time: the whole set at once would be a needlessly
        # large transient, and each plane is only a few tens of thousands of
        # points
        planes[name] = np.stack(
            [
                sample(
                    vol,
                    montage.display_points(shape, axis, i, factor) @ to_anat[:3, :3].T
                    + to_anat[:3, 3],
                )
                for i in picks_by_axis[name]
            ]
        )
    return planes


def anat_background(
    *,
    anat_img: nib.nifti1.Nifti1Image,
    func_img: nib.nifti1.Nifti1Image,
    anat_to_func: np.ndarray,
    picks_by_axis: Mapping[str, Sequence[int]],
) -> montage.Background:
    """The ``"anat"`` background for one run, ready to composite.

    Aligned pixel-for-pixel with what ``montage.display_plane`` produces for a
    functional-grid volume, so the IC overlay needs no resampling of its own
    and the slice numbers mean the same thing on both backgrounds.
    """
    factor = anat_factor(anat_img, func_img)
    raw = anat_planes(
        anat_img=anat_img,
        func_img=func_img,
        anat_to_func=anat_to_func,
        picks_by_axis=picks_by_axis,
        factor=factor,
    )
    stack = np.concatenate([planes.ravel() for planes in raw.values()])
    inside = stack > 0
    if not inside.any():
        # an empty percentile raises an opaque IndexError several frames down
        raise RegistrationError(
            "the registration puts every montage pixel outside the anatomical; "
            "check that the .mat maps highres to the functional and not the reverse"
        )
    return montage.Background(
        name="anat",
        factor=factor,
        planes=montage.gray_planes(raw, montage.robust_window(stack, inside)),
    )
