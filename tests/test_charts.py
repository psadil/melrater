import numpy as np

from melrater.core import charts


def test_timecourse_fd_svg_structure() -> None:
    # Arrange
    ts = list(np.sin(np.arange(20)))
    fd = [0.0] + [0.1] * 19

    # Act
    svg = charts.timecourse_fd_svg(ts, fd, tr=2.0)

    # Assert: two polylines (timecourse + FD), labels, no NaN
    assert svg.count("<polyline") == 2
    assert "IC timecourse" in svg
    assert "FD (mm)" in svg
    assert "time (s)" in svg
    assert "nan" not in svg.lower()


def test_spectrum_svg_structure() -> None:
    svg = charts.spectrum_svg([1.0, 2.0, 0.5], [0.1, 0.2, 0.3])

    assert svg.count("<polyline") == 1
    assert "frequency (Hz)" in svg


def test_metric_glyph_colors_outlier_dot_red() -> None:
    svg = charts.metric_glyph_svg(
        z=5.0, p5=-1.0, p25=-0.5, p75=0.5, p95=1.0, signal_z=[0.2]
    )

    assert f'fill="{charts.C_SEV_BAD}"' in svg
    assert f'stroke="{charts.C_SIGNAL}"' in svg  # the signal tick


def test_prob_strip_marks_ratings_and_threshold() -> None:
    # Arrange: one rated (disagreeing) and one unrated component
    entries = [
        charts.ProbEntry(p_signal=0.9, fix_label="Signal", user_label="Noise"),
        charts.ProbEntry(p_signal=0.001, fix_label="Noise", user_label=None),
    ]

    # Act
    svg = charts.prob_strip_svg(entries, current_index=0, threshold=0.05)

    # Assert: the rated tick uses the human label's color, threshold is drawn
    assert f'stroke="{charts.C_NOISE}" stroke-width="1.6"' in svg
    assert "thr 0.05" in svg
