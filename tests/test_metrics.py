import numpy as np
import pytest

from melrater.core import metrics
from melrater.core.schemas import MetricNorm, MetricStat, MetricValue


@pytest.fixture
def table() -> metrics.MetricTable:
    names = ["featA", "featB", "const", "featC"]
    raw = np.array([[1.0, 5.0, 7.0, 0.1], [2.0, 6.0, 7.0, 0.2], [30.0, 7.0, 7.0, 0.3]])
    return metrics.build_metric_table(names, raw)


def test_constant_feature_is_dropped(table: metrics.MetricTable) -> None:
    assert table.dropped == ["const"]


def test_kept_names_exclude_dropped(table: metrics.MetricTable) -> None:
    assert table.names == ["featA", "featB", "featC"]


def test_z_has_one_column_per_kept_feature(table: metrics.MetricTable) -> None:
    assert table.z.shape == (3, 3)


def test_robust_z_value(table: metrics.MetricTable) -> None:
    # featA = [1, 2, 30]: median 2, MAD 1 -> z(30) = 28 / 1.4826
    z_outlier = table.z[2, table.names.index("featA")]

    assert z_outlier == pytest.approx(28 / 1.4826, rel=1e-6)


def test_stats_bands_are_ordered(table: metrics.MetricTable) -> None:
    # Act
    stats = metrics.compute_metric_stats(table, signal_rows=[0])

    # Assert
    s = stats.stats["featA"]
    assert s.p5 <= s.p25 <= s.p75 <= s.p95


def test_stats_record_signal_ticks(table: metrics.MetricTable) -> None:
    # Act
    stats = metrics.compute_metric_stats(table, signal_rows=[0])

    # Assert
    assert stats.stats["featA"].signal_z == [pytest.approx(float(table.z[0, 0]))]


def test_stats_record_dropped(table: metrics.MetricTable) -> None:
    # Act
    stats = metrics.compute_metric_stats(table, signal_rows=[0])

    # Assert
    assert stats.dropped == ["const"]


def test_stats_record_the_centre_their_z_units_are_measured_from(
    table: metrics.MetricTable,
) -> None:
    # Act
    stats = metrics.compute_metric_stats(table, signal_rows=[0])

    # Assert: featA = [1, 2, 30], median 2 — the raw value z = 0 stands for
    assert stats.stats["featA"].median == pytest.approx(2.0)


def test_stats_record_the_scale_their_z_units_are_measured_in(
    table: metrics.MetricTable,
) -> None:
    # Act
    stats = metrics.compute_metric_stats(table, signal_rows=[0])

    # Assert: MAD of [1, 2, 30] is 1, so one z is 1.4826 raw units
    assert stats.stats["featA"].scale == pytest.approx(1.4826)


def test_component_metrics_keep_raw_value(table: metrics.MetricTable) -> None:
    # Act
    values = metrics.compute_component_metrics(table, row=2)

    # Assert
    assert values["featA"].raw == pytest.approx(30.0)


def test_component_metrics_keep_z(table: metrics.MetricTable) -> None:
    # Act
    values = metrics.compute_component_metrics(table, row=2)

    # Assert
    assert values["featA"].z == pytest.approx(28 / 1.4826, rel=1e-6)


def test_family_of_prefixed_name() -> None:
    assert metrics.family_of("edgemasks:11") == "edgemasks"


def test_family_of_bare_name() -> None:
    assert metrics.family_of("skewness") == "skewness"


# --- the cohort: one metric pooled over every scan ------------------------


def test_metric_norms_centre_on_the_pooled_median() -> None:
    # Act
    norms = metrics.build_metric_norms({"featA": np.array([1.0, 2.0, 3.0, 4.0])})

    # Assert
    assert norms["featA"].median == pytest.approx(2.5)


def test_metric_norms_count_the_values_that_contributed() -> None:
    # Act
    norms = metrics.build_metric_norms({"featA": np.array([1.0, 2.0, 3.0, 4.0])})

    # Assert
    assert norms["featA"].n == 4


def test_metric_norms_bands_are_ordered() -> None:
    # Act
    norms = metrics.build_metric_norms({"featA": np.arange(100, dtype=float)})

    # Assert
    n = norms["featA"]
    assert n.p5 <= n.p25 <= n.p75 <= n.p95


