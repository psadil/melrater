"""Generate static HTML mockups of the melrater rating screen.

Reads a real MELODIC+pyFIX derivatives directory and writes two
self-contained HTML files (variant_a.html, variant_b.html) built from real
data — IC timecourses, framewise displacement, power spectra, pyFIX feature
distributions, and thresholded spatial-map montages — so the rating-screen
layout can be judged before any application code exists.

Run with: pixi run mockups [--debug-img]
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
from dataclasses import dataclass
from pathlib import Path
from string import Template

import nibabel as nib
import numpy as np
from nibabel.orientations import apply_orientation, io_orientation
from PIL import Image, ImageDraw
from PIL import features as pil_features

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

DATA_ROOT = Path(
    "/Users/psadil/git/a2cps/melodic/derivatives/"
    "sub-10113_ses-V1_task-cuff_run-01_desc-preproc_bold"
)
ICA_DIR = DATA_ROOT / "filtered_func_data.ica"
FIX_FILE = DATA_ROOT / "fix4melview_UKBiobank_thr1.txt"
OUT_DIR = Path(__file__).resolve().parent
DEBUG_DIR = Path(
    "/private/tmp/claude-501/-Users-psadil-git-neuro-melrater/"
    "8b687d07-ef3e-4408-84ae-04afca56d545/scratchpad"
)

RUN_LABEL = "sub-10113 · ses-V1 · task-cuff · run-01"
FIX_REVIEWER = "UKBiobank @ thr1"
FIX_THRESHOLD = 0.01

# 1-based IC indices rendered as tabs, with the role each plays in the mockup.
COMPONENTS: list[tuple[int, str]] = [
    (50, "clear signal"),
    (1, "clear noise"),
    (2, "borderline"),
]

Z_THRESH = 3.0  # |z| display threshold for spatial maps
OVERLAY_VMAX = 10.0
OUTLIER_Z = 3.0  # |robust z| above which a metric counts as an outlier
METRIC_CLIP = 6.0  # display clip for metric z-scores
UPSCALE = 2  # nearest-neighbour upscale of montage voxels
FD_REFS = (0.2, 0.5)  # mm reference lines
MIN_SLICE_COVERAGE = 0.05  # mask fraction for an axial slice to be shown
N_LIGHTBOX = 25
N_STRIP = 8

# Simulated human review state for variant C: ICs 1..MOCK_RATED_THROUGH have
# been "rated", agreeing with FIX except for deliberate overrides near the
# decision threshold — so rating-colored ticks visibly sit on the "wrong" side
# of the threshold line, which is exactly the disagreement signal to judge.
MOCK_RATED_THROUGH = 24
MOCK_RATING_OVERRIDES = {2: "Noise", 9: "Unknown", 15: "Signal"}

PINNED_FAMILIES = [
    "motioncorrelation",
    "edgemasks",
    "sagmasks",
    "tsjump",
    "clusterdist",
]
PINNED_SINGLES = ["skewness", "kurtosis", "entropy:0", "fftcoarse:3", "spatialoverlap"]

# palette (dark theme)
C_BG = "#12151a"
C_PANEL = "#1a1f27"
C_PANEL2 = "#222834"
C_BORDER = "#2c3442"
C_TEXT = "#d6dae1"
C_MUTED = "#8b93a1"
C_GRID = "#262d38"
C_ACCENT = "#60a5fa"
C_SIGNAL = "#4ade80"
C_NOISE = "#f87171"
C_UNKNOWN = "#fbbf24"
C_FD = "#e8a13c"
C_SEV_OK = "#8b93a1"
C_SEV_WARN = "#fbbf24"
C_SEV_BAD = "#f87171"
RATING_COLORS = {"Signal": C_SIGNAL, "Noise": C_NOISE, "Unknown": C_UNKNOWN}

# --------------------------------------------------------------------------
# LOADERS
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    label: str  # "Signal" | "Noise"
    p_signal: float


def load_tr() -> float:
    header = nib.load(DATA_ROOT / "filtered_func_data.nii.gz").header
    assert isinstance(header, nib.nifti1.Nifti1Header)
    return float(header["pixdim"][4])


def load_verdicts() -> list[Verdict]:
    verdicts: list[Verdict] = []
    for line in FIX_FILE.read_text().splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("["):
            break
        _, label, _, prob = (part.strip() for part in line.split(","))
        verdicts.append(Verdict(label=label, p_signal=float(prob)))
    return verdicts


def load_features() -> tuple[list[str], np.ndarray]:
    with open(DATA_ROOT / "fix" / "features.csv") as f:
        reader = csv.reader(f)
        names = next(reader)
        rows = [[float(v) for v in row] for row in reader if row]
    return names, np.asarray(rows)


def load_fd() -> np.ndarray:
    par = np.loadtxt(DATA_ROOT / "mc" / "prefiltered_func_data_mcf.par")
    rot, trans = par[:, :3], par[:, 3:]
    fd = np.abs(np.diff(trans, axis=0)).sum(axis=1) + 50.0 * np.abs(
        np.diff(rot, axis=0)
    ).sum(axis=1)
    return np.concatenate([[0.0], fd])


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


# --------------------------------------------------------------------------
# COMPUTE
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricTable:
    names: list[str]  # kept feature names
    raw: np.ndarray  # (n_ic, n_kept)
    z: np.ndarray  # (n_ic, n_kept) robust z, unclipped
    dropped: list[str]  # constant features excluded from z-scoring
    signal_rows: list[int]  # 0-based rows of FIX-labeled Signal components


def build_metric_table(
    names: list[str], raw: np.ndarray, verdicts: list[Verdict]
) -> MetricTable:
    med = np.median(raw, axis=0)
    mad = np.median(np.abs(raw - med), axis=0)
    scale = 1.4826 * mad
    std = raw.std(axis=0)
    scale = np.where(scale > 0, scale, std)
    keep = scale > 0
    z = (raw[:, keep] - med[keep]) / scale[keep]
    return MetricTable(
        names=[n for n, k in zip(names, keep) if k],
        raw=raw[:, keep],
        z=z,
        dropped=[n for n, k in zip(names, keep) if not k],
        signal_rows=[i for i, v in enumerate(verdicts) if v.label == "Signal"],
    )


def family_of(name: str) -> str:
    return name.split(":")[0]


def family_members(table: MetricTable) -> dict[str, list[int]]:
    fams: dict[str, list[int]] = {}
    for j, name in enumerate(table.names):
        fams.setdefault(family_of(name), []).append(j)
    return fams


def outlier_indices(table: MetricTable, row: int) -> list[int]:
    z = np.abs(table.z[row])
    idx = np.flatnonzero(z > OUTLIER_Z)
    return sorted(idx.tolist(), key=lambda j: -z[j])


def pinned_indices(table: MetricTable, row: int) -> list[tuple[str, int]]:
    """(display label, column) for the pinned interpretable strips."""
    fams = family_members(table)
    pinned: list[tuple[str, int]] = []
    for fam in PINNED_FAMILIES:
        members = fams.get(fam, [])
        if not members:
            continue
        j = max(members, key=lambda j: abs(float(table.z[row, j])))
        pinned.append((table.names[j] if ":" in table.names[j] else fam, j))
    name_to_col = {n: j for j, n in enumerate(table.names)}
    for name in PINNED_SINGLES:
        if name in name_to_col:
            pinned.append((name, name_to_col[name]))
    return pinned


def axis_picks(mask: np.ndarray, axis: int, n: int) -> list[int]:
    """Evenly spaced slice indices along `axis` where the mask has coverage."""
    other = tuple(i for i in range(3) if i != axis)
    coverage = mask.sum(axis=other) / (mask.shape[other[0]] * mask.shape[other[1]])
    good = np.flatnonzero(coverage > MIN_SLICE_COVERAGE)
    lo, hi = int(good.min()), int(good.max())
    return np.unique(np.linspace(lo, hi, n).round().astype(int)).tolist()


def mock_user_ratings(verdicts: list[Verdict]) -> dict[int, str]:
    """1-based IC -> simulated human label for the 'partially rated' state."""
    ratings = {ic: verdicts[ic - 1].label for ic in range(1, MOCK_RATED_THROUGH + 1)}
    ratings.update(MOCK_RATING_OVERRIDES)
    return ratings


# --------------------------------------------------------------------------
# RENDER_IMG — montages composed as numpy RGB, encoded via Pillow
# --------------------------------------------------------------------------

try:
    import pillow_avif  # noqa: F401  # registers the AVIF codec with Pillow

    AVIF_OK = True
except ImportError:
    AVIF_OK = bool(pil_features.check("avif"))

_POS_LO = np.array([1.0, 0.0, 0.0])  # red
_POS_HI = np.array([1.0, 1.0, 0.0])  # yellow
_NEG_LO = np.array([0.0, 0.2, 1.0])  # blue
_NEG_HI = np.array([0.6, 1.0, 1.0])  # light blue


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


# left/right edge annotations per display axis (0=sagittal, 1=coronal, 2=axial)
_EDGE_LABELS = {0: ("P", "A"), 1: ("L", "R"), 2: ("L", "R")}


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
    # orientation labels on the first cell (neurological: subject L on image left)
    left, right = _EDGE_LABELS[axis]
    draw.text((4, ch - 16), left, fill=(200, 205, 215))
    draw.text((cw - 12, ch - 16), right, fill=(200, 205, 215))
    return img


def render_strip(
    bg: np.ndarray, ov: np.ndarray, zs: list[int], window: tuple[float, float]
) -> Image.Image:
    cells = [slice_cell(bg, ov, 2, z, window) for z in zs]
    ch, cw, _ = cells[0].shape
    gap = 2
    canvas = np.zeros((ch, len(cells) * cw + (len(cells) - 1) * gap, 3), np.uint8)
    for i, cell in enumerate(cells):
        canvas[:, i * (cw + gap) : i * (cw + gap) + cw] = cell
    img = Image.fromarray(canvas)
    draw = ImageDraw.Draw(img)
    for i, z in enumerate(zs):
        draw.text((i * (cw + gap) + 4, 2), str(z), fill=(150, 155, 165))
    draw.text((4, ch - 16), "L", fill=(200, 205, 215))
    return img


def render_ortho(
    bg: np.ndarray,
    ov: np.ndarray,
    peak: tuple[int, int, int],
    window: tuple[float, float],
) -> Image.Image:
    px, py, pz = peak
    nx, ny, nz = bg.shape
    sag = _upscale(_display(slice_rgb(bg[px, :, :], ov[px, :, :], window)))
    cor = _upscale(_display(slice_rgb(bg[:, py, :], ov[:, py, :], window)))
    axi = _upscale(_display(slice_rgb(bg[:, :, pz], ov[:, :, pz], window)))
    # crosshair (row, col) per display panel, accounting for the row flip
    marks = [
        (sag, (nz - 1 - pz) * UPSCALE, py * UPSCALE),
        (cor, (nz - 1 - pz) * UPSCALE, px * UPSCALE),
        (axi, (ny - 1 - py) * UPSCALE, px * UPSCALE),
    ]
    gap = 4
    h = max(p.shape[0] for p, _, _ in marks)
    w = sum(p.shape[1] for p, _, _ in marks) + gap * (len(marks) - 1)
    canvas = np.zeros((h, w, 3), np.uint8)
    x0 = 0
    positions: list[tuple[int, int, int, int, int]] = []
    for panel, row, col in marks:
        y0 = (h - panel.shape[0]) // 2
        canvas[y0 : y0 + panel.shape[0], x0 : x0 + panel.shape[1]] = panel
        positions.append((x0, y0, panel.shape[1], panel.shape[0], 0))
        # draw crosshair directly into the array (dim white)
        rr, cc = y0 + row, x0 + col
        canvas[y0 + row, x0 : x0 + panel.shape[1]] = (
            canvas[y0 + row, x0 : x0 + panel.shape[1]] // 2 + 110
        )
        canvas[y0 : y0 + panel.shape[0], x0 + col] = (
            canvas[y0 : y0 + panel.shape[0], x0 + col] // 2 + 110
        )
        del rr, cc
        x0 += panel.shape[1] + gap
    img = Image.fromarray(canvas)
    draw = ImageDraw.Draw(img)
    axi_x0 = positions[2][0]
    axi_y0 = positions[2][1]
    draw.text((axi_x0 + 4, axi_y0 + ny * UPSCALE - 16), "L", fill=(200, 205, 215))
    draw.text(
        (axi_x0 + nx * UPSCALE - 12, axi_y0 + ny * UPSCALE - 16),
        "R",
        fill=(200, 205, 215),
    )
    return img


def encode_img(img: Image.Image) -> str:
    buf = io.BytesIO()
    if AVIF_OK:
        img.save(buf, format="AVIF", quality=60)
        mime = "image/avif"
    else:
        img.save(buf, format="PNG", optimize=True)
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


# --------------------------------------------------------------------------
# RENDER_SVG — hand-rolled charts (no plotting library)
# --------------------------------------------------------------------------


def _poly(xs: np.ndarray, ys: np.ndarray) -> str:
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    return f'<polyline fill="none" points="{pts}"'


def _xmap(v: np.ndarray, lo: float, hi: float, x0: float, x1: float) -> np.ndarray:
    return x0 + (v - lo) / (hi - lo) * (x1 - x0)


def timecourse_fd_svg(ts: np.ndarray, fd: np.ndarray, tr: float, width: int) -> str:
    """IC timecourse and FD as two panels sharing one x axis."""
    ml, mr = 46, 10
    h_ts, h_fd, gap, m_top, m_bot = 110, 74, 18, 16, 40
    height = m_top + h_ts + gap + h_fd + m_bot
    x0, x1 = ml, width - mr
    t = np.arange(len(ts)) * tr
    t_max = float(t[-1])
    xs = _xmap(t, 0, t_max, x0, x1)

    ts_amp = float(np.abs(ts).max()) or 1.0
    y_ts = m_top + h_ts / 2 - (ts / ts_amp) * (h_ts / 2 - 4)
    fd_top = m_top + h_ts + gap
    fd_max = max(float(fd.max()), FD_REFS[1] + 0.1)
    y_fd = fd_top + h_fd - (fd / fd_max) * (h_fd - 6)

    parts = [
        (
            f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="system-ui" font-size="11" class="chart">'
        )
    ]
    # shared vertical gridlines every 60 s, drawn through both panels
    for sec in range(0, int(t_max) + 1, 60):
        gx = _xmap(np.array([sec]), 0, t_max, x0, x1)[0]
        parts.append(
            f'<line x1="{gx:.1f}" y1="{m_top}" x2="{gx:.1f}" '
            f'y2="{fd_top + h_fd}" stroke="{C_GRID}"/>'
        )
        parts.append(
            f'<text x="{gx:.1f}" y="{fd_top + h_fd + 14}" fill="{C_MUTED}" '
            f'text-anchor="middle">{sec}</text>'
        )
    # panel frames + zero line
    for top, hh in ((m_top, h_ts), (fd_top, h_fd)):
        parts.append(
            f'<rect x="{x0}" y="{top}" width="{x1 - x0}" height="{hh}" '
            f'fill="none" stroke="{C_BORDER}"/>'
        )
    zy = m_top + h_ts / 2
    parts.append(f'<line x1="{x0}" y1="{zy}" x2="{x1}" y2="{zy}" stroke="{C_GRID}"/>')
    # FD reference lines
    for ref, color in zip(FD_REFS, (C_SEV_WARN, C_SEV_BAD)):
        ry = fd_top + h_fd - (ref / fd_max) * (h_fd - 6)
        parts.append(
            f'<line x1="{x0}" y1="{ry:.1f}" x2="{x1}" y2="{ry:.1f}" '
            f'stroke="{color}" stroke-dasharray="4 4" opacity="0.55"/>'
        )
        parts.append(
            f'<text x="{x0 - 6}" y="{ry + 4:.1f}" fill="{color}" '
            f'text-anchor="end">{ref}</text>'
        )
    parts.append(_poly(xs, y_ts) + f' stroke="{C_ACCENT}" stroke-width="1.1"/>')
    parts.append(_poly(xs, y_fd) + f' stroke="{C_FD}" stroke-width="1.1"/>')
    # labels
    parts.append(
        f'<text x="{x0}" y="{m_top - 4}" fill="{C_MUTED}">IC timecourse (a.u.)</text>'
    )
    parts.append(f'<text x="{x0}" y="{fd_top - 4}" fill="{C_FD}">FD (mm)</text>')
    parts.append(
        f'<text x="{(x0 + x1) / 2:.0f}" y="{fd_top + h_fd + 30}" fill="{C_MUTED}" '
        f'text-anchor="middle">time (s)</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def spectrum_svg(ft: np.ndarray, freqs: np.ndarray, width: int) -> str:
    ml, mr, m_top, m_bot = 46, 10, 14, 38
    hh = 96
    height = m_top + hh + m_bot
    x0, x1 = ml, width - mr
    f_max = float(freqs[-1])
    xs = _xmap(freqs, 0, f_max, x0, x1)
    amp = float(ft.max()) or 1.0
    ys = m_top + hh - (ft / amp) * (hh - 6)

    parts = [
        (
            f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="system-ui" font-size="11" class="chart">'
        )
    ]
    # shade outside the typical resting-state band (0.01–0.1 Hz)
    for lo, hi in ((0.0, 0.01), (0.1, f_max)):
        bx0 = _xmap(np.array([lo]), 0, f_max, x0, x1)[0]
        bx1 = _xmap(np.array([hi]), 0, f_max, x0, x1)[0]
        parts.append(
            f'<rect x="{bx0:.1f}" y="{m_top}" width="{bx1 - bx0:.1f}" '
            f'height="{hh}" fill="#ffffff" opacity="0.03"/>'
        )
    parts.append(
        f'<rect x="{x0}" y="{m_top}" width="{x1 - x0}" height="{hh}" '
        f'fill="none" stroke="{C_BORDER}"/>'
    )
    for tick in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
        gx = _xmap(np.array([tick]), 0, f_max, x0, x1)[0]
        parts.append(
            f'<line x1="{gx:.1f}" y1="{m_top}" x2="{gx:.1f}" '
            f'y2="{m_top + hh}" stroke="{C_GRID}"/>'
        )
        parts.append(
            f'<text x="{gx:.1f}" y="{m_top + hh + 14}" fill="{C_MUTED}" '
            f'text-anchor="middle">{tick:g}</text>'
        )
    parts.append(_poly(xs, ys) + f' stroke="{C_ACCENT}" stroke-width="1.1"/>')
    parts.append(
        f'<text x="{x0}" y="{m_top - 3}" fill="{C_MUTED}">power spectrum</text>'
    )
    parts.append(
        f'<text x="{(x0 + x1) / 2:.0f}" y="{m_top + hh + 28}" fill="{C_MUTED}" '
        f'text-anchor="middle">frequency (Hz)</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def severity_color(z: float) -> str:
    az = abs(z)
    if az > OUTLIER_Z:
        return C_SEV_BAD
    if az > 2.0:
        return C_SEV_WARN
    return C_SEV_OK


def metric_glyph_svg(table: MetricTable, row: int, col: int) -> str:
    """Dot-on-distribution strip for one metric: bands, signal ticks, this IC."""
    w, h = 170, 22
    zx0, zx1 = 6.0, 164.0

    def zx(z: float) -> float:
        z = max(-METRIC_CLIP, min(METRIC_CLIP, z))
        return zx0 + (z + METRIC_CLIP) / (2 * METRIC_CLIP) * (zx1 - zx0)

    zcol = table.z[:, col]
    p5, p25, p75, p95 = (float(v) for v in np.percentile(zcol, (5, 25, 75, 95)))
    z_here = float(zcol[row])
    parts = [
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" class="glyph">'
    ]
    parts.append(
        f'<rect x="{zx(p5):.1f}" y="7" width="{zx(p95) - zx(p5):.1f}" height="8" '
        f'rx="2" fill="#2a3140"/>'
    )
    parts.append(
        f'<rect x="{zx(p25):.1f}" y="5" width="{zx(p75) - zx(p25):.1f}" height="12" '
        f'rx="2" fill="#39424f"/>'
    )
    parts.append(
        f'<line x1="{zx(0):.1f}" y1="2" x2="{zx(0):.1f}" y2="20" stroke="#4a5261"/>'
    )
    for s in table.signal_rows:
        sx = zx(float(zcol[s]))
        parts.append(
            f'<line x1="{sx:.1f}" y1="4" x2="{sx:.1f}" y2="18" '
            f'stroke="{C_SIGNAL}" stroke-width="1.4" opacity="0.85"/>'
        )
    parts.append(
        f'<circle cx="{zx(z_here):.1f}" cy="11" r="4.2" '
        f'fill="{severity_color(z_here)}" stroke="{C_BG}" stroke-width="1.2"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def prob_strip_svg(
    verdicts: list[Verdict],
    current_row: int,
    ratings: dict[int, str] | None = None,
) -> str:
    """All 96 P(signal) values on a log axis, with the threshold and this IC.

    With `ratings`, ticks for rated components are tall and colored by the
    human label (a color on the "wrong" side of the threshold line marks a
    disagreement with FIX); unrated ticks are short, faint, FIX-colored.
    """
    w, h = 340, 52
    x0, x1 = 16.0, w - 10.0
    lo_exp = -5.0

    def px(p: float) -> float:
        lp = max(lo_exp, np.log10(max(p, 1e-9)))
        return x0 + (lp - lo_exp) / (0 - lo_exp) * (x1 - x0)

    parts = [
        (
            f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="system-ui" font-size="10" class="chart">'
        )
    ]
    parts.append(f'<line x1="{x0}" y1="30" x2="{x1}" y2="30" stroke="{C_BORDER}"/>')
    for e in range(int(lo_exp), 1):
        gx = px(10.0**e)
        parts.append(
            f'<line x1="{gx:.1f}" y1="26" x2="{gx:.1f}" y2="34" stroke="{C_BORDER}"/>'
        )
        label = "1" if e == 0 else f"1e{e}"
        parts.append(
            f'<text x="{gx:.1f}" y="46" fill="{C_MUTED}" text-anchor="middle">{label}</text>'
        )
    tx = px(FIX_THRESHOLD)
    parts.append(
        f'<line x1="{tx:.1f}" y1="6" x2="{tx:.1f}" y2="38" stroke="{C_UNKNOWN}" '
        f'stroke-dasharray="3 3"/>'
    )
    parts.append(
        f'<text x="{tx + 3:.1f}" y="12" fill="{C_UNKNOWN}">thr {FIX_THRESHOLD}</text>'
    )
    for i, v in enumerate(verdicts):
        fix_color = C_SIGNAL if v.label == "Signal" else C_NOISE
        rating = ratings.get(i + 1) if ratings is not None else None
        if rating is not None:
            color, opacity, y_lo, y_hi, sw = RATING_COLORS[rating], 0.95, 20, 40, 1.6
        elif ratings is not None:
            color, opacity, y_lo, y_hi, sw = fix_color, 0.28, 25, 35, 1.2
        else:
            color, opacity, y_lo, y_hi, sw = fix_color, 0.55, 22, 38, 1.2
        parts.append(
            f'<line x1="{px(v.p_signal):.1f}" y1="{y_lo}" x2="{px(v.p_signal):.1f}" '
            f'y2="{y_hi}" stroke="{color}" stroke-width="{sw}" opacity="{opacity}"/>'
        )
    cur = verdicts[current_row]
    parts.append(
        f'<circle cx="{px(cur.p_signal):.1f}" cy="30" r="5" fill="{C_ACCENT}" '
        f'stroke="{C_BG}" stroke-width="1.5"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------
# HTML fragments
# --------------------------------------------------------------------------


def fmt_val(v: float) -> str:
    return f"{v:.3g}"


def metric_row_html(
    table: MetricTable, row: int, col: int, label: str | None = None
) -> str:
    z = float(table.z[row, col])
    return (
        '<div class="metric-row">'
        f'<span class="metric-name" title="{table.names[col]}">{label or table.names[col]}</span>'
        f"{metric_glyph_svg(table, row, col)}"
        f'<span class="metric-val">{fmt_val(float(table.raw[row, col]))}'
        f'<em style="color:{severity_color(z)}">z={z:+.1f}</em></span>'
        "</div>"
    )


def outlier_chip_html(n_out: int, anchor: str) -> str:
    if n_out == 0:
        return '<span class="chip chip-ok">no outlying metrics</span>'
    return (
        f'<a class="chip chip-alert" href="#{anchor}">&#9888; {n_out} outlier '
        f"metric{'s' if n_out != 1 else ''} (|z|&gt;{OUTLIER_Z:g})</a>"
    )


def metrics_panel_html(
    table: MetricTable,
    row: int,
    ic: int,
    max_outliers: int | None = None,
    include_pinned: bool = True,
) -> str:
    outliers = outlier_indices(table, row)
    pinned = pinned_indices(table, row)
    anchor = f"outliers-{ic}"
    parts = [f'<div class="card metrics-card" id="{anchor}">']
    parts.append(
        '<div class="card-title">FIX metrics '
        f"<span>{outlier_chip_html(len(outliers), anchor)}</span></div>"
    )
    # outliers first — this is where a reviewer's attention should go
    if outliers:
        shown = outliers if max_outliers is None else outliers[:max_outliers]
        parts.append(
            f'<div class="metric-section">outliers (|z| &gt; {OUTLIER_Z:g})</div>'
        )
        parts.extend(metric_row_html(table, row, j) for j in shown)
        if len(shown) < len(outliers):
            parts.append(
                f'<div class="metric-more">+ {len(outliers) - len(shown)} more outliers '
                "(expand families below)</div>"
            )
    else:
        parts.append(
            f'<div class="metric-empty">no metric exceeds |z| &gt; {OUTLIER_Z:g} '
            "— nothing anomalous</div>"
        )
    if include_pinned:
        parts.append('<div class="metric-section">pinned</div>')
        parts.extend(metric_row_html(table, row, j, label) for label, j in pinned)
    # complete families, collapsed
    parts.append('<div class="metric-section">all families</div>')
    fams = family_members(table)
    for fam in sorted(fams):
        members = fams[fam]
        max_z = float(np.abs(table.z[row, members]).max())
        chip = (
            f'<span class="famz" style="color:{severity_color(max_z)}">'
            f"max|z| {max_z:.1f}</span>"
        )
        rows = "".join(metric_row_html(table, row, j) for j in members)
        parts.append(
            f"<details><summary>{fam} <span class='fam-n'>{len(members)}</span> {chip}"
            f"</summary>{rows}</details>"
        )
    parts.append("</div>")
    return "".join(parts)


def verdict_chip_html(v: Verdict) -> str:
    color = C_SIGNAL if v.label == "Signal" else C_NOISE
    return (
        f'<span class="verdict" style="color:{color}">{v.label.upper()}</span> '
        f'<span class="verdict-p">P(signal) = {fmt_val(v.p_signal)} '
        f"<em>vs thr {FIX_THRESHOLD}</em></span>"
    )


def rating_buttons_html(selected: str | None = None) -> str:
    buttons = []
    for label, kbds in (("Signal", "1s"), ("Unknown", "2u"), ("Noise", "3n")):
        sel = " selected" if selected == label else ""
        keys = "".join(f"<kbd>{k}</kbd>" for k in kbds)
        buttons.append(
            f'<button class="rate rate-{label.lower()}{sel}" data-rate="{label}">'
            f"{label} {keys}</button>"
        )
    return '<div class="rate-buttons">' + "".join(buttons) + "</div>"


@dataclass(frozen=True)
class ComponentPartials:
    ic: int  # 1-based
    role: str
    verdict: Verdict
    expl_var: float
    total_var: float
    n_outliers: int
    tc_fd_narrow: str
    tc_fd_wide: str
    spec_narrow: str
    spec_wide: str
    lightbox_uri: str
    lightbox_cor_uri: str
    lightbox_sag_uri: str
    ortho_uri: str
    strip_uri: str
    metrics_compact: str
    metrics_full: str
    metrics_no_pin: str
    prob_strip: str
    prob_strip_rated: str


def build_partials(
    ic: int,
    role: str,
    mix: np.ndarray,
    ftmix: np.ndarray,
    freqs: np.ndarray,
    fd: np.ndarray,
    icstats: np.ndarray,
    verdicts: list[Verdict],
    table: MetricTable,
    tr: float,
    bg: np.ndarray,
    window: tuple[float, float],
    ic_img: nib.nifti1.Nifti1Image,
    lightbox_zs: list[int],
    strip_zs: list[int],
    cor_picks: list[int],
    sag_picks: list[int],
    ratings: dict[int, str],
) -> ComponentPartials:
    row = ic - 1
    ov = canonical_vol(ic_img, row)
    peak_flat = int(np.abs(ov).argmax())
    peak = tuple(int(i) for i in np.unravel_index(peak_flat, ov.shape))
    assert len(peak) == 3
    ts = mix[:, row]
    ft = ftmix[:, row]
    return ComponentPartials(
        ic=ic,
        role=role,
        verdict=verdicts[row],
        expl_var=float(icstats[row, 0]),
        total_var=float(icstats[row, 1]),
        n_outliers=len(outlier_indices(table, row)),
        tc_fd_narrow=timecourse_fd_svg(ts, fd, tr, width=376),
        tc_fd_wide=timecourse_fd_svg(ts, fd, tr, width=760),
        spec_narrow=spectrum_svg(ft, freqs, width=376),
        spec_wide=spectrum_svg(ft, freqs, width=760),
        lightbox_uri=encode_img(render_lightbox(bg, ov, lightbox_zs, window)),
        lightbox_cor_uri=encode_img(render_lightbox(bg, ov, cor_picks, window, axis=1)),
        lightbox_sag_uri=encode_img(render_lightbox(bg, ov, sag_picks, window, axis=0)),
        ortho_uri=encode_img(render_ortho(bg, ov, (peak[0], peak[1], peak[2]), window)),
        strip_uri=encode_img(render_strip(bg, ov, strip_zs, window)),
        metrics_compact=metrics_panel_html(table, row, ic, max_outliers=5),
        metrics_full=metrics_panel_html(table, row, ic),
        metrics_no_pin=metrics_panel_html(table, row, ic, include_pinned=False),
        prob_strip=prob_strip_svg(verdicts, row),
        prob_strip_rated=prob_strip_svg(verdicts, row, ratings),
    )


# --------------------------------------------------------------------------
# Page assembly
# --------------------------------------------------------------------------

CSS_SHARED = Template("""
* { box-sizing: border-box; margin: 0; }
html, body { height: 100%; }
html { overflow-x: auto; }
body {
  background: ${bg}; color: ${text};
  font: 13px/1.45 system-ui, -apple-system, sans-serif;
  display: flex; flex-direction: column; overflow: hidden;
  min-width: 1280px;
}
.tabs {
  display: flex; gap: 6px; align-items: center; padding: 0 12px;
  height: 40px; flex: none; background: ${panel}; border-bottom: 1px solid ${border};
}
.tabs .brand { font-weight: 700; margin-right: 12px; color: ${accent}; }
.tabs .note { margin-left: auto; color: ${muted}; font-size: 12px; }
.tab {
  background: none; border: 1px solid ${border}; border-radius: 6px;
  color: ${muted}; padding: 4px 12px; cursor: pointer; font: inherit;
}
.tab.active { color: ${text}; background: ${panel2}; border-color: ${accent}; }
[data-ic-section] { display: none; min-height: 0; flex: 1; }
.card {
  background: ${panel}; border: 1px solid ${border}; border-radius: 8px;
  padding: 10px 12px; margin-bottom: 10px;
}
.card-title {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
  color: ${muted}; margin-bottom: 6px; display: flex; gap: 8px;
  align-items: center; justify-content: space-between;
}
.chart { width: 100%; height: auto; display: block; }
.chip {
  display: inline-block; border-radius: 999px; padding: 2px 10px;
  font-size: 11px; text-decoration: none;
}
.chip-ok { background: #232a35; color: ${muted}; }
.chip-alert { background: #3a1f24; color: ${bad}; border: 1px solid ${bad}; }
.metric-row { display: flex; align-items: center; gap: 8px; padding: 1px 0; }
.metric-name {
  flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; color: ${text}; font-size: 12px;
}
.glyph { flex: none; width: 150px; height: 20px; }
.metric-val {
  flex: none; width: 96px; text-align: right; font-size: 11px;
  color: ${muted}; font-variant-numeric: tabular-nums;
}
.metric-val em { font-style: normal; margin-left: 5px; }
.metric-section {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  color: ${muted}; margin: 8px 0 3px; border-bottom: 1px solid ${border};
}
.metric-empty { color: ${muted}; font-size: 12px; padding: 4px 0; }
.metric-more { color: ${muted}; font-size: 11px; padding: 3px 0; }
details { margin: 2px 0; }
summary { cursor: pointer; color: ${text}; font-size: 12px; padding: 2px 0; }
summary .fam-n { color: ${muted}; font-size: 10px; }
summary .famz { font-size: 10px; float: right; }
.montage { width: 100%; height: auto; display: block; border-radius: 4px; }
.montage-caption { color: ${muted}; font-size: 11px; margin-top: 4px; }
.verdict { font-weight: 700; font-size: 15px; }
.verdict-p { color: ${text}; }
.verdict-p em { color: ${muted}; font-style: normal; font-size: 11px; }
.rate-buttons { display: flex; gap: 8px; }
button.rate {
  flex: 1; font: inherit; font-weight: 600; padding: 9px 0; cursor: pointer;
  border-radius: 8px; background: ${panel2}; color: ${text};
  border: 1px solid ${border};
}
button.rate kbd {
  font: 10px ui-monospace, monospace; background: #00000055;
  border-radius: 3px; padding: 1px 4px; margin-left: 3px; color: ${muted};
}
button.rate-signal.selected { background: #14351f; border-color: ${signal}; color: ${signal}; }
button.rate-unknown.selected { background: #3a2f12; border-color: ${unknown}; color: ${unknown}; }
button.rate-noise.selected { background: #3a1a1a; border-color: ${bad}; color: ${bad}; }
.meta-chip {
  background: ${panel2}; border: 1px solid ${border}; border-radius: 6px;
  padding: 2px 8px; font-size: 11px; color: ${muted}; white-space: nowrap;
}
.nav-btn {
  background: ${panel2}; color: ${text}; border: 1px solid ${border};
  border-radius: 6px; padding: 3px 10px; font: inherit; cursor: pointer;
}
.pos { font-weight: 700; font-size: 15px; }
input.jump {
  width: 70px; background: ${panel2}; color: ${text}; font: inherit;
  border: 1px solid ${border}; border-radius: 6px; padding: 3px 8px;
}
""").substitute(
    bg=C_BG,
    panel=C_PANEL,
    panel2=C_PANEL2,
    border=C_BORDER,
    text=C_TEXT,
    muted=C_MUTED,
    accent=C_ACCENT,
    signal=C_SIGNAL,
    unknown=C_UNKNOWN,
    bad=C_SEV_BAD,
)

JS_SHARED = Template("""
(() => {
  const ics = [${ics}];
  let cur = 0;
  function show(i) {
    cur = (i + ics.length) % ics.length;
    document.querySelectorAll('[data-ic-section]').forEach(s =>
      s.classList.toggle('active', Number(s.dataset.icSection) === ics[cur]));
    document.querySelectorAll('[data-ic-tab]').forEach(t =>
      t.classList.toggle('active', Number(t.dataset.icTab) === ics[cur]));
    document.querySelectorAll('[data-queue-ic]').forEach(r =>
      r.classList.toggle('current', Number(r.dataset.queueIc) === ics[cur]));
  }
  function rate(label) {
    const sec = document.querySelector('[data-ic-section].active');
    if (!sec) return;
    sec.querySelectorAll('[data-rate]').forEach(b =>
      b.classList.toggle('selected', b.dataset.rate === label));
  }
  document.addEventListener('click', e => {
    const tab = e.target.closest('[data-ic-tab]');
    if (tab) { show(ics.indexOf(Number(tab.dataset.icTab))); return; }
    const ax = e.target.closest('[data-axis-btn]');
    if (ax) {
      const sec = ax.closest('[data-ic-section]');
      sec.querySelectorAll('[data-axis-btn]').forEach(b =>
        b.classList.toggle('active', b === ax));
      sec.querySelectorAll('[data-axis-img]').forEach(img =>
        img.classList.toggle('active', img.dataset.axisImg === ax.dataset.axisBtn));
      return;
    }
    const q = e.target.closest('[data-queue-ic]');
    if (q) {
      const i = ics.indexOf(Number(q.dataset.queueIc));
      if (i >= 0) show(i);
      return;
    }
    const b = e.target.closest('[data-rate]');
    if (b) rate(b.dataset.rate);
  });
  document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT') return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'ArrowRight') show(cur + 1);
    else if (e.key === 'ArrowLeft') show(cur - 1);
    else if (e.key === '1' || e.key === 's') rate('Signal');
    else if (e.key === '2' || e.key === 'u') rate('Unknown');
    else if (e.key === '3' || e.key === 'n') rate('Noise');
    else if (e.key === 'g') {
      const jump = document.querySelector('[data-ic-section].active .jump');
      if (jump) { jump.focus(); e.preventDefault(); }
    }
  });
  show(0);
})();
""")


def tabs_html(partials: list[ComponentPartials], variant: str, n_total: int) -> str:
    tabs = "".join(
        f'<button class="tab" data-ic-tab="{p.ic}">IC {p.ic} '
        f'<span style="color:{C_MUTED};font-size:11px">({p.role})</span></button>'
        for p in partials
    )
    return (
        '<div class="tabs"><span class="brand">melrater</span>'
        f"{tabs}"
        f'<span class="note">mockup {variant} · {RUN_LABEL} · '
        f"{len(partials)} of {n_total} components rendered</span></div>"
    )


def toolbar_html(p: ComponentPartials, n_total: int, tr: float) -> str:
    return (
        '<div class="toolbar">'
        f'<button class="nav-btn">&#8592;</button>'
        f'<span class="pos">IC {p.ic} / {n_total}</span>'
        f'<button class="nav-btn">&#8594;</button>'
        f'<input class="jump" placeholder="go to&#8230;">'
        f'<span class="meta-chip">{RUN_LABEL}</span>'
        f'<span class="meta-chip">TR {tr:g} s</span>'
        f'<span class="meta-chip">expl. var {p.expl_var:.2f}%</span>'
        f'<span class="meta-chip">total var {p.total_var:.2f}%</span>'
        f'<span style="margin-left:auto">{outlier_chip_html(p.n_outliers, f"outliers-{p.ic}")}</span>'
        "</div>"
    )


MONTAGE_CAPTION = (
    f"neurological (subject L on image left) · |z| &ge; {Z_THRESH:g} · "
    "red-yellow +, blue-lightblue &minus; · labels are axial voxel indices"
)


def build_variant_a(partials: list[ComponentPartials], n_total: int, tr: float) -> str:
    css = CSS_SHARED + Template("""
[data-ic-section].active {
  display: grid;
  grid-template:
    "toolbar toolbar" 48px
    "brain   side"    minmax(0, 1fr)
    "ratebar ratebar" 64px
    / minmax(0, 1fr) 400px;
}
.toolbar {
  grid-area: toolbar; display: flex; gap: 10px; align-items: center;
  padding: 0 14px; border-bottom: 1px solid ${border}; background: ${panel};
}
.brain { grid-area: brain; overflow-y: auto; padding: 14px; }
.side { grid-area: side; overflow-y: auto; padding: 12px; border-left: 1px solid ${border}; }
.ratebar {
  grid-area: ratebar; display: flex; align-items: center; gap: 18px;
  padding: 0 16px; background: ${panel}; border-top: 1px solid ${border};
}
.ratebar .rate-buttons { flex: 0 0 420px; margin-left: auto; }
.progress { color: ${muted}; font-size: 12px; }
""").substitute(border=C_BORDER, panel=C_PANEL, muted=C_MUTED)
    sections = []
    for p in partials:
        sections.append(
            f'<section data-ic-section="{p.ic}">'
            + toolbar_html(p, n_total, tr)
            + '<div class="brain">'
            + f'<img class="montage" style="max-width:548px" src="{p.ortho_uri}" alt="ortho">'
            + f'<img class="montage" style="margin-top:10px" src="{p.lightbox_uri}" alt="lightbox">'
            + f'<div class="montage-caption">{MONTAGE_CAPTION}</div>'
            + "</div>"
            + '<div class="side">'
            + f'<div class="card"><div class="card-title">timecourse + motion</div>{p.tc_fd_narrow}</div>'
            + f'<div class="card"><div class="card-title">spectrum</div>{p.spec_narrow}</div>'
            + p.metrics_compact
            + "</div>"
            + '<div class="ratebar">'
            + f'<span class="meta-chip">{FIX_REVIEWER}</span>'
            + f"<span>{verdict_chip_html(p.verdict)}</span>"
            + rating_buttons_html()
            + f'<span class="progress">0 / {n_total} rated</span>'
            + "</div>"
            + "</section>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>melrater mockup — variant A</title>"
        f"<style>{css}</style></head><body>"
        + tabs_html(partials, "A · fsleyes-familiar", n_total)
        + "".join(sections)
        + f"<script>{JS_SHARED.substitute(ics=', '.join(str(p.ic) for p in partials))}</script>"
        "</body></html>"
    )


def queue_html(
    verdicts: list[Verdict], icstats: np.ndarray, rendered: list[int]
) -> str:
    lo_exp = -5.0
    rows = []
    for i, v in enumerate(verdicts):
        ic = i + 1
        color = C_SIGNAL if v.label == "Signal" else C_NOISE
        frac = (max(lo_exp, float(np.log10(max(v.p_signal, 1e-9)))) - lo_exp) / -lo_exp
        marker = "&#9664;" if ic in rendered else ""
        rows.append(
            f'<div class="qrow" data-queue-ic="{ic}" '
            f'title="{"rendered in mockup" if ic in rendered else "not rendered in this mockup"}">'
            f'<span class="qic">IC {ic}</span>'
            f'<span class="qchip" style="color:{color}">{v.label[0]}</span>'
            f'<span class="qbar"><i style="width:{frac * 100:.0f}%;background:{color}"></i></span>'
            f'<span class="qvar">{icstats[i, 0]:.1f}%</span>'
            f'<span class="qmark">{marker}</span>'
            "</div>"
        )
    return '<div class="queue">' + "".join(rows) + "</div>"


def build_variant_b(
    partials: list[ComponentPartials],
    n_total: int,
    verdicts: list[Verdict],
    icstats: np.ndarray,
    tr: float,
) -> str:
    css = CSS_SHARED + Template("""
.page { display: grid; grid-template-columns: 230px minmax(0, 1fr); flex: 1; min-height: 0; }
.queue {
  overflow-y: auto; border-right: 1px solid ${border}; background: ${panel};
  padding: 6px 0;
}
.qrow {
  display: flex; align-items: center; gap: 6px; padding: 2px 10px;
  font-size: 11px; cursor: pointer; color: ${muted};
}
.qrow:hover { background: ${panel2}; }
.qrow.current { background: ${panel2}; color: ${text}; box-shadow: inset 2px 0 ${accent}; }
.qic { width: 40px; font-variant-numeric: tabular-nums; }
.qchip { width: 12px; font-weight: 700; }
.qbar { flex: 1; height: 5px; background: #262d38; border-radius: 3px; overflow: hidden; }
.qbar i { display: block; height: 100%; }
.qvar { width: 38px; text-align: right; font-variant-numeric: tabular-nums; }
.qmark { width: 12px; color: ${accent}; }
.rest { min-height: 0; display: flex; }
[data-ic-section].active {
  display: grid; width: 100%;
  grid-template-columns: minmax(0, 1fr) 380px;
}
.main { overflow-y: auto; padding: 12px 14px; }
.context { overflow-y: auto; padding: 12px; border-left: 1px solid ${border}; }
.header-row { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
.kbd-legend { color: ${muted}; font-size: 11px; margin-top: 8px; }
.kbd-legend kbd {
  font: 10px ui-monospace, monospace; background: ${panel2};
  border: 1px solid ${border}; border-radius: 3px; padding: 1px 4px;
}
.context .glyph { width: 128px; }
.context .metric-val { width: 88px; }
""").substitute(
        border=C_BORDER,
        panel=C_PANEL,
        panel2=C_PANEL2,
        muted=C_MUTED,
        text=C_TEXT,
        accent=C_ACCENT,
    )
    sections = []
    for p in partials:
        sections.append(
            f'<section data-ic-section="{p.ic}">'
            + '<div class="main">'
            + '<div class="header-row">'
            + '<button class="nav-btn">&#8592;</button>'
            + f'<span class="pos">IC {p.ic} / {n_total}</span>'
            + '<button class="nav-btn">&#8594;</button>'
            + '<input class="jump" placeholder="go to&#8230;">'
            + f'<span class="meta-chip">expl. var {p.expl_var:.2f}%</span>'
            + f'<span class="meta-chip">total var {p.total_var:.2f}%</span>'
            + f'<span class="meta-chip">TR {tr:g} s</span>'
            + "</div>"
            + '<div class="card"><div class="card-title">timecourse + motion '
            + f"(time-aligned)</div>{p.tc_fd_wide}</div>"
            + f'<div class="card"><div class="card-title">spectrum</div>{p.spec_wide}</div>'
            + '<div class="card"><div class="card-title">spatial map</div>'
            + f'<img class="montage" src="{p.strip_uri}" alt="axial strip">'
            + f'<img class="montage" style="max-width:548px;margin-top:8px" src="{p.ortho_uri}" alt="ortho">'
            + "<details><summary>full lightbox</summary>"
            + f'<img class="montage" style="margin-top:8px" src="{p.lightbox_uri}" alt="lightbox"></details>'
            + f'<div class="montage-caption">{MONTAGE_CAPTION}</div>'
            + "</div></div>"
            + '<div class="context">'
            + '<div class="card"><div class="card-title">FIX verdict '
            + f'<span class="meta-chip">{FIX_REVIEWER}</span></div>'
            + f"<div>{verdict_chip_html(p.verdict)}</div>{p.prob_strip}</div>"
            + '<div class="card"><div class="card-title">your rating '
            + '<span class="meta-chip">psadil</span></div>'
            + rating_buttons_html()
            + '<div class="kbd-legend"><kbd>1</kbd>/<kbd>s</kbd> signal &nbsp;'
            + "<kbd>2</kbd>/<kbd>u</kbd> unknown &nbsp;<kbd>3</kbd>/<kbd>n</kbd> noise &nbsp;"
            + "<kbd>&#8592;</kbd><kbd>&#8594;</kbd> prev/next &nbsp;<kbd>g</kbd> jump</div>"
            + "</div>"
            + p.metrics_full
            + "</div>"
            + "</section>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>melrater mockup — variant B</title>"
        f"<style>{css}</style></head><body>"
        + tabs_html(partials, "B · context-dense triage", n_total)
        + '<div class="page">'
        + queue_html(verdicts, icstats, [p.ic for p in partials])
        + '<div class="rest">'
        + "".join(sections)
        + "</div></div>"
        + f"<script>{JS_SHARED.substitute(ics=', '.join(str(p.ic) for p in partials))}</script>"
        "</body></html>"
    )


MONTAGE_CAPTION_C = (
    f"|z| &ge; {Z_THRESH:g} · red-yellow +, blue-lightblue &minus; · labels are "
    "voxel indices · axial/coronal shown neurological (subject L on image left)"
)


def build_variant_c(
    partials: list[ComponentPartials],
    n_total: int,
    tr: float,
    ratings: dict[int, str],
) -> str:
    """Merged design: A's lightbox-dominant layout with a slice-axis switcher
    (no ortho), B's P(signal) strip in the sidebar colored by human ratings,
    compact charts, and the metrics panel without the pinned section."""
    css = CSS_SHARED + Template("""
[data-ic-section].active {
  display: grid;
  grid-template:
    "toolbar toolbar" 48px
    "brain   side"    minmax(0, 1fr)
    "ratebar ratebar" 64px
    / minmax(0, 1fr) 400px;
}
.toolbar {
  grid-area: toolbar; display: flex; gap: 10px; align-items: center;
  padding: 0 14px; border-bottom: 1px solid ${border}; background: ${panel};
}
.brain { grid-area: brain; overflow-y: auto; padding: 14px; }
.side { grid-area: side; overflow-y: auto; padding: 12px; border-left: 1px solid ${border}; }
.ratebar {
  grid-area: ratebar; display: flex; align-items: center; gap: 18px;
  padding: 0 16px; background: ${panel}; border-top: 1px solid ${border};
}
.ratebar .rate-buttons { flex: 0 0 480px; }
.progress { color: ${muted}; font-size: 12px; margin-left: auto; }
.axis-btns { display: flex; gap: 6px; margin-bottom: 10px; }
.axis-btn {
  background: none; border: 1px solid ${border}; border-radius: 6px;
  color: ${muted}; padding: 3px 12px; cursor: pointer; font: inherit; font-size: 12px;
}
.axis-btn.active { color: ${text}; background: ${panel2}; border-color: ${accent}; }
[data-axis-img] { display: none; max-width: 908px; }
[data-axis-img].active { display: block; }
.strip-legend { color: ${muted}; font-size: 11px; margin-top: 4px; }
""").substitute(
        border=C_BORDER,
        panel=C_PANEL,
        panel2=C_PANEL2,
        muted=C_MUTED,
        text=C_TEXT,
        accent=C_ACCENT,
    )
    axes = (
        ("axial", "lightbox_uri"),
        ("coronal", "lightbox_cor_uri"),
        ("sagittal", "lightbox_sag_uri"),
    )
    sections = []
    for p in partials:
        axis_btns = "".join(
            f'<button class="axis-btn{" active" if name == "axial" else ""}" '
            f'data-axis-btn="{name}">{name}</button>'
            for name, _ in axes
        )
        axis_imgs = "".join(
            f'<img class="montage{" active" if name == "axial" else ""}" '
            f'data-axis-img="{name}" src="{getattr(p, attr)}" alt="{name} lightbox">'
            for name, attr in axes
        )
        sections.append(
            f'<section data-ic-section="{p.ic}">'
            + toolbar_html(p, n_total, tr)
            + '<div class="brain">'
            + f'<div class="axis-btns">{axis_btns}</div>'
            + axis_imgs
            + f'<div class="montage-caption">{MONTAGE_CAPTION_C}</div>'
            + "</div>"
            + '<div class="side">'
            + '<div class="card"><div class="card-title">FIX verdict '
            + f'<span class="meta-chip">{FIX_REVIEWER}</span></div>'
            + f"<div>{verdict_chip_html(p.verdict)}</div>{p.prob_strip_rated}"
            + '<div class="strip-legend">tall tick = rated (color = human label) · '
            + "faint tick = unrated (FIX label) · dot = this IC</div></div>"
            + f'<div class="card"><div class="card-title">timecourse + motion</div>{p.tc_fd_narrow}</div>'
            + f'<div class="card"><div class="card-title">spectrum</div>{p.spec_narrow}</div>'
            + p.metrics_no_pin
            + "</div>"
            + '<div class="ratebar">'
            + rating_buttons_html(ratings.get(p.ic))
            + f'<span class="progress">{len(ratings)} / {n_total} rated</span>'
            + "</div>"
            + "</section>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>melrater mockup — variant C</title>"
        f"<style>{css}</style></head><body>"
        + tabs_html(partials, "C · merged", n_total)
        + "".join(sections)
        + f"<script>{JS_SHARED.substitute(ics=', '.join(str(p.ic) for p in partials))}</script>"
        "</body></html>"
    )


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--debug-img",
        action="store_true",
        help="also dump montage images as PNGs to the scratchpad dir",
    )
    args = parser.parse_args()

    tr = load_tr()
    mix = np.loadtxt(ICA_DIR / "melodic_mix")
    ftmix = np.loadtxt(ICA_DIR / "melodic_FTmix")
    icstats = np.loadtxt(ICA_DIR / "melodic_ICstats")
    fd = load_fd()
    verdicts = load_verdicts()
    feat_names, feat_raw = load_features()
    table = build_metric_table(feat_names, feat_raw, verdicts)

    _, n_ic = mix.shape
    # FTmix rows are the positive-frequency bins of a zero-padded FFT of length
    # 2*n_bins (436 here), DC dropped — so the last bin sits exactly at Nyquist.
    freqs = np.arange(1, ftmix.shape[0] + 1) / (2 * ftmix.shape[0] * tr)

    print(
        f"TR = {tr:g} s | mix {mix.shape} | FTmix {ftmix.shape} | ICstats {icstats.shape}"
    )
    print(
        f"nyquist ~ {freqs[-1]:.4f} Hz | FD n={len(fd)} median={np.median(fd):.3f} max={fd.max():.3f} mm"
    )
    print(
        f"verdicts: {len(verdicts)} | Signal: {len(table.signal_rows)} -> {[i + 1 for i in table.signal_rows]}"
    )
    print(f"IC 2 verdict: {verdicts[1].label} @ {verdicts[1].p_signal}")
    print(
        f"features kept {len(table.names)} / {len(feat_names)}; dropped constant: {table.dropped}"
    )
    print(f"AVIF encode: {'yes' if AVIF_OK else 'NO - falling back to PNG'}")

    mean_img = nib.load(ICA_DIR / "mean.nii.gz")
    mask_img = nib.load(ICA_DIR / "mask.nii.gz")
    ic_img = nib.load(ICA_DIR / "melodic_IC.nii.gz")
    assert isinstance(mean_img, nib.nifti1.Nifti1Image)
    assert isinstance(mask_img, nib.nifti1.Nifti1Image)
    assert isinstance(ic_img, nib.nifti1.Nifti1Image)
    bg = canonical_vol(mean_img)
    mask = canonical_vol(mask_img)
    inside = bg[mask > 0]
    window = (float(np.percentile(inside, 2)), float(np.percentile(inside, 98)))
    lightbox_zs = axis_picks(mask, 2, N_LIGHTBOX)
    strip_zs = axis_picks(mask, 2, N_STRIP)
    cor_picks = axis_picks(mask, 1, N_LIGHTBOX)
    sag_picks = axis_picks(mask, 0, N_LIGHTBOX)
    ratings = mock_user_ratings(verdicts)
    print(
        f"axial slices: lightbox {lightbox_zs[0]}..{lightbox_zs[-1]} (n={len(lightbox_zs)}), strip n={len(strip_zs)}"
    )
    print(
        f"coronal picks n={len(cor_picks)}, sagittal picks n={len(sag_picks)} | "
        f"mock ratings: {len(ratings)} ICs, overrides {MOCK_RATING_OVERRIDES}"
    )

    partials = []
    for ic, role in COMPONENTS:
        p = build_partials(
            ic,
            role,
            mix,
            ftmix,
            freqs,
            fd,
            icstats,
            verdicts,
            table,
            tr,
            bg,
            window,
            ic_img,
            lightbox_zs,
            strip_zs,
            cor_picks,
            sag_picks,
            ratings,
        )
        row = ic - 1
        outs = outlier_indices(table, row)
        top = ", ".join(f"{table.names[j]}({table.z[row, j]:+.1f})" for j in outs[:5])
        print(
            f"IC {ic} ({role}): {p.verdict.label} p={p.verdict.p_signal:g}, "
            f"{len(outs)} outliers: {top or 'none'}"
        )
        partials.append(p)

    html_a = build_variant_a(partials, n_ic, tr)
    html_b = build_variant_b(partials, n_ic, verdicts, icstats, tr)
    html_c = build_variant_c(partials, n_ic, tr, ratings)
    for name, html in (
        ("variant_a.html", html_a),
        ("variant_b.html", html_b),
        ("variant_c.html", html_c),
    ):
        out = OUT_DIR / name
        out.write_text(html)
        print(f"wrote {out} ({len(html) / 1e6:.2f} MB)")

    if args.debug_img:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        for ic, _ in COMPONENTS:
            ov = canonical_vol(ic_img, ic - 1)
            peak_flat = int(np.abs(ov).argmax())
            px, py, pz = (int(i) for i in np.unravel_index(peak_flat, ov.shape))
            render_lightbox(bg, ov, lightbox_zs, window).save(
                DEBUG_DIR / f"ic{ic}_lightbox.png"
            )
            render_ortho(bg, ov, (px, py, pz), window).save(
                DEBUG_DIR / f"ic{ic}_ortho.png"
            )
        print(f"debug images in {DEBUG_DIR}")


if __name__ == "__main__":
    main()
