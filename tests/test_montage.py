import nibabel as nib
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
    # Arrange: mid-gray background (windowing already applied), no statistic
    gray = np.full((4, 4), 127, dtype=np.uint8)
    ov = np.zeros((4, 4))

    # Act
    out = montage.slice_rgb(gray, ov)

    # Assert
    assert tuple(out[0, 0]) == (127, 127, 127)


def test_slice_rgb_suprathreshold_is_fully_opaque() -> None:
    # Arrange: |z| at the color saturation point
    gray = np.full((4, 4), 127, dtype=np.uint8)
    ov = np.full((4, 4), montage.OVERLAY_VMAX)

    # Act
    out = montage.slice_rgb(gray, ov)

    # Assert: pure yellow, no background bleed-through
    assert tuple(out[0, 0]) == (255, 255, 0)


def test_slice_rgb_subthreshold_blends_quadratically() -> None:
    # Arrange: |z| = 1.5 -> alpha = (1.5/3)^2 = 0.25, color t = 0.15
    gray = np.full((4, 4), 127, dtype=np.uint8)
    ov = np.full((4, 4), 1.5)

    # Act
    out = montage.slice_rgb(gray, ov)

    # Assert: 0.75*gray + 0.25*(1, 0.15, 0), scaled to bytes. Green is one
    # LSB below the pre-8-bit-background value (105): 127/255 is not exactly
    # the 0.5 that windowing 100.0 into (0, 200) used to produce.
    assert tuple(out[0, 0]) == (159, 104, 95)


def test_slice_rgb_nan_shows_pure_background() -> None:
    # Arrange: a NaN voxel (nonstandard derivatives mask with NaN)
    gray = np.full((4, 4), 127, dtype=np.uint8)
    ov = np.full((4, 4), np.nan)

    # Act
    out = montage.slice_rgb(gray, ov)

    # Assert
    assert tuple(out[0, 0]) == (127, 127, 127)


def test_slice_rgb_negative_uses_cool_colors() -> None:
    # Arrange
    gray = np.full((4, 4), 127, dtype=np.uint8)
    ov = np.full((4, 4), -montage.OVERLAY_VMAX)

    # Act
    out = montage.slice_rgb(gray, ov)

    # Assert: fully saturated light blue
    assert tuple(out[0, 0]) == (153, 255, 255)


def test_axis_picks_rejects_empty_coverage() -> None:
    # Arrange: a blob too small to reach MIN_SLICE_COVERAGE on any slice
    mask = np.zeros((20, 20, 20), dtype="float32")
    mask[:2, :2, :2] = 1.0

    # Act / Assert
    with pytest.raises(ValueError, match="mask coverage"):
        montage.axis_picks(mask, axis=0)


def test_display_points_match_the_overlay_grid() -> None:
    # Arrange: a fractional factor, which is what an anatomical background at
    # its own resolution actually uses
    rng = np.random.default_rng(3)
    vol = rng.random((5, 4, 3)).astype("float32")

    # Act: sample the same slice both ways
    plane = montage.display_plane(vol, axis=2, idx=1, factor=2.4)
    points = montage.display_points(vol.shape, axis=2, idx=1, factor=2.4)
    index = np.clip(np.rint(points).astype(int), 0, np.array(vol.shape) - 1)

    # Assert: the pairing that keeps a background and the z-map drawn over it
    # addressing the same voxel. If this drifts, the two are sheared apart on
    # screen with nothing else to notice.
    assert np.array_equal(plane, vol[index[..., 0], index[..., 1], index[..., 2]])


def test_display_plane_replicates_pixels_at_an_integer_factor() -> None:
    # Arrange
    vol = np.arange(24, dtype="float32").reshape(2, 3, 4)

    # Act
    plane = montage.display_plane(vol, axis=2, idx=0, factor=2)

    # Assert: the plain nearest-neighbour upscale the montage used to bake in
    expected = np.repeat(
        np.repeat(vol[:, :, 0].swapaxes(0, 1)[::-1], 2, axis=0), 2, axis=1
    )
    assert np.array_equal(plane, expected)


def test_display_plane_is_the_voxel_grid_at_the_default_factor() -> None:
    # Arrange
    vol = np.arange(24, dtype="float32").reshape(2, 3, 4)

    # Act
    plane = montage.display_plane(vol, axis=2, idx=0)

    # Assert: montages are encoded at voxel resolution; the browser upscales
    assert np.array_equal(plane, vol[:, :, 0].swapaxes(0, 1)[::-1])


def test_render_lightbox_tiles_cells_with_no_gap() -> None:
    # Arrange: two flat cells, told apart by their gray level
    bg = [np.full((3, 4), 10, dtype=np.uint8), np.full((3, 4), 20, dtype=np.uint8)]
    ov = [np.zeros((3, 4)), np.zeros((3, 4))]

    # Act
    img = np.asarray(montage.render_lightbox(bg, ov, cols=2))

    # Assert: the second cell starts exactly where the first ends, which is
    # what lets the page place a label at (col / cols, row / rows)
    assert (img.shape, tuple(img[0, 4])) == ((3, 8, 3), (20, 20, 20))


def test_canonical_zooms_follow_the_reoriented_axes() -> None:
    # Arrange: array axis 0 runs along world y at 1 mm, axis 1 along world x
    # at 2 mm, so the canonical (RAS) array swaps them
    affine = np.array(
        [
            [0.0, 2.0, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 3.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    img = nib.nifti1.Nifti1Image(np.zeros((4, 5, 6), dtype="float32"), affine)

    # Act
    zooms = montage.canonical_zooms(img)

    # Assert: a kernel in millimetres has to know which array axis is which
    assert np.allclose(zooms, (2.0, 1.0, 3.0))


def test_gray_planes_span_the_full_byte_range() -> None:
    # Arrange: planes holding exactly the window's endpoints
    raw = {"axial": np.array([[[0.0, 50.0, 100.0]]])}

    # Act
    planes = montage.gray_planes(raw, window=(0.0, 100.0))

    # Assert
    assert tuple(planes["axial"][0, 0]) == (0, 127, 255)


def test_montage_count_counts_every_background_and_axis() -> None:
    # Assert: the single home for the arithmetic the tar cap and the ingest
    # API's set check both read
    assert montage.montage_count(2, ("func",), ("raw",)) == 6


def test_montage_count_counts_every_smoothing_level() -> None:
    # Assert
    assert montage.montage_count(2, ("func",), ("raw", "smooth")) == 12
