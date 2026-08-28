"""Readers for MELODIC+pyFIX derivative files.

Pure functions over the individual files FSL MELODIC and pyFIX write
(melodic_mix, melodic_FTmix, fix4melview_*.txt, ...). Which files belong to
which run is the catalog's business (see lake.py): everything here takes
explicit paths — collected in :class:`RunInputs` — and turns them into one
loaded :class:`MelodicSource`.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np

FIX_FILE_RE = re.compile(r"fix4melview_(?P<model>.+)_thr(?P<thr>\d+)\.txt$")


@dataclass(frozen=True)
class FixVerdict:
    label: str  # "Signal" | "Noise"
    p_signal: float


@dataclass(frozen=True)
class FixResult:
    model: str
    threshold: int
    verdicts: list[FixVerdict]

    @property
    def reviewer_name(self) -> str:
        return f"{self.model} @ thr{self.threshold}"


@dataclass(frozen=True)
class RunInputs:
    """One run's resolved inputs: local paths, plus the catalog-read tables.

    The motion parameters and per-component stats arrive as arrays rather
    than paths because the catalog ingests the ``.par`` and ``melodic_ICstats``
    files into its ``feat_motion``/``feat_icstats`` tables — there is nothing
    left to parse, only rows to read (see lake.py).
    """

    root: Path  # the run directory itself (stored as Run.path)
    label: str
    bold: Path  # filtered_func_data.nii.gz — the TR comes from its header
    mix: Path
    ftmix: Path
    features: Path  # fix/features.csv
    ic: Path  # melodic_IC.nii.gz
    mean: Path  # the montage background
    mask: Path  # the montage slice-picking mask
    classifications: tuple[Path, ...]  # fix4melview_*_thr*.txt
    motion: np.ndarray  # (n_timepoints, 6) mcflirt order: 3 rot (rad), 3 trans (mm)
    icstats: np.ndarray  # (n_components, 2): explained %, total %


@dataclass(frozen=True)
class MelodicSource:
    """Everything melrater needs from one run, loaded."""

    root: Path
    label: str
    tr: float
    mix: np.ndarray  # (n_timepoints, n_components) IC timecourses
    ftmix: np.ndarray  # (n_bins, n_components) power spectra
    icstats: np.ndarray  # (n_components, 2): explained %, total %
    fd: np.ndarray  # (n_timepoints,) framewise displacement (mm)
    frequencies: np.ndarray  # (n_bins,) Hz
    feature_names: list[str]
    features: np.ndarray  # (n_components, n_features)
    fix_results: list[FixResult]
    ic_path: Path  # montage inputs, opened only at render time
    mean_path: Path
    mask_path: Path

    @property
    def n_components(self) -> int:
        return int(self.mix.shape[1])

    @property
    def n_timepoints(self) -> int:
        return int(self.mix.shape[0])


def load_tr(bold: Path) -> float:
    header = nib.load(bold).header
    assert isinstance(header, nib.nifti1.Nifti1Header)
    return float(header["pixdim"][4])


def fd_power(params: np.ndarray) -> np.ndarray:
    """Power's framewise displacement from mcflirt motion parameters.

    mcflirt columns are 3 rotations (radians) then 3 translations (mm);
    FD = sum|dtrans| + 50mm * sum|drot|, with 0 prepended for the first frame.
    """
    rot, trans = params[:, :3], params[:, 3:]
    fd = np.abs(np.diff(trans, axis=0)).sum(axis=1) + 50.0 * np.abs(
        np.diff(rot, axis=0)
    ).sum(axis=1)
    return np.concatenate([[0.0], fd])


def load_features(path: Path) -> tuple[list[str], np.ndarray]:
    with open(path) as f:
        reader = csv.reader(f)
        names = next(reader)
        rows = [[float(v) for v in row] for row in reader if row]
    return names, np.asarray(rows)


def parse_fix_file(path: Path) -> FixResult:
    match = FIX_FILE_RE.search(path.name)
    if match is None:
        raise ValueError(f"not a fix4melview file: {path.name}")
    verdicts: list[FixVerdict] = []
    # first line is the ICA directory name; a trailing "[...]" line lists noise ICs
    for line in path.read_text().splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("["):
            break
        number, label, _, prob = (part.strip() for part in line.split(","))
        if int(number) != len(verdicts) + 1:
            raise ValueError(
                f"{path.name}: expected component {len(verdicts) + 1}, "
                f"found {number} — refusing to assign verdicts positionally"
            )
        verdicts.append(FixVerdict(label=label, p_signal=float(prob)))
    return FixResult(
        model=match["model"], threshold=int(match["thr"]), verdicts=verdicts
    )


def load_run(inputs: RunInputs) -> MelodicSource:
    mix = np.loadtxt(inputs.mix)
    ftmix = np.loadtxt(inputs.ftmix)
    icstats = inputs.icstats
    if mix.shape[1] != ftmix.shape[1] or mix.shape[1] != icstats.shape[0]:
        raise ValueError(
            f"inconsistent component counts in {inputs.label}: "
            f"mix {mix.shape}, FTmix {ftmix.shape}, ICstats {icstats.shape}"
        )
    if inputs.motion.shape[0] != mix.shape[0]:
        raise ValueError(
            f"{inputs.label}: {inputs.motion.shape[0]} motion rows "
            f"for {mix.shape[0]} volumes"
        )
    tr = load_tr(inputs.bold)
    # FTmix rows are the positive-frequency bins of a zero-padded FFT of length
    # 2*n_bins, DC dropped — the last bin sits exactly at Nyquist.
    frequencies = np.arange(1, ftmix.shape[0] + 1) / (2 * ftmix.shape[0] * tr)
    feature_names, features = load_features(inputs.features)
    if features.shape[0] != mix.shape[1]:
        raise ValueError(
            f"{inputs.features.name} has {features.shape[0]} rows "
            f"for {mix.shape[1]} components"
        )
    # filename order, so the (alphabetically) first model stays the primary one
    fix_results = [
        parse_fix_file(p) for p in sorted(inputs.classifications, key=lambda p: p.name)
    ]
    for result in fix_results:
        if len(result.verdicts) != mix.shape[1]:
            raise ValueError(
                f"{result.reviewer_name} has {len(result.verdicts)} verdicts "
                f"for {mix.shape[1]} components"
            )
    return MelodicSource(
        root=inputs.root,
        label=inputs.label,
        tr=tr,
        mix=mix,
        ftmix=ftmix,
        icstats=icstats,
        fd=fd_power(inputs.motion),
        frequencies=frequencies,
        feature_names=feature_names,
        features=features,
        fix_results=fix_results,
        ic_path=inputs.ic,
        mean_path=inputs.mean,
        mask_path=inputs.mask,
    )
