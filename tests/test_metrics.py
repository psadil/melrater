import numpy as np
import pytest

from melrater.core import metrics


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
