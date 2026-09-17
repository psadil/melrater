"""Slice-montage rendering: IC z-maps over a choice of background.

A montage is flat: the background is composited into the file at render time,
so each background is its own complete set of images. A `Background` carries
the 75 display planes (25 slices x 3 axes) a run shows, already sampled onto
the montage's own pixel grid, because those planes are the same for every
component of the run and are built once rather than per component.

Overlays use transparent thresholding ("highlight, don't hide"; Taylor et
al. 2025, "Go Figure", https://pmc.ncbi.nlm.nih.gov/articles/PMC12036441/):
nothing is hidden — opacity ramps quadratically with |z| until Z_THRESH,
above which the overlay is fully opaque.

Volumes are reoriented to closest-canonical (RAS); axial and coronal montages
display neurological convention (subject L on image left), sagittal shows
anterior to the right. Output is AVIF when the Pillow AVIF codec is present,
PNG otherwise.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import nibabel as nib
import numpy as np
from nibabel.orientations import apply_orientation, inv_ornt_aff, io_orientation
from PIL import Image, ImageDraw
from PIL import features as pil_features

try:
    import pillow_avif  # noqa: F401  # registers the AVIF codec with Pillow

    AVIF_OK = True
except ImportError:
    AVIF_OK = bool(pil_features.check("avif"))

Z_THRESH = 3.0  # |z| at which the overlay becomes fully opaque
OVERLAY_VMAX = 10.0  # |z| at which the overlay color saturates
ALPHA_GAMMA = 2.0  # quadratic opacity ramp below Z_THRESH ("Go Figure")
UPSCALE = 2  # nearest-neighbour upscale of montage voxels
MIN_SLICE_COVERAGE = 0.05  # mask fraction for a slice to be shown
N_LIGHTBOX = 25
#: Hex characters of the montage-set digest kept for the storage path. 64 bits:
#: a collision would only serve one run's cached montages for another, and the
#: whole population is a few hundred runs.
DIGEST_LENGTH = 16

AXES = {"sagittal": 0, "coronal": 1, "axial": 2}

#: Montage backgrounds, in display order. "func" (the MELODIC temporal mean) is
#: the default and every run has it; "anat" (the FEAT registration's highres,
#: resampled by `resample.py`) is there only for a run that was registered.
BACKGROUNDS = ("func", "anat")

_POS_LO = np.array([1.0, 0.0, 0.0])  # red
_POS_HI = np.array([1.0, 1.0, 0.0])  # yellow
_NEG_LO = np.array([0.0, 0.2, 1.0])  # blue
_NEG_HI = np.array([0.6, 1.0, 1.0])  # light blue

# left/right edge annotations per display axis (0=sagittal, 1=coronal, 2=axial)
_EDGE_LABELS = {0: ("P", "A"), 1: ("L", "R"), 2: ("L", "R")}


def canonical_vol(img: nib.nifti1.Nifti1Image, idx: int | None = None) -> np.ndarray:
    """One 3D volume in RAS ('closest canonical') orientation."""
    if idx is not None:
        data = np.asarray(img.dataobj[..., idx], dtype=np.float32)
    else:
        data = np.asarray(img.dataobj, dtype=np.float32)
        if data.ndim == 4 and data.shape[3] == 1:
            data = data[..., 0]
    return np.asarray(
        apply_orientation(data, io_orientation(img.affine)), dtype=np.float32
    )


def canonical_affine(img: nib.nifti1.Nifti1Image) -> np.ndarray:
    """The world affine of the array ``canonical_vol`` returns.

    ``canonical_vol`` reorders and flips an image to RAS but says nothing about
    where those voxels then are; this is the matching index-to-world map. The
    two have to be read together by anything resampling onto that grid, which
    is why they live next to each other.
    """
    return np.asarray(
        img.affine @ inv_ornt_aff(io_orientation(img.affine), img.shape[:3]),
        dtype=float,
    )


def robust_window(bg: np.ndarray, mask: np.ndarray) -> tuple[float, float]:
    inside = bg[mask > 0]
    return float(np.percentile(inside, 2)), float(np.percentile(inside, 98))


def axis_picks(mask: np.ndarray, axis: int, n: int = N_LIGHTBOX) -> list[int]:
    """Evenly spaced slice indices along `axis` where the mask has coverage."""
    other = tuple(i for i in range(3) if i != axis)
    coverage = mask.sum(axis=other) / (mask.shape[other[0]] * mask.shape[other[1]])
    good = np.flatnonzero(coverage > MIN_SLICE_COVERAGE)
    if good.size == 0:
        raise ValueError(
            f"no slice along axis {axis} exceeds {MIN_SLICE_COVERAGE:.0%} mask "
            f"coverage (mask shape {mask.shape}, {int(mask.sum())} voxels set)"
        )
    lo, hi = int(good.min()), int(good.max())
    return np.unique(np.linspace(lo, hi, n).round().astype(int)).tolist()


def _overlay_rgb(t: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return lo + t[..., None] * (hi - lo)


def slice_rgb(gray2d: np.ndarray, ov2d: np.ndarray) -> np.ndarray:
    """Composite one 8-bit background plane with one z plane.

    Windowing is the background's own business and has already happened by the
    time a plane arrives here — the two backgrounds have quite different
    intensity distributions and cannot share a window — so this is the
    transparent-thresholding rule and nothing else.

    The overlay is never hidden: opacity ramps quadratically with |z| up to
    Z_THRESH, above which it is fully opaque. Color encodes signed magnitude
    (red-yellow +, blue-lightblue -) saturating at OVERLAY_VMAX.
    """
    background = np.repeat((gray2d / 255.0)[..., None], 3, axis=-1)

    ov2d = np.nan_to_num(ov2d)  # NaN voxels render as plain background
    magnitude = np.abs(ov2d)
    t = np.clip(magnitude / OVERLAY_VMAX, 0.0, 1.0)
    color = np.empty((*ov2d.shape, 3))
    pos = ov2d >= 0
    color[pos] = _overlay_rgb(t[pos], _POS_LO, _POS_HI)
    color[~pos] = _overlay_rgb(t[~pos], _NEG_LO, _NEG_HI)

    alpha = np.clip(magnitude / Z_THRESH, 0.0, 1.0) ** ALPHA_GAMMA
    rgb = background * (1.0 - alpha[..., None]) + color * alpha[..., None]
    return (rgb * 255).astype(np.uint8)


def _display(arr: np.ndarray) -> np.ndarray:
    """(a, b, ...) slice -> display array with axis b up: transpose, flip rows."""
    return arr.swapaxes(0, 1)[::-1]


def _fine(n: int, factor: float) -> np.ndarray:
    """Coarse-index coordinates of ``n`` voxels subdivided by ``factor``.

    The subdivided pixel centres span exactly the same extent the coarse ones
    do, [-0.5, n - 0.5], so a background sampled here and an overlay sampled
    here cover the same field of view whatever their voxel sizes.
    """
    m = max(1, round(n * factor))
    return (np.arange(m) + 0.5) * n / m - 0.5


def display_points(
    shape: tuple[int, ...], axis: int, idx: int, factor: float
) -> np.ndarray:
    """Continuous voxel coordinates of one display cell's pixel centres.

    ``(H, W, 3)`` in the index frame of a ``canonical_vol`` array of ``shape``,
    laid out exactly as ``display_plane`` lays that slice out. A volume on a
    *different* grid, sampled at these points after being mapped into this
    frame, therefore lands pixel-for-pixel under the overlay — which is what
    lets the anatomical background be sampled at its own resolution without a
    separate "which fine slice" rule to get wrong.
    """
    p, q = (i for i in range(3) if i != axis)
    cols, rows = _fine(shape[p], factor), _fine(shape[q], factor)[::-1]
    points = np.empty((rows.size, cols.size, 3))
    points[..., axis] = idx
    points[..., p] = cols[None, :]
    points[..., q] = rows[:, None]
    return points


def display_plane(
    vol: np.ndarray, axis: int, idx: int, factor: float = UPSCALE
) -> np.ndarray:
    """One slice of a same-grid volume, in display orientation at ``factor``.

    Nearest-neighbour, which at an integer ``factor`` is the plain pixel
    replication the montage has always done. Pinned to ``display_points`` by a
    test: the two must address the same voxel for every pixel, or a background
    and the z-map drawn over it would be silently sheared apart.
    """
    p, q = (i for i in range(3) if i != axis)
    cols = np.clip(np.rint(_fine(vol.shape[p], factor)), 0, vol.shape[p] - 1)
    rows = np.clip(np.rint(_fine(vol.shape[q], factor)), 0, vol.shape[q] - 1)
    plane = np.take(vol, idx, axis=axis)
    return _display(plane[np.ix_(cols.astype(int), rows.astype(int))])


def volume_planes(
    vol: np.ndarray,
    picks_by_axis: Mapping[str, Sequence[int]],
    factor: float = UPSCALE,
) -> dict[str, np.ndarray]:
    """``display_plane`` over every pick of every axis, as ``(n, H, W)``."""
    return {
        name: np.stack(
            [display_plane(vol, axis, i, factor) for i in picks_by_axis[name]]
        )
        for name, axis in AXES.items()
    }


def gray_planes(
    raw: Mapping[str, np.ndarray], window: tuple[float, float]
) -> dict[str, np.ndarray]:
    """Window a background's raw display planes to 8 bits.

    8-bit rather than float: the planes are pickled into every render worker,
    the output is 8-bit anyway, and the whole set is a couple of megabytes
    instead of eight.
    """
    lo, hi = window
    return {
        name: np.clip((planes - lo) / (hi - lo) * 255.0, 0.0, 255.0).astype(np.uint8)
        for name, planes in raw.items()
    }


@dataclasses.dataclass(frozen=True)
class Background:
    """One montage background, sampled and ready to composite.

    ``planes[axis_name]`` is ``(n_picks, H, W)`` uint8: already in display
    orientation, already at ``factor``, already windowed. ``factor`` is how
    finely the display grid subdivides the *functional* voxel grid, so the
    overlay can be sampled to match — 2 for the functional background (plain
    pixel replication, as always), higher for an anatomical carried at its own
    resolution.
    """

    name: str
    factor: float
    planes: dict[str, np.ndarray]


def render_lightbox(
    # iterable rather than sequence: a background hands over its whole
    # (n_picks, H, W) stack, which iterates into planes but is not a Sequence
    bg_planes: Iterable[np.ndarray],
    ov_planes: Iterable[np.ndarray],
    picks: Sequence[int],
    axis: int = 2,
    cols: int = 5,
) -> Image.Image:
    """One axis' lightbox. ``picks`` is only for the slice-index labels now.

    Those labels stay the *functional* volume's indices whatever grid the
    background was sampled on, so the number under a slice does not change when
    a reviewer swaps backgrounds.
    """
    cells = [slice_rgb(b, o) for b, o in zip(bg_planes, ov_planes, strict=True)]
    ch, cw, _ = cells[0].shape
    rows = -(-len(cells) // cols)
    gap = 2
    canvas = np.zeros(
        (rows * ch + (rows - 1) * gap, cols * cw + (cols - 1) * gap, 3), np.uint8
    )
    for i, cell in enumerate(cells):
        r, c = divmod(i, cols)
        y0, x0 = r * (ch + gap), c * (cw + gap)
        canvas[y0 : y0 + ch, x0 : x0 + cw] = cell
    img = Image.fromarray(canvas)
    draw = ImageDraw.Draw(img)
    for i, idx in enumerate(picks):
        r, c = divmod(i, cols)
        draw.text(
            (c * (cw + gap) + 4, r * (ch + gap) + 2), str(idx), fill=(150, 155, 165)
        )
    left, right = _EDGE_LABELS[axis]
    draw.text((4, ch - 16), left, fill=(200, 205, 215))
    draw.text((cw - 12, ch - 16), right, fill=(200, 205, 215))
    return img


def encode(img: Image.Image) -> tuple[bytes, str]:
    """Encode to (bytes, file extension)."""
    buf = io.BytesIO()
    if AVIF_OK:
        # 4:4:4 rather than the 4:2:0 default: the overlay colour *is* the
        # z-value, and halving the chroma resolution smears isolated
        # suprathreshold voxels into their neighbours. Measured on a real
        # component, against a lossless reference: 4:2:0 at quality 100 still
        # peaks at 162/255 of error, while 4:4:4 at 60 costs ~20% more bytes
        # than 4:2:0 at 60 and roughly halves the error (PSNR 32.1 vs 27.7 dB).
        img.save(buf, format="AVIF", quality=60, subsampling="4:4:4")
        return buf.getvalue(), "avif"
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), "png"


def montage_name(index: int, background: str, axis_name: str, ext: str) -> str:
    return f"ic{index:03d}_{background}_{axis_name}.{ext}"


def montage_count(n_components: int, backgrounds: Sequence[str]) -> int:
    """How many files one run's complete montage set holds.

    The single home for that arithmetic: the tar reader caps members with it
    and the ingest API checks the stored set against it, and those two numbers
    disagreeing is exactly how a truncated push would slip through.
    """
    return n_components * len(backgrounds) * len(AXES)


#: The exact inverse of ``montage_name``, and the only thing that turns an
#: outside string into a stored path. ``\Z`` rather than ``$``: ``$`` also
#: matches before a trailing newline, so ``"ic001_axial.avif\n"`` would pass
#: and the rebuilt name would differ from the one that was checked.
MONTAGE_NAME_RE = re.compile(
    rf"ic(\d{{3}})_({'|'.join(BACKGROUNDS)})_({'|'.join(AXES)})\.(avif|png)\Z"
)


def parse_montage_name(name: str) -> tuple[int, str, str, str] | None:
    """``'ic007_anat_axial.avif'`` -> ``(7, 'anat', 'axial', 'avif')``, else None.

    Callers rebuild the stored name with ``montage_name(*parsed)`` rather than
    reusing ``name``, so nothing a client chose ever reaches a storage path.
    """
    match = MONTAGE_NAME_RE.fullmatch(name)
    if match is None:
        return None
    index = int(match[1])
    if index < 1:  # IC numbers are 1-based; ic000 is not a component
        return None
    return index, match[2], match[3], match[4]


def digest_montages(members: Iterable[tuple[str, bytes]]) -> str:
    """A stable fingerprint of one run's whole montage set.

    This is the run's montage *identity*: it is the directory name the files
    are stored under (``runs/<uuid>/<digest>/``), so a re-render lands beside
    the old set rather than on top of it, and a montage URL never has to be
    invalidated — different bytes are simply a different URL. Being derived
    from the bytes, it also means two databases agree on it without either
    having to be told.

    Order-independent, so it does not depend on how a directory happens to
    enumerate.
    """
    return digest_hashed_montages(
        (name, hashlib.sha256(data).digest()) for name, data in members
    )


def digest_hashed_montages(hashed: Iterable[tuple[str, bytes]]) -> str:
    """``digest_montages`` for a caller that already hashed each file.

    The receiving end streams one montage at a time and never holds the whole
    set, so it hashes as it goes and folds the per-file digests in here.
    """
    outer = hashlib.sha256()
    for name, file_digest in sorted(hashed):
        outer.update(name.encode())
        outer.update(b"\0")
        outer.update(file_digest)
    return outer.hexdigest()[:DIGEST_LENGTH]


def digest_directory(staged: Path) -> str:
    """``digest_montages`` over a freshly rendered directory."""
    return digest_montages(
        (entry.name, entry.read_bytes())
        for entry in staged.iterdir()
        if entry.is_file()
    )


def render_component_montages(
    ov: np.ndarray,
    backgrounds: Sequence[Background],
    picks_by_axis: Mapping[str, Sequence[int]],
    out_dir: Path,
    index: int,
) -> None:
    """Render one component's lightboxes: one file per background per axis."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for axis_name, axis in AXES.items():
        picks = picks_by_axis[axis_name]
        for background in backgrounds:
            ov_planes = [display_plane(ov, axis, i, background.factor) for i in picks]
            img = render_lightbox(
                background.planes[axis_name], ov_planes, picks, axis=axis
            )
            data, ext = encode(img)
            name = montage_name(index, background.name, axis_name, ext)
            (out_dir / name).write_bytes(data)


