import numpy as np
import pytest

from melrater.core import metrics


@pytest.fixture
def table() -> metrics.MetricTable:
    names = ["featA", "featB", "const", "featC"]
    raw = np.array([[1.0, 5.0, 7.0, 0.1], [2.0, 6.0, 7.0, 0.2], [30.0, 7.0, 7.0, 0.3]])
    return metrics.build_metric_table(names, raw)


def test_constant_feature_dropped(table: metrics.MetricTable) -> None:
    assert table.dropped == ["const"]
    assert table.names == ["featA", "featB", "featC"]
    assert table.z.shape == (3, 3)


def test_robust_z_value(table: metrics.MetricTable) -> None:
    # featA = [1, 2, 30]: median 2, MAD 1 -> z(30) = 28 / 1.4826
    z_outlier = table.z[2, table.names.index("featA")]

    assert z_outlier == pytest.approx(28 / 1.4826, rel=1e-6)


def test_stats_payload_bands_and_signal_ticks(table: metrics.MetricTable) -> None:
    # Act
    payload = metrics.metric_stats_payload(table, signal_rows=[0])

    # Assert
    stats = payload["stats"]["featA"]
    assert stats["p5"] <= stats["p25"] <= stats["p75"] <= stats["p95"]
    assert stats["signal_z"] == [pytest.approx(float(table.z[0, 0]))]
    assert payload["dropped"] == ["const"]


def test_component_payload_roundtrip(table: metrics.MetricTable) -> None:
    # Act
    payload = metrics.component_metrics_payload(table, row=2)

    # Assert
    assert payload["featA"]["raw"] == pytest.approx(30.0)
    assert payload["featA"]["z"] == pytest.approx(28 / 1.4826, rel=1e-6)


def test_family_of() -> None:
    assert metrics.family_of("edgemasks:11") == "edgemasks"
    assert metrics.family_of("skewness") == "skewness"
