"""Read-side helpers that need no database: label geometry and switcher buttons."""

from melrater.core import selectors
from melrater.core.models import Run


def test_slice_labels_place_the_sixth_cell_on_the_second_row() -> None:
    # Arrange: seven picks over a five-column lightbox is two rows
    run = Run(montage_picks={"axial": [1, 2, 3, 4, 5, 6, 7]})

    # Act
    cell = selectors.slice_labels(run)["axial"].cells[5]

    # Assert: the cells tile with no gap, so the label sits at exact fractions
    assert (cell.index, cell.left, cell.top) == (6, "0%", "50%")


def test_slice_labels_mark_the_sagittal_edges_posterior_anterior() -> None:
    # Arrange
    run = Run(montage_picks={"sagittal": [4]})

    # Act
    labels = selectors.slice_labels(run)["sagittal"]

    # Assert
    assert (labels.edge_left, labels.edge_right) == ("P", "A")


def test_slice_labels_skip_an_axis_without_picks() -> None:
    # Arrange: a run rendered before the picks were recorded
    run = Run(montage_picks={"axial": [1]})

    # Act / Assert: no labels rather than wrong ones
    assert "coronal" not in selectors.slice_labels(run)


def test_smoothing_buttons_offer_only_what_the_run_has() -> None:
    # Arrange
    run = Run(montage_smoothings=["raw"])

    # Act / Assert
    assert selectors.smoothing_buttons(run) == [{"key": "raw", "label": "unsmoothed"}]
