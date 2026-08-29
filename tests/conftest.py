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
    entries elsewhere; the motion and ICstats arrays come from the files
    exactly as the catalog's feat_motion/feat_icstats tables would hold them
    (feat_icstats stores only the required explained/total variance pair).
    """
    from melrater.core import melodic

    ica = root / "filtered_func_data.ica"
    return melodic.RunInputs(
        root=root,
        label=root.name,
        bold=root / "filtered_func_data.nii.gz",
        mix=ica / "melodic_mix",
        ftmix=ica / "melodic_FTmix",
        features=root / "fix" / "features.csv",
        ic=ica / "melodic_IC.nii.gz",
        mean=ica / "mean.nii.gz",
        mask=root / "mask.nii.gz",
        classifications=(root / "fix4melview_TestModel_thr5.txt",),
        motion=np.loadtxt(root / "mc" / "prefiltered_func_data_mcf.par"),
        icstats=np.loadtxt(ica / "melodic_ICstats")[:, :2],
    )


@pytest.fixture
def media_root(tmp_path: Path, settings) -> Path:
    """Point both the montage store and the media view at a temp directory.

    STORAGES has to be overridden, not just MEDIA_ROOT: Django resets its
    storage handler when STORAGES changes and *not* when MEDIA_ROOT does, so
    overriding only the latter leaves a cached FileSystemStorage happily
    writing montages into the repository.
    """
    root = tmp_path / "media"
    settings.MEDIA_ROOT = root
    settings.STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(root), "base_url": settings.MEDIA_URL},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
    return root


def montage_dir(media_root: Path, run) -> Path:
    """Where one run's montages land: keyed by uuid, then by content digest."""
    return media_root / "runs" / str(run.uuid) / str(run.montage_digest)


@pytest.fixture
def ingested_run(melodic_dir: Path, media_root: Path):
    from melrater.core import melodic, services

    return services.ingest_run(source=melodic.load_run(run_inputs(melodic_dir)))


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user("rater", password="pw")


@pytest.fixture
def bare_runs(db):
    """Make Run+Component rows directly, with no montage rendering.

    Enough for query-count and run-list tests, and orders of magnitude cheaper
    than ingesting: `ingested_run` renders nine images.
    """
    from melrater.core.models import Component, Run

    made: list = []

    def make(n: int, components: int = 3):
        from_index = len(made)
        for i in range(from_index, from_index + n):
            run = Run.objects.create(
                path=f"/data/run{i}",
                label=f"run{i:03d}",
                sub=f"{i:03d}",
                task="rest",
                tr=2.0,
                n_timepoints=4,
                fd=[],
                frequencies=[],
                metric_stats={"names": [], "dropped": [], "stats": {}},
                montage_digest=f"{i:016x}",
            )
            Component.objects.bulk_create(
                Component(
                    run=run,
                    index=j + 1,
                    explained_var=1.0,
                    total_var=1.0,
                    timecourse=[],
                    spectrum=[],
                    metrics={},
                )
                for j in range(components)
            )
            made.append(run)
        return made

    return make


@pytest.fixture
def ingest_user(django_user_model):
    """An account in the `ingest` group, as `create_rater --ingest` makes one."""
    from django.contrib.auth.models import Group

    from melrater.core.api import INGEST_GROUP

    user = django_user_model.objects.create_user("pusher", password="pw")
    user.groups.add(Group.objects.get_or_create(name=INGEST_GROUP)[0])
    return user


@pytest.fixture
def ingest_auth(ingest_user) -> dict[str, str]:
    """Headers that authenticate as `ingest_user` over HTTP Basic."""
    import base64

    token = base64.b64encode(b"pusher:pw").decode()
    return {"HTTP_AUTHORIZATION": f"Basic {token}"}


@pytest.fixture
def push_bundle(ingested_run, media_root):
    """One run's two file parts, exactly as `push_runs` would send them."""
    from melrater.core import selectors

    return selectors.run_bundle(ingested_run)
