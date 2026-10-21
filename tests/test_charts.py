import numpy as np
import pytest

from melrater.core import charts
from melrater.core.metrics import MetricFrame, ScanRange

#: Long enough that the Friston-24 chance level is a fraction rather than
#: saturated, so the meter's tick lands somewhere meaningful: 24/199 = 0.12.
N_VOLUMES = 200


@pytest.fixture
def tc_fd_svg() -> str:
    ts = list(np.sin(np.arange(N_VOLUMES)))
    fd = [0.0] + [0.1] * (N_VOLUMES - 1)
    return charts.timecourse_fd_svg(ts, fd, tr=2.0)


def test_timecourse_fd_draws_two_traces(tc_fd_svg: str) -> None:
    assert tc_fd_svg.count("<polyline") == 2


def test_timecourse_fd_labels_both_panels(tc_fd_svg: str) -> None:
    assert "IC timecourse" in tc_fd_svg and "FD (mm)" in tc_fd_svg


def test_timecourse_fd_has_no_nan_coordinates(tc_fd_svg: str) -> None:
    assert "nan" not in tc_fd_svg.lower()


def test_timecourse_fd_says_nothing_about_motion_without_an_r2(
    tc_fd_svg: str,
) -> None:
    # a run ingested before the fit existed has no value to draw
    assert "motion R" not in tc_fd_svg


@pytest.fixture
def tc_fd_r2_svg() -> str:
    ts = list(np.sin(np.arange(N_VOLUMES)))
    fd = [0.0] + [0.1] * (N_VOLUMES - 1)
    return charts.timecourse_fd_svg(ts, fd, tr=2.0, motion_r2=0.42)


def test_timecourse_fd_reports_the_motion_r2(tc_fd_r2_svg: str) -> None:
    assert "motion R&#178; 0.42" in tc_fd_r2_svg


def test_timecourse_fd_names_the_chance_level_the_r2_is_read_against(
    tc_fd_r2_svg: str,
) -> None:
    # 24 regressors over 200 volumes explain 24/199 of anything at all
    assert "explain 0.12 by chance" in tc_fd_r2_svg


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


@pytest.fixture
def scan_frame() -> MetricFrame:
    """The fallback frame: the bands are this run's own components."""
    return MetricFrame(
        cohort=False,
        z=5.0,
        scan_z=5.0,
        p5=-1.0,
        p25=-0.5,
        p75=0.5,
        p95=1.0,
        signal_z=[0.2],
        scan_range=None,
    )


@pytest.fixture
def cohort_frame() -> MetricFrame:
    """The cohort frame: wider bands, and this run's own range as a rail."""
    return MetricFrame(
        cohort=True,
        z=5.0,
        scan_z=1.2,
        p5=-2.0,
        p25=-0.7,
        p75=0.7,
        p95=2.0,
        signal_z=[0.2],
        scan_range=ScanRange(p25=0.4, p75=1.6, median=1.0),
    )


def test_metric_glyph_colors_outlier_dot_red(scan_frame: MetricFrame) -> None:
    # Act
    svg = charts.metric_glyph_svg(scan_frame)

    # Assert
    assert f'fill="{charts.C_SEV_BAD}"' in svg


def test_metric_glyph_draws_signal_ticks(scan_frame: MetricFrame) -> None:
    # Act
    svg = charts.metric_glyph_svg(scan_frame)

    # Assert
    assert f'stroke="{charts.C_SIGNAL}"' in svg


def test_metric_glyph_draws_no_scan_rail_in_the_scan_frame(
    scan_frame: MetricFrame,
) -> None:
    # Arrange/Act: the bands already are this run, so a rail would copy them
    svg = charts.metric_glyph_svg(scan_frame)

    # Assert
    assert f'stroke="{charts.C_MUTED}"' not in svg


def test_metric_glyph_rails_the_scan_range_over_the_cohort_bands(
    cohort_frame: MetricFrame,
) -> None:
    # Act
    svg = charts.metric_glyph_svg(cohort_frame)

    # Assert: the rail spans the run's own middle half, notched at its median
    assert f'stroke="{charts.C_MUTED}" stroke-width="1.6"' in svg


@pytest.fixture
def prob_entries() -> list[charts.ProbEntry]:
    # two rated components, the first disagreeing with FIX (above thr, Noise)
    return [
        charts.ProbEntry(index=1, p_signal=0.9, user_label="Noise"),
        charts.ProbEntry(index=2, p_signal=0.001, user_label="Noise"),
    ]


def test_prob_strip_colors_tick_by_human_label(
    prob_entries: list[charts.ProbEntry],
) -> None:
    # Act
    svg = charts.prob_strip_svg(prob_entries, current_ic=1, threshold=0.05)

    # Assert: the tick uses the human label color
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


def test_metric_glyph_emits_only_its_own_markup(cohort_frame: MetricFrame) -> None:
    # Arrange: the glyph is rendered with |safe, so its inputs must never be
    # able to introduce a tag. They are all floats — this pins that.
    svg = charts.metric_glyph_svg(cohort_frame)

    # Assert
    assert svg.count("<") == svg.count("<svg") + svg.count("<rect") + svg.count(
        "<line"
    ) + svg.count("<circle") + svg.count("</svg")


def test_prob_strip_ignores_an_unknown_label(tmp_path) -> None:
    # Arrange: labels are used only as palette keys, never interpolated into
    # the markup
    entries = [charts.ProbEntry(index=1, p_signal=0.5, user_label="<script>")]

    # Act
    svg = charts.prob_strip_svg(entries, current_ic=1, threshold=0.05)

    # Assert
    assert "<script>" not in svg
