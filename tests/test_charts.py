import numpy as np
import pytest

from melrater.core import charts


@pytest.fixture
def tc_fd_svg() -> str:
    ts = list(np.sin(np.arange(20)))
    fd = [0.0] + [0.1] * 19
    return charts.timecourse_fd_svg(ts, fd, tr=2.0)


def test_timecourse_fd_draws_two_traces(tc_fd_svg: str) -> None:
    assert tc_fd_svg.count("<polyline") == 2


def test_timecourse_fd_labels_both_panels(tc_fd_svg: str) -> None:
    assert "IC timecourse" in tc_fd_svg and "FD (mm)" in tc_fd_svg


def test_timecourse_fd_has_no_nan_coordinates(tc_fd_svg: str) -> None:
    assert "nan" not in tc_fd_svg.lower()


def test_spectrum_draws_one_trace() -> None:
    # Act
    svg = charts.spectrum_svg([1.0, 2.0, 0.5], [0.1, 0.2, 0.3])

    # Assert
    assert svg.count("<polyline") == 1


def test_spectrum_labels_frequency_axis() -> None:
    # Act
    svg = charts.spectrum_svg([1.0, 2.0, 0.5], [0.1, 0.2, 0.3])

    # Assert
    assert "frequency (Hz)" in svg


def test_metric_glyph_colors_outlier_dot_red() -> None:
    # Act
    svg = charts.metric_glyph_svg(
        z=5.0, p5=-1.0, p25=-0.5, p75=0.5, p95=1.0, signal_z=[0.2]
    )

    # Assert
    assert f'fill="{charts.C_SEV_BAD}"' in svg


def test_metric_glyph_draws_signal_ticks() -> None:
    # Act
    svg = charts.metric_glyph_svg(
        z=5.0, p5=-1.0, p25=-0.5, p75=0.5, p95=1.0, signal_z=[0.2]
    )

    # Assert
    assert f'stroke="{charts.C_SIGNAL}"' in svg


@pytest.fixture
def prob_entries() -> list[charts.ProbEntry]:
    # one rated (disagreeing with FIX) and one unrated component
    return [
        charts.ProbEntry(index=1, p_signal=0.9, fix_label="Signal", user_label="Noise"),
        charts.ProbEntry(index=2, p_signal=0.001, fix_label="Noise", user_label=None),
    ]


def test_prob_strip_colors_rated_tick_by_human_label(
    prob_entries: list[charts.ProbEntry],
) -> None:
    # Act
    svg = charts.prob_strip_svg(prob_entries, current_ic=1, threshold=0.05)

    # Assert: the rated tick is tall (1.6 width) and uses the human label color
    assert f'stroke="{charts.C_NOISE}" stroke-width="1.6"' in svg


def test_prob_strip_draws_threshold(prob_entries: list[charts.ProbEntry]) -> None:
    # Act
    svg = charts.prob_strip_svg(prob_entries, current_ic=1, threshold=0.05)

    # Assert
    assert "thr 0.05" in svg


def test_prob_strip_marks_current_component(
    prob_entries: list[charts.ProbEntry],
) -> None:
    # Act
    svg = charts.prob_strip_svg(prob_entries, current_ic=1, threshold=0.05)

    # Assert
    assert f'fill="{charts.C_ACCENT}"' in svg


def test_prob_strip_omits_dot_for_unknown_component(
    prob_entries: list[charts.ProbEntry],
) -> None:
    # Act: current component has no FIX entry (e.g. row deleted in admin)
    svg = charts.prob_strip_svg(prob_entries, current_ic=99, threshold=0.05)

    # Assert
    assert f'fill="{charts.C_ACCENT}"' not in svg
