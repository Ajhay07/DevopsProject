"""
Unit tests for CPU and memory quantity parsing and data structures.
"""
import pytest
from monitoring.metrics import (
    parse_cpu,
    parse_memory,
    NodeMetrics,
    PodMetrics,
    ClusterMetricsSummary,
)


def test_parse_cpu_formats():
    # Cores
    assert parse_cpu("2") == 2000.0
    assert parse_cpu(1) == 1000.0
    assert parse_cpu(0.5) == 500.0

    # Millicores
    assert parse_cpu("250m") == 250.0
    assert parse_cpu("1000m") == 1000.0

    # Nanocores (from Metrics Server)
    assert parse_cpu("150000000n") == 150.0
    assert parse_cpu("500000000n") == 500.0

    # Microcores
    assert parse_cpu("50000u") == 50.0

    # None and empty
    assert parse_cpu(None) == 0.0
    assert parse_cpu("") == 0.0


def test_parse_cpu_invalid():
    with pytest.raises(ValueError):
        parse_cpu("invalid_cpu")


def test_parse_memory_formats():
    # Binary units
    assert parse_memory("1024Ki") == 1.0  # 1 MiB
    assert parse_memory("256Mi") == 256.0
    assert parse_memory("2Gi") == 2048.0
    assert parse_memory("1Ti") == 1024.0 * 1024.0

    # Decimal units
    assert round(parse_memory("1000k"), 4) == round((1000 * 1000) / (1024 * 1024), 4)

    # Raw bytes
    assert parse_memory(1048576) == 1.0  # 1 MiB
    assert parse_memory("1048576") == 1.0

    # None and empty
    assert parse_memory(None) == 0.0
    assert parse_memory("") == 0.0


def test_node_metrics_calculations():
    node = NodeMetrics(
        name="worker-1",
        status="Ready",
        cpu_usage_m=1600.0,
        cpu_allocatable_m=2000.0,
        memory_usage_mib=3072.0,
        memory_allocatable_mib=4096.0,
    )
    node.calculate_percentages()

    assert node.cpu_percent == 80.0
    assert node.memory_percent == 75.0


def test_cluster_metrics_summary():
    n1 = NodeMetrics("node-1", "Ready", 800.0, 1000.0, 400.0, 1000.0)
    n1.calculate_percentages()  # 80% CPU
    n2 = NodeMetrics("node-2", "Ready", 200.0, 1000.0, 200.0, 1000.0)
    n2.calculate_percentages()  # 20% CPU

    summary = ClusterMetricsSummary(nodes={"node-1": n1, "node-2": n2})
    summary.calculate_summary()

    assert summary.avg_cpu_percent == 50.0
    assert summary.max_cpu_node == "node-1"
    assert summary.min_cpu_node == "node-2"
    assert summary.cpu_spread == 60.0
