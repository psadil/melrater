"""FSL's scaled-millimetre convention, and sampling onto the montage grid."""

import nibabel as nib
import numpy as np
import pytest

from melrater.core import montage, resample


def _image(shape=(6, 5, 4), affine=None) -> nib.nifti1.Nifti1Image:
    affine = np.diag([2.0, 3.0, 4.0, 1.0]) if affine is None else affine
    affine = np.asarray(affine, dtype=float)
    data = np.arange(int(np.prod(shape)), dtype="float32").reshape(shape)
    return nib.nifti1.Nifti1Image(data, affine)


# --- the FSL frame ------------------------------------------------------


def test_fsl_scale_is_plain_zooms_for_radiological_storage() -> None:
    # Arrange: a negative determinant, so FSL does not flip x
    img = _image(affine=np.diag([-2.0, 3.0, 4.0, 1.0]))

    # Assert
    assert np.array_equal(resample.fsl_scale(img), np.diag([2.0, 3.0, 4.0, 1.0]))


def test_fsl_scale_flips_x_for_neurological_storage() -> None:
    # Arrange: a positive determinant, which is the case FSL mirrors
    img = _image(affine=np.diag([2.0, 3.0, 4.0, 1.0]))

    # Assert: -zx in the corner, offset by (nx - 1) * zx
    assert tuple(resample.fsl_scale(img)[0]) == (-2.0, 0.0, 0.0, 10.0)


def test_flirt_world_maps_an_image_to_itself_for_the_identity() -> None:
    # Arrange
    img = _image()

    # Act
    world = resample.flirt_world(np.eye(4), src=img, ref=img)

    # Assert: identity in FSL's frame has to be identity in the world's
    assert np.allclose(world, np.eye(4))


def test_flirt_world_recovers_a_known_world_transform() -> None:
    # Arrange: compose a .mat *from* a world transform, then read it back
    src, ref = _image(), _image(affine=np.diag([-2.0, 2.0, 2.0, 1.0]))
    known = np.eye(4)
    known[:3, 3] = (5.0, -3.0, 11.0)
    mat = (
        resample.fsl_scale(ref)
        @ np.linalg.inv(ref.affine)
        @ known
        @ src.affine
        @ np.linalg.inv(resample.fsl_scale(src))
    )

    # Act / Assert
    assert np.allclose(resample.flirt_world(mat, src=src, ref=ref), known)


def test_read_flirt_mat_rejects_a_matrix_that_is_not_4x4(tmp_path) -> None:
    # Arrange
    path = tmp_path / "bad.mat"
    np.savetxt(path, np.eye(3))

    # Act / Assert
    with pytest.raises(resample.RegistrationError, match="not a 4x4"):
        resample.read_flirt_mat(path)


# --- sampling -----------------------------------------------------------


def test_sample_is_exact_at_integer_indices() -> None:
    # Arrange
    vol = np.arange(24, dtype="float32").reshape(2, 3, 4)
    ijk = np.array([[[1, 2, 3]]], dtype=float)

    # Act / Assert: no interpolation error where a point lands on a voxel
    assert resample.sample(vol, ijk)[0, 0] == pytest.approx(vol[1, 2, 3])


def test_sample_is_zero_outside_the_volume() -> None:
    # Arrange: a field of view reaching past a brain-extracted structural
    vol = np.ones((3, 3, 3), dtype="float32")
    ijk = np.array([[[-5.0, 0.0, 0.0]]])

    # Act / Assert: background, not a smear of the nearest surviving voxel
    assert resample.sample(vol, ijk)[0, 0] == 0.0


# --- the background -----------------------------------------------------


def test_anat_factor_follows_the_anatomical_resolution() -> None:
    # Arrange: 1 mm structural under a 3 mm functional
    anat = _image(affine=np.diag([1.0, 1.0, 1.0, 1.0]))
    func = _image(affine=np.diag([3.0, 3.0, 3.0, 1.0]))

    # Assert: capped at MAX_FACTOR rather than the raw ratio of 3
    assert resample.anat_factor(anat, func) == resample.MAX_FACTOR


def test_anat_factor_never_goes_below_the_montage_upscale() -> None:
    # Arrange: a structural no finer than the functional
    anat = _image(affine=np.diag([2.0, 2.0, 2.0, 1.0]))
    func = _image(affine=np.diag([2.0, 2.0, 2.0, 1.0]))

    # Assert: the anatomical is never coarser than the functional background
    assert resample.anat_factor(anat, func) == float(montage.UPSCALE)


def test_anat_background_refuses_a_transform_that_misses_the_anatomical() -> None:
    # Arrange: a translation far outside any plausible head
    anat, func = _image(affine=np.diag([1.0, 1.0, 1.0, 1.0])), _image()
    miss = np.eye(4)
    miss[0, 3] = 10_000.0
    picks = {name: [0] for name in montage.AXES}

    # Act / Assert
    with pytest.raises(resample.RegistrationError, match="outside the anatomical"):
        resample.anat_background(
            anat_img=anat, func_img=func, anat_to_func=miss, picks_by_axis=picks
        )
