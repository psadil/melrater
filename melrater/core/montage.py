"""Slice-montage rendering: thresholded IC z-maps over the mean functional.

Volumes are reoriented to closest-canonical (RAS); axial and coronal montages
display neurological convention (subject L on image left), sagittal shows
anterior to the right. Output is AVIF when the Pillow AVIF codec is present,
PNG otherwise.
"""

from __future__ import annotations

import io
from pathlib import Path

import nibabel as nib
import numpy as np
from nibabel.orientations import apply_orientation, io_orientation
from PIL import Image, ImageDraw
from PIL import features as pil_features

try:
    import pillow_avif  # noqa: F401  # registers the AVIF codec with Pillow

    AVIF_OK = True
except ImportError:
    AVIF_OK = bool(pil_features.check("avif"))

Z_THRESH = 3.0  # |z| display threshold for spatial maps
OVERLAY_VMAX = 10.0
UPSCALE = 2  # nearest-neighbour upscale of montage voxels
MIN_SLICE_COVERAGE = 0.05  # mask fraction for a slice to be shown
N_LIGHTBOX = 25

AXES = {"sagittal": 0, "coronal": 1, "axial": 2}

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


def robust_window(bg: np.ndarray, mask: np.ndarray) -> tuple[float, float]:
    inside = bg[mask > 0]
    return float(np.percentile(inside, 2)), float(np.percentile(inside, 98))


def axis_picks(mask: np.ndarray, axis: int, n: int = N_LIGHTBOX) -> list[int]:
    """Evenly spaced slice indices along `axis` where the mask has coverage."""
    other = tuple(i for i in range(3) if i != axis)
    coverage = mask.sum(axis=other) / (mask.shape[other[0]] * mask.shape[other[1]])
    good = np.flatnonzero(coverage > MIN_SLICE_COVERAGE)
    lo, hi = int(good.min()), int(good.max())
    return np.unique(np.linspace(lo, hi, n).round().astype(int)).tolist()


def _overlay_rgb(t: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return lo + t[..., None] * (hi - lo)


def slice_rgb(
    bg2d: np.ndarray, ov2d: np.ndarray, window: tuple[float, float]
) -> np.ndarray:
    """Composite one 2D slice: grayscale background + opaque thresholded overlay."""
    lo, hi = window
    gray = np.clip((bg2d - lo) / (hi - lo), 0.0, 1.0)
    rgb = np.repeat(gray[..., None], 3, axis=-1)
    span = OVERLAY_VMAX - Z_THRESH
    pos = ov2d >= Z_THRESH
    if pos.any():
        t = np.clip((ov2d[pos] - Z_THRESH) / span, 0.0, 1.0)
        rgb[pos] = _overlay_rgb(t, _POS_LO, _POS_HI)
    neg = ov2d <= -Z_THRESH
    if neg.any():
        t = np.clip((-ov2d[neg] - Z_THRESH) / span, 0.0, 1.0)
        rgb[neg] = _overlay_rgb(t, _NEG_LO, _NEG_HI)
    return (rgb * 255).astype(np.uint8)


def _display(arr2d: np.ndarray) -> np.ndarray:
    """(a, b) slice -> display array with axis b up: transpose then flip rows."""
    return arr2d.transpose(1, 0, 2)[::-1]


def _upscale(rgb: np.ndarray) -> np.ndarray:
    return np.repeat(np.repeat(rgb, UPSCALE, axis=0), UPSCALE, axis=1)


def slice_cell(
    bg: np.ndarray, ov: np.ndarray, axis: int, idx: int, window: tuple[float, float]
) -> np.ndarray:
    bg2d = np.take(bg, idx, axis=axis)
    ov2d = np.take(ov, idx, axis=axis)
    return _upscale(_display(slice_rgb(bg2d, ov2d, window)))


def render_lightbox(
    bg: np.ndarray,
    ov: np.ndarray,
    picks: list[int],
    window: tuple[float, float],
    axis: int = 2,
    cols: int = 5,
) -> Image.Image:
    cells = [slice_cell(bg, ov, axis, i, window) for i in picks]
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
        img.save(buf, format="AVIF", quality=60)
        return buf.getvalue(), "avif"
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), "png"


def montage_name(index: int, axis_name: str, ext: str) -> str:
    return f"ic{index:03d}_{axis_name}.{ext}"


def render_component_montages(
    bg: np.ndarray,
    ov: np.ndarray,
    picks_by_axis: dict[str, list[int]],
    window: tuple[float, float],
    out_dir: Path,
    index: int,
) -> None:
    """Render one component's lightboxes (one file per display axis)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for axis_name, axis in AXES.items():
        img = render_lightbox(bg, ov, picks_by_axis[axis_name], window, axis=axis)
        data, ext = encode(img)
        (out_dir / montage_name(index, axis_name, ext)).write_bytes(data)


# ProcessPool worker state: initialized once per worker process. This module is
# Django-free on purpose so spawn-based workers can re-import it cheaply.
_WORKER: dict = {}


def _init_worker(
    ic_path: str,
    bg: np.ndarray,
    picks_by_axis: dict[str, list[int]],
    window: tuple[float, float],
    out_dir: str,
) -> None:
    _WORKER.update(
        ic_img=nib.load(ic_path),
        bg=bg,
        picks=picks_by_axis,
        window=window,
        out_dir=Path(out_dir),
    )


def _render_one(index: int) -> int:
    ic_img = _WORKER["ic_img"]
    assert isinstance(ic_img, nib.nifti1.Nifti1Image)
    ov = canonical_vol(ic_img, index - 1)
    render_component_montages(
        _WORKER["bg"],
        ov,
        _WORKER["picks"],
        _WORKER["window"],
        _WORKER["out_dir"],
        index,
    )
    return index


def render_run_montages(
    ic_path: Path,
    indices: list[int],
    bg: np.ndarray,
    picks_by_axis: dict[str, list[int]],
    window: tuple[float, float],
    out_dir: Path,
    workers: int = 0,
) -> None:
    """Render lightboxes for many components, optionally in parallel."""
    initargs = (str(ic_path), bg, picks_by_axis, window, str(out_dir))
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