def test_metric_norms_leave_out_a_cohort_wide_constant() -> None:
    # Arrange: no spread means no scale to state a position in
    values = {"const": np.full(5, 7.0)}

    # Act
    norms = metrics.build_metric_norms(values)

    # Assert
    assert "const" not in norms


def test_metric_norms_leave_out_a_metric_with_no_values() -> None:
    # Act
    norms = metrics.build_metric_norms({"featA": np.array([])})

    # Assert
    assert "featA" not in norms


# --- picking a frame for one displayed row --------------------------------

#: featA as `table` scores it: raw 2.0 is the run's median, one z is 1.4826.
SCAN_STAT = MetricStat(
    median=2.0, scale=1.4826, p5=-1.0, p25=-0.5, p75=0.5, p95=1.0, signal_z=[1.0]
)
#: A cohort on which raw values are simply tenths.
COHORT_NORM = MetricNorm(
    median=0.0, scale=10.0, p5=-2.0, p25=-1.0, p75=1.0, p95=2.0, n=900
)


@pytest.fixture
def cohort_frame() -> metrics.MetricFrame:
    return metrics.metric_frame(
        value=MetricValue(raw=30.0, z=18.9), stat=SCAN_STAT, norm=COHORT_NORM
    )


def test_metric_frame_scores_the_component_against_the_cohort(
    cohort_frame: metrics.MetricFrame,
) -> None:
    # raw 30 on a cohort centred at 0 with a scale of 10
    assert cohort_frame.z == pytest.approx(3.0)


def test_metric_frame_keeps_the_within_scan_score(
    cohort_frame: metrics.MetricFrame,
) -> None:
    assert cohort_frame.scan_z == pytest.approx(18.9)


def test_metric_frame_takes_its_bands_from_the_cohort(
    cohort_frame: metrics.MetricFrame,
) -> None:
    assert cohort_frame.p95 == pytest.approx(COHORT_NORM.p95)


def test_metric_frame_rails_the_scan_over_the_cohort_bands(
    cohort_frame: metrics.MetricFrame,
) -> None:
    assert cohort_frame.scan_range is not None


@pytest.fixture
def cohort_rail(cohort_frame: metrics.MetricFrame) -> metrics.ScanRange:
    """The rail, which the cohort frame always carries (a test above pins that)."""
    assert cohort_frame.scan_range is not None
    return cohort_frame.scan_range


def test_metric_frame_places_the_scans_median_in_cohort_units(
    cohort_rail: metrics.ScanRange,
) -> None:
    # the run's median is raw 2.0, which the cohort scores at 0.2
    assert cohort_rail.median == pytest.approx(0.2)


def test_metric_frame_places_the_scans_range_in_cohort_units(
    cohort_rail: metrics.ScanRange,
) -> None:
    # the run's p75 is z 0.5 there, so raw 2 + 0.5*1.4826 = 2.7413
    assert cohort_rail.p75 == pytest.approx(0.27413)


def test_metric_frame_places_the_signal_ticks_in_cohort_units(
    cohort_frame: metrics.MetricFrame,
) -> None:
    # the tick at z 1.0 there is raw 3.4826, which the cohort scores at 0.34826
    assert cohort_frame.signal_z == [pytest.approx(0.34826)]


@pytest.fixture
def scan_frame() -> metrics.MetricFrame:
    """No cohort knows this metric, so the run has to place it itself."""
    return metrics.metric_frame(
        value=MetricValue(raw=30.0, z=18.9), stat=SCAN_STAT, norm=None
    )


def test_metric_frame_falls_back_to_the_scans_own_score(
    scan_frame: metrics.MetricFrame,
) -> None:
    assert scan_frame.z == pytest.approx(18.9)


def test_metric_frame_falls_back_to_the_scans_own_bands(
    scan_frame: metrics.MetricFrame,
) -> None:
    assert scan_frame.p95 == pytest.approx(SCAN_STAT.p95)


def test_metric_frame_draws_no_rail_when_the_bands_are_the_scan(
    scan_frame: metrics.MetricFrame,
) -> None:
    # a rail would be a copy of the bands it sits above
    assert scan_frame.scan_range is None
