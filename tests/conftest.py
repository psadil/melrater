"""Shared fixtures: a tiny synthetic MELODIC+pyFIX derivatives directory."""

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

N_COMPONENTS = 3
N_TIMEPOINTS = 20
N_BINS = 10
SHAPE = (6, 6, 4)
TR = 2.0
FD_STEP = 0.01  # constant translation ramp -> FD == FD_STEP everywhere but t=0


@pytest.fixture
def melodic_dir(tmp_path: Path) -> Path:
    rng = np.random.default_rng(7)
    root = tmp_path / "sub-01_task-test_desc-preproc_bold"
    ica = root / "filtered_func_data.ica"
    (root / "mc").mkdir(parents=True)
    (root / "fix").mkdir()
    ica.mkdir()

    affine = np.diag([2.0, 2.0, 2.0, 1.0])
    func = rng.normal(size=(*SHAPE, 2)).astype("float32")
    func_img = nib.nifti1.Nifti1Image(func, affine)
    func_img.header["pixdim"][4] = TR
    func_img.to_filename(root / "filtered_func_data.nii.gz")

    mean = (100 + rng.normal(size=SHAPE)).astype("float32")
    nib.nifti1.Nifti1Image(mean, affine).to_filename(ica / "mean.nii.gz")
    mask = np.ones(SHAPE, dtype="float32")
    nib.nifti1.Nifti1Image(mask, affine).to_filename(root / "mask.nii.gz")
    nib.nifti1.Nifti1Image(mask, affine).to_filename(ica / "mask.nii.gz")
    ic = (rng.normal(size=(*SHAPE, N_COMPONENTS)) * 4).astype("float32")
    nib.nifti1.Nifti1Image(ic, affine).to_filename(ica / "melodic_IC.nii.gz")

    np.savetxt(ica / "melodic_mix", rng.normal(size=(N_TIMEPOINTS, N_COMPONENTS)))
    np.savetxt(ica / "melodic_FTmix", np.abs(rng.normal(size=(N_BINS, N_COMPONENTS))))
    np.savetxt(ica / "melodic_ICstats", np.abs(rng.normal(size=(N_COMPONENTS, 4))))

    par = np.zeros((N_TIMEPOINTS, 6))
    par[:, 3] = np.arange(N_TIMEPOINTS) * FD_STEP  # x-translation ramp
    np.savetxt(root / "mc" / "prefiltered_func_data_mcf.par", par)

    header = ["featA", "featB", "const", "featC"]
    rows = [[1.0, 5.0, 7.0, 0.1], [2.0, 6.0, 7.0, 0.2], [30.0, 7.0, 7.0, 0.3]]
    with open(root / "fix" / "features.csv", "w") as f:
        f.write(",".join(header) + "\n")
        f.writelines(",".join(str(v) for v in row) + "\n" for row in rows)

    (root / "fix4melview_TestModel_thr5.txt").write_text(
        "filtered_func_data.ica\n"
        "1, Signal, False, 0.9\n"
        "2, Noise, True, 0.01\n"
        "3, Noise, True, 0.002\n"
        "[2, 3]\n"
    )
    return root


def run_inputs(root: Path):
    """The RunInputs a catalog would resolve for the synthetic directory.

    A plain factory rather than a fixture so tests can point individual
    entries elsewhere; motion comes from the .par exactly as the catalog's
    feat_motion table would hold it.
    """
    from melrater.core import melodic

    ica = root / "filtered_func_data.ica"
    return melodic.RunInputs(
        root=root,
        label=root.name,
        bold=root / "filtered_func_data.nii.gz",
        mix=ica / "melodic_mix",
        ftmix=ica / "melodic_FTmix",
        icstats=ica / "melodic_ICstats",
        features=root / "fix" / "features.csv",
        ic=ica / "melodic_IC.nii.gz",
        mean=ica / "mean.nii.gz",
        mask=root / "mask.nii.gz",
        classifications=(root / "fix4melview_TestModel_thr5.txt",),
        motion=np.loadtxt(root / "mc" / "prefiltered_func_data_mcf.par"),
    )


@pytest.fixture
def media_root(tmp_path: Path, settings) -> Path:
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def ingested_run(melodic_dir: Path, media_root: Path):
    from melrater.core import melodic, services

    return services.ingest_run(source=melodic.load_run(run_inputs(melodic_dir)))


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user("rater", password="pw")