# ProcessPool worker state: initialized once per worker process. This module is
# Django-free on purpose so spawn-based workers can re-import it cheaply.
_WORKER: dict = {}


def _init_worker(
    ic_path: str,
    backgrounds: tuple[Background, ...],
    picks_by_axis: dict[str, list[int]],
    out_dir: str,
) -> None:
    _WORKER.update(
        ic_img=nib.load(ic_path),
        backgrounds=backgrounds,
        picks=picks_by_axis,
        out_dir=Path(out_dir),
    )


def _render_one(index: int) -> int:
    ic_img = _WORKER["ic_img"]
    assert isinstance(ic_img, nib.nifti1.Nifti1Image)
    ov = canonical_vol(ic_img, index - 1)
    render_component_montages(
        ov,
        _WORKER["backgrounds"],
        _WORKER["picks"],
        _WORKER["out_dir"],
        index,
    )
    return index


def render_run_montages(
    ic_path: Path,
    indices: list[int],
    backgrounds: Sequence[Background],
    picks_by_axis: dict[str, list[int]],
    out_dir: Path,
    workers: int = 0,
) -> None:
    """Render lightboxes for many components, optionally in parallel.

    The backgrounds are pickled into each worker once: they are the same 75
    planes for every component, so a run's whole background cost is paid at
    pool startup rather than per montage.
    """
    initargs = (str(ic_path), tuple(backgrounds), picks_by_axis, str(out_dir))
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(
            max_workers=workers, initializer=_init_worker, initargs=initargs
        ) as pool:
            list(pool.map(_render_one, indices))
        return
    _init_worker(*initargs)
    for index in indices:
        _render_one(index)
