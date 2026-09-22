"""Server-rendered SVG charts for the rating screen.

Hand-rolled SVG (no plotting library): small, crisp, and safe to inline.
All functions are pure and operate on plain sequences (as stored in JSON
fields), so views can call them straight from model data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from melrater.core.melodic import FRISTON_K
from melrater.core.metrics import OUTLIER_Z, MetricFrame

METRIC_CLIP = 6.0  # display clip for metric z-scores
FD_REFS = (0.2, 0.5)  # mm reference lines
PROB_FLOOR_EXP = -5.0  # left edge of the log P(signal) axis
R2_METER_W = 52.0  # width of the motion-R^2 meter on the FD panel

C_BG = "#12151a"
C_BORDER = "#2c3442"
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
LABEL_COLORS = {"Signal": C_SIGNAL, "Noise": C_NOISE, "Unknown": C_UNKNOWN}


def severity_color(z: float) -> str:
    az = abs(z)
    if az > OUTLIER_Z:
        return C_SEV_BAD
    if az > 2.0:
        return C_SEV_WARN
    return C_SEV_OK


def _poly(xs: np.ndarray, ys: np.ndarray) -> str:
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    return f'<polyline fill="none" points="{pts}"'


def _xmap(v: np.ndarray, lo: float, hi: float, x0: float, x1: float) -> np.ndarray:
    return x0 + (v - lo) / (hi - lo) * (x1 - x0)


def _motion_r2_parts(r2: float, n_timepoints: int, x1: float, y: float) -> list[str]:
    """The FD panel's motion-R^2 readout: the figure, a meter, and chance.

    The meter's faint tick is what an unrelated timecourse would score on this
    fit — `FRISTON_K` regressors over `n_timepoints` volumes explain
    K/(n-1) of anything by construction — so the bar is read against the tick
    and not against zero. Without it a reader would have to know the run
    length to know whether 0.15 was a lot.
    """
    x0 = x1 - R2_METER_W
    chance = FRISTON_K / max(n_timepoints - 1, 1)
    filled = R2_METER_W * min(max(r2, 0.0), 1.0)
    tick = x0 + R2_METER_W * min(max(chance, 0.0), 1.0)
    return [
        (
            f'<text x="{x0 - 6:.1f}" y="{y:.1f}" fill="{C_MUTED}" text-anchor="end">'
            f"motion R&#178; {r2:.2f}</text>"
        ),
        (
            f'<rect x="{x0:.1f}" y="{y - 10:.1f}" width="{R2_METER_W:.1f}" '
            f'height="7" fill="none" stroke="{C_BORDER}"/>'
        ),
        (
            f'<rect x="{x0:.1f}" y="{y - 10:.1f}" width="{filled:.1f}" height="7" '
            f'fill="{C_FD}" opacity="0.7"><title>motion R&#178; {r2:.2f}; '
            f"{FRISTON_K} regressors over {n_timepoints} volumes explain "
            f"{chance:.2f} by chance</title></rect>"
        ),
        (
            f'<line x1="{tick:.1f}" y1="{y - 12:.1f}" x2="{tick:.1f}" '
            f'y2="{y - 1:.1f}" stroke="{C_MUTED}"/>'
        ),
    ]


def timecourse_fd_svg(
    timecourse: Sequence[float],
    fd: Sequence[float],
    tr: float,
    motion_r2: float | None = None,
    width: int = 376,
) -> str:
    """IC timecourse and FD as two panels sharing one x axis.

    `motion_r2` rides on the FD panel's own label line. It is the same number
    for no two components and the same trace for all of them, which is the
    point: the FD curve says when the head moved, the figure says how much of
    *this* component moved with it. None for a run ingested before it was
    computed, and then nothing is drawn.
    """
    ts = np.asarray(timecourse, dtype=float)
    fd_arr = np.asarray(fd, dtype=float)
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
    fd_max = max(float(fd_arr.max()), FD_REFS[1] + 0.1)
    y_fd = fd_top + h_fd - (fd_arr / fd_max) * (h_fd - 6)

    parts = [
        (
            f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="system-ui" font-size="11" class="chart">'
        )
    ]
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
    for top, hh in ((m_top, h_ts), (fd_top, h_fd)):
        parts.append(
            f'<rect x="{x0}" y="{top}" width="{x1 - x0}" height="{hh}" '
            f'fill="none" stroke="{C_BORDER}"/>'
        )
    zy = m_top + h_ts / 2
    parts.append(f'<line x1="{x0}" y1="{zy}" x2="{x1}" y2="{zy}" stroke="{C_GRID}"/>')
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
    parts.append(
        f'<text x="{x0}" y="{m_top - 4}" fill="{C_MUTED}">IC timecourse (a.u.)</text>'
    )
    parts.append(f'<text x="{x0}" y="{fd_top - 4}" fill="{C_FD}">FD (mm)</text>')
    if motion_r2 is not None:
        parts.extend(_motion_r2_parts(motion_r2, len(ts), x1, fd_top - 4))
    parts.append(
        f'<text x="{(x0 + x1) / 2:.0f}" y="{fd_top + h_fd + 30}" fill="{C_MUTED}" '
        f'text-anchor="middle">time (s)</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def spectrum_svg(
    spectrum: Sequence[float], frequencies: Sequence[float], width: int = 376
) -> str:
    ft = np.asarray(spectrum, dtype=float)
    freqs = np.asarray(frequencies, dtype=float)
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
    # shade outside the typical resting-state band (0.01-0.1 Hz)
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
    for tick in np.arange(0.0, f_max + 1e-9, 0.1):
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


def metric_glyph_svg(frame: MetricFrame) -> str:
    """Dot-on-distribution strip for one metric: bands, Signal ticks, this IC.

    The bands are whichever distribution the frame chose — the cohort when
    melrater has one, this scan's own components otherwise — and the rail
    along the top, drawn only in the first case, is where this scan's middle
    half falls inside it, notched at the scan's median. A rail sitting on the
    centre means the scan is ordinary for this feature and the dot can be read
    straight off the bands; a rail pushed to one side means the whole scan is
    unusual, and a dot in the middle of it is unremarkable *here*.
    """
    w, h = 170, 26
    zx0, zx1 = 6.0, 164.0

    def zx(value: float) -> float:
        clipped = max(-METRIC_CLIP, min(METRIC_CLIP, value))
        return zx0 + (clipped + METRIC_CLIP) / (2 * METRIC_CLIP) * (zx1 - zx0)

    parts = [
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" class="glyph">'
    ]
    if (scan := frame.scan_range) is not None:
        parts.append(
            f'<line x1="{zx(scan.p25):.1f}" y1="4" x2="{zx(scan.p75):.1f}" y2="4" '
            f'stroke="{C_MUTED}" stroke-width="1.6" opacity="0.7"/>'
        )
        parts.append(
            f'<line x1="{zx(scan.median):.1f}" y1="1" '
            f'x2="{zx(scan.median):.1f}" y2="7" stroke="{C_MUTED}"/>'
        )
    parts.append(
        f'<rect x="{zx(frame.p5):.1f}" y="11" '
        f'width="{zx(frame.p95) - zx(frame.p5):.1f}" height="8" rx="2" fill="#2a3140"/>'
    )
    parts.append(
        f'<rect x="{zx(frame.p25):.1f}" y="9" '
        f'width="{zx(frame.p75) - zx(frame.p25):.1f}" height="12" rx="2" '
        f'fill="#39424f"/>'
    )
    parts.append(
        f'<line x1="{zx(0):.1f}" y1="6" x2="{zx(0):.1f}" y2="24" stroke="#4a5261"/>'
    )
    for s in frame.signal_z:
        sx = zx(float(s))
        parts.append(
            f'<line x1="{sx:.1f}" y1="8" x2="{sx:.1f}" y2="22" '
            f'stroke="{C_SIGNAL}" stroke-width="1.4" opacity="0.85"/>'
        )
    parts.append(
        f'<circle cx="{zx(frame.z):.1f}" cy="15" r="4.2" '
        f'fill="{severity_color(frame.z)}" stroke="{C_BG}" stroke-width="1.2"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


@dataclass(frozen=True)
class ProbEntry:
    """One component's tick on the P(signal) strip."""

    index: int  # 1-based component index
    p_signal: float
    fix_label: str
    user_label: str | None


