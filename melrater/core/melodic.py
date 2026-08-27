"""Readers for a MELODIC+pyFIX derivatives directory.

Pure functions over the on-disk layout produced by FSL MELODIC and pyFIX,
e.g. sub-XX_..._desc-preproc_bold/ containing filtered_func_data.ica/,
mc/prefiltered_func_data_mcf.par, fix/features.csv and fix4melview_*.txt.
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
class MelodicSource:
    """Everything melrater needs from one derivatives directory."""

    root: Path
    label: str
    tr: float
    mix: np.ndarray  # (n_timepoints, n_components) IC timecourses
    ftmix: np.ndarray  # (n_bins, n_components) power spectra
    icstats: np.ndarray  # (n_components, >=2): explained %, total %
    fd: np.ndarray  # (n_timepoints,) framewise displacement (mm)
    frequencies: np.ndarray  # (n_bins,) Hz
    feature_names: list[str]
    features: np.ndarray  # (n_components, n_features)
    fix_results: list[FixResult]

    @property
    def n_components(self) -> int:
        return int(self.mix.shape[1])

    @property
    def n_timepoints(self) -> int:
        return int(self.mix.shape[0])


def load_tr(root: Path) -> float:
    header = nib.load(root / "filtered_func_data.nii.gz").header
    assert isinstance(header, nib.nifti1.Nifti1Header)
    return float(header["pixdim"][4])


def load_fd(root: Path) -> np.ndarray:
    """Power's framewise displacement from an FSL mcflirt .par file.

    mcflirt columns are 3 rotations (radians) then 3 translations (mm);
    FD = sum|dtrans| + 50mm * sum|drot|, with 0 prepended for the first frame.
    """
    par = np.loadtxt(root / "mc" / "prefiltered_func_data_mcf.par")
    rot, trans = par[:, :3], par[:, 3:]
    fd = np.abs(np.diff(trans, axis=0)).sum(axis=1) + 50.0 * np.abs(
        np.diff(rot, axis=0)
    ).sum(axis=1)
    return np.concatenate([[0.0], fd])


def load_features(root: Path) -> tuple[list[str], np.ndarray]:
    with open(root / "fix" / "features.csv") as f:
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
        _, label, _, prob = (part.strip() for part in line.split(","))
        verdicts.append(FixVerdict(label=label, p_signal=float(prob)))
    return FixResult(
        model=match["model"], threshold=int(match["thr"]), verdicts=verdicts
    )


def load_fix_results(root: Path) -> list[FixResult]:
    return [parse_fix_file(p) for p in sorted(root.glob("fix4melview_*_thr*.txt"))]


def load_source(root: Path) -> MelodicSource:
    root = root.resolve()
    ica = root / "filtered_func_data.ica"
    tr = load_tr(root)
    mix = np.loadtxt(ica / "melodic_mix")
    ftmix = np.loadtxt(ica / "melodic_FTmix")
    icstats = np.loadtxt(ica / "melodic_ICstats")
    if mix.shape[1] != ftmix.shape[1] or mix.shape[1] != icstats.shape[0]:
        raise ValueError(
            f"inconsistent component counts in {ica}: "
            f"mix {mix.shape}, FTmix {ftmix.shape}, ICstats {icstats.shape}"
        )
    # FTmix rows are the positive-frequency bins of a zero-padded FFT of length
    # 2*n_bins, DC dropped — the last bin sits exactly at Nyquist.
    frequencies = np.arange(1, ftmix.shape[0] + 1) / (2 * ftmix.shape[0] * tr)
    feature_names, features = load_features(root)
    if features.shape[0] != mix.shape[1]:
        raise ValueError(
            f"features.csv has {features.shape[0]} rows for {mix.shape[1]} components"
        )
    fix_results = load_fix_results(root)
    for result in fix_results:
        if len(result.verdicts) != mix.shape[1]:
            raise ValueError(
                f"{result.reviewer_name} has {len(result.verdicts)} verdicts "
                f"for {mix.shape[1]} components"
            )
    return MelodicSource(
        root=root,
        label=root.name,
        tr=tr,
        mix=mix,
        ftmix=ftmix,
        icstats=icstats,
        fd=load_fd(root),
        frequencies=frequencies,
        feature_names=feature_names,
        features=features,
        fix_results=fix_results,
    )
