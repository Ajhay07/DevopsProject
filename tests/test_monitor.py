"""
Unit tests for ClusterMonitor data aggregation and history tracking.
"""
import os
import tempfile
from unittest.mock import MagicMock
import pytest

from monitoring.monitor import ClusterMonitor


@pytest.fixture
def monitor_setup():
    temp_dir = tempfile.mkdtemp()
    csv_file = os.path.join(temp_dir, "test_metrics.csv")
    config = {
        "logging": {"metrics_csv": csv_file},
        "safeguards": {"exclude_namespaces": ["kube-system"]},
    }
    mock_cm = MagicMock()
    return mock_cm, config, csv_file


def test_collect_metrics_aggregation(monitor_setup):
    mock_cm, config, csv_file = monitor_setup

    mock_cm.get_nodes.return_value = [
        {
            "name": "node-1",
            "status": "Ready",
            "allocatable_cpu": "2000m",
            "allocatable_memory": "4096Mi",
            "roles": ["control-plane"],
        },
        {
            "name": "node-2",
            "status": "Ready",
            "allocatable_cpu": "2000m",
            "allocatable_memory": "4096Mi",
            "roles": ["worker"],
        },
    ]

    mock_cm.get_node_metrics.return_value = {
        "node-1": {"cpu": "1000m", "memory": "2048Mi"},  # 50% CPU
        "node-2": {"cpu": "1600m", "memory": "3072Mi"},  # 80% CPU
    }

    mock_cm.get_pods.return_value = [
        {
            "name": "pod-a",
            "namespace": "default",
            "node_name": "node-1",
            "phase": "Running",
            "controller_kind": "ReplicaSet",
            "controller_name": "app-rs",
            "labels": {"app": "web"},
        },
        {
            "name": "pod-b",
            "namespace": "default",
            "node_name": "node-2",
            "phase": "Running",
            "controller_kind": "ReplicaSet",
            "controller_name": "stress-rs",
            "labels": {"app": "stress"},
        },
    ]

    mock_cm.get_pod_metrics.return_value = [
        {
            "name": "pod-a",
            "namespace": "default",
            "containers": [{"usage": {"cpu": "200m", "memory": "256Mi"}}],
        },
        {
            "name": "pod-b",
            "namespace": "default",
            "containers": [{"usage": {"cpu": "700m", "memory": "512Mi"}}],
        },
    ]

    monitor = ClusterMonitor(mock_cm, config)
    summary = monitor.collect_metrics()

    assert len(summary.nodes) == 2
    assert summary.nodes["node-1"].cpu_percent == 50.0
    assert summary.nodes["node-2"].cpu_percent == 80.0
    assert summary.cpu_spread == 30.0
    assert summary.max_cpu_node == "node-2"
    assert summary.min_cpu_node == "node-1"

    assert len(summary.pods) == 2
    assert len(monitor.history) == 1

    # Verify CSV file was created and written
    assert os.path.exists(csv_file)
    with open(csv_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        assert len(lines) == 3  # Header + 2 nodes