def prob_strip_svg(
    entries: Sequence[ProbEntry], current_ic: int, threshold: float
) -> str:
    """All components' P(signal) on a log axis with the decision threshold.

    Rated components get tall ticks colored by the human label — a color on
    the "wrong" side of the threshold line marks a disagreement with FIX;
    unrated ticks are short, faint, and FIX-colored.
    """
    w, h = 340, 52
    x0, x1 = 16.0, w - 10.0

    def px(p: float) -> float:
        lp = max(PROB_FLOOR_EXP, np.log10(max(p, 1e-9)))
        return x0 + (lp - PROB_FLOOR_EXP) / (0 - PROB_FLOOR_EXP) * (x1 - x0)

    parts = [
        (
            f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="system-ui" font-size="10" class="chart">'
        )
    ]
    parts.append(f'<line x1="{x0}" y1="30" x2="{x1}" y2="30" stroke="{C_BORDER}"/>')
    for e in range(int(PROB_FLOOR_EXP), 1):
        gx = px(10.0**e)
        parts.append(
            f'<line x1="{gx:.1f}" y1="26" x2="{gx:.1f}" y2="34" stroke="{C_BORDER}"/>'
        )
        label = "1" if e == 0 else f"1e{e}"
        parts.append(
            f'<text x="{gx:.1f}" y="46" fill="{C_MUTED}" text-anchor="middle">{label}</text>'
        )
    tx = px(threshold)
    parts.append(
        f'<line x1="{tx:.1f}" y1="6" x2="{tx:.1f}" y2="38" stroke="{C_UNKNOWN}" '
        f'stroke-dasharray="3 3"/>'
    )
    parts.append(
        f'<text x="{tx + 3:.1f}" y="12" fill="{C_UNKNOWN}">thr {threshold:g}</text>'
    )
    for entry in entries:
        fix_color = LABEL_COLORS.get(entry.fix_label, C_MUTED)
        if entry.user_label is not None:
            color = LABEL_COLORS.get(entry.user_label, C_MUTED)
            opacity, y_lo, y_hi, sw = 0.95, 20, 40, 1.6
        else:
            color, opacity, y_lo, y_hi, sw = fix_color, 0.28, 25, 35, 1.2
        parts.append(
            f'<line x1="{px(entry.p_signal):.1f}" y1="{y_lo}" '
            f'x2="{px(entry.p_signal):.1f}" y2="{y_hi}" '
            f'stroke="{color}" stroke-width="{sw}" opacity="{opacity}"/>'
        )
    cur = next((e for e in entries if e.index == current_ic), None)
    if cur is not None:
        parts.append(
            f'<circle cx="{px(cur.p_signal):.1f}" cy="30" r="5" fill="{C_ACCENT}" '
            f'stroke="{C_BG}" stroke-width="1.5"/>'
        )
    parts.append("</svg>")
    return "".join(parts)
