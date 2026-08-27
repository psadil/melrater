"""lake._assemble: turning one catalog row's resolutions into RunInputs.

The queries themselves are bidslake's tested machinery; what melrater adds —
label/root derivation, the never-guess policy for gaps, motion-row problems —
is pure and exercised here on hand-made resolutions, no catalog involved.
"""

from pathlib import Path

import numpy as np

from melrater.core import lake

RUN_DIR = "sub-01_task-t_desc-preproc_bold"
ANCHOR_PATH = f"{RUN_DIR}/filtered_func_data.ica/melodic_mix"
ANCHOR_LOCAL = Path("/data") / RUN_DIR / "filtered_func_data.ica" / "melodic_mix"


def resolved_roles() -> dict[str, Path | None]:
    root = Path("/data") / RUN_DIR
    ica = root / "filtered_func_data.ica"
    return {
        "bold": root / "filtered_func_data.nii.gz",
        "ftmix": ica / "melodic_FTmix",
        "icstats": ica / "melodic_ICstats",
        "features": root / "fix" / "features.csv",
        "ic": ica / "melodic_IC.nii.gz",
        "mean": ica / "mean.nii.gz",
        "mask": root / "mask.nii.gz",
        "motion": root / "mc" / "prefiltered_func_data_mcf.par",
    }


def assemble(**overrides) -> lake.DiscoveredRun:
    kwargs: dict = {
        "anchor_path": ANCHOR_PATH,
        "anchor_local": ANCHOR_LOCAL,
        "roles": resolved_roles(),
        "unresolved": {},
        "classifications": (Path("/data") / RUN_DIR / "fix4melview_A_thr1.txt",),
        "motion": np.zeros((5, 6)),
    }
    kwargs.update(overrides)
    return lake._assemble(**kwargs)


def test_label_is_the_run_directory() -> None:
    assert assemble().label == RUN_DIR


def test_root_is_the_local_run_directory() -> None:
    assert assemble().root == Path("/data") / RUN_DIR


def test_complete_run_builds_inputs() -> None:
    assert assemble().inputs is not None


def test_the_anchor_is_the_mixing_matrix() -> None:
    run = assemble()

    assert run.inputs is not None and run.inputs.mix == ANCHOR_LOCAL


def test_classifications_are_carried_over() -> None:
    run = assemble()

    assert run.inputs is not None and run.inputs.classifications == (
        Path("/data") / RUN_DIR / "fix4melview_A_thr1.txt",
    )


def test_missing_role_yields_no_inputs() -> None:
    # Arrange
    roles = resolved_roles() | {"mean": None}

    # Act
    run = assemble(roles=roles, unresolved={"mean": 0})

    # Assert
    assert run.inputs is None


def test_missing_role_is_reported() -> None:
    roles = resolved_roles() | {"mean": None}

    run = assemble(roles=roles, unresolved={"mean": 0})

    assert run.problems == ("mean matched nothing",)


def test_ambiguous_role_is_reported_with_its_count() -> None:
    roles = resolved_roles() | {"mask": None}

    run = assemble(roles=roles, unresolved={"mask": 2})

    assert run.problems == ("mask was ambiguous (2 matches)",)


def test_absent_motion_rows_are_reported() -> None:
    run = assemble(motion=None)

    assert run.problems == ("feat_motion has no rows for the motion file",)


def test_unreadable_motion_row_is_reported() -> None:
    # Arrange: one NULL .par line, back from the catalog as a NaN row
    motion = np.zeros((5, 6))
    motion[2, :] = np.nan

    # Act
    run = assemble(motion=motion)

    # Assert
    assert run.problems == ("feat_motion has 1 unreadable row(s)",)


def test_unreadable_motion_row_yields_no_inputs() -> None:
    motion = np.zeros((5, 6))
    motion[2, :] = np.nan

    run = assemble(motion=motion)

    assert run.inputs is None
