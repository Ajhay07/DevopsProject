"""
Unit tests for the WorkloadRebalancer.
Tests candidate pod selection, safety exclusions, recommendation mode,
and automatic eviction flow with safeguards.
"""
from unittest.mock import MagicMock
import pytest

from decision.decision_engine import (
    ClusterDecision,
    RebalanceAction,
)
from monitoring.metrics import PodMetrics
from rebalancer.rebalance import WorkloadRebalancer


@pytest.fixture
def rebalancer_config():
    return {
        "safeguards": {
            "exclude_namespaces": [
                "kube-system",
                "kube-public",
                "kube-node-lease",
                "kubernetes-dashboard",
            ],
            "exclude_pod_patterns": ["metrics-server", "coredns"],
            "prevent_immediate_rescheduling": True,
        }
    }


def test_find_eligible_pods_filters(rebalancer_config):
    mock_cm = MagicMock()
    rebalancer = WorkloadRebalancer(mock_cm, rebalancer_config)

    pods = [
        # 1. System pod - should be EXCLUDED
        PodMetrics(
            name="coredns-12345",
            namespace="kube-system",
            node_name="worker-1",
            status="Running",
            controller_kind="ReplicaSet",
            controller_name="coredns",
            cpu_usage_m=50.0,
        ),
        # 2. DaemonSet pod - should be EXCLUDED
        PodMetrics(
            name="kube-proxy-abcde",
            namespace="default",
            node_name="worker-1",
            status="Running",
            controller_kind="DaemonSet",
            controller_name="kube-proxy",
            cpu_usage_m=80.0,
        ),
        # 3. Unmanaged standalone pod - should be EXCLUDED
        PodMetrics(
            name="standalone-test",
            namespace="default",
            node_name="worker-1",
            status="Running",
            controller_kind=None,
            controller_name=None,
            cpu_usage_m=200.0,
        ),
        # 4. Eligible managed deployment pod (low CPU)
        PodMetrics(
            name="app-pod-1",
            namespace="default",
            node_name="worker-1",
            status="Running",
            controller_kind="ReplicaSet",
            controller_name="app-deploy-rs",
            cpu_usage_m=100.0,
        ),
        # 5. Eligible managed deployment pod (high CPU)
        PodMetrics(
            name="cpu-stress-xyz",
            namespace="default",
            node_name="worker-1",
            status="Running",
            controller_kind="ReplicaSet",
            controller_name="cpu-stress-rs",
            cpu_usage_m=450.0,
        ),
        # 6. Pod on another node - should be EXCLUDED
        PodMetrics(
            name="cpu-stress-other",
            namespace="default",
            node_name="worker-2",
            status="Running",
            controller_kind="ReplicaSet",
            controller_name="cpu-stress-rs",
            cpu_usage_m=450.0,
        ),
    ]

    eligible = rebalancer.find_eligible_pods(pods, source_node="worker-1")
    assert len(eligible) == 2
    # Ensure ranked by CPU usage descending: cpu-stress-xyz (450m) before app-pod-1 (100m)
    assert eligible[0].name == "cpu-stress-xyz"
    assert eligible[1].name == "app-pod-1"


def test_recommendation_mode(rebalancer_config):
    mock_cm = MagicMock()
    rebalancer = WorkloadRebalancer(mock_cm, rebalancer_config)

    decision = ClusterDecision(
        is_imbalanced=True,
        action=RebalanceAction.REBALANCE_WORKLOAD,
        source_node="worker-1",
        target_node="worker-2",
    )

    pods = [
        PodMetrics(
            name="stress-pod-1",
            namespace="default",
            node_name="worker-1",
            status="Running",
            controller_kind="ReplicaSet",
            controller_name="stress-rs",
            cpu_usage_m=300.0,
        )
    ]

    result = rebalancer.rebalance(decision, pods, mode="recommend")
    assert result.success is True
    assert result.executed is False
    assert result.evicted_pod == "stress-pod-1"
    assert "[RECOMMENDATION]" in result.message
    # Verify no cluster mutations occurred
    mock_cm.evict_pod.assert_not_called()
    mock_cm.cordon_node.assert_not_called()


def test_automatic_mode_eviction_and_safeguards(rebalancer_config):
    mock_cm = MagicMock()
    mock_cm.cordon_node.return_value = True
    mock_cm.uncordon_node.return_value = True
    mock_cm.evict_pod.return_value = True

    # Mock get_pods to simulate replacement pod creation
    mock_cm.get_pods.side_effect = [
        # initial call for pre_pods
        [{"name": "stress-pod-1", "controller_name": "stress-rs"}],
        # polling call showing newly scheduled replacement pod
        [
            {"name": "stress-pod-1", "controller_name": "stress-rs"},
            {"name": "stress-pod-replacement", "controller_name": "stress-rs", "node_name": "worker-2"},
        ],
    ]

    rebalancer = WorkloadRebalancer(mock_cm, rebalancer_config)

    decision = ClusterDecision(
        is_imbalanced=True,
        action=RebalanceAction.REBALANCE_WORKLOAD,
        source_node="worker-1",
        target_node="worker-2",
    )

    pods = [
        PodMetrics(
            name="stress-pod-1",
            namespace="default",
            node_name="worker-1",
            status="Running",
            controller_kind="ReplicaSet",
            controller_name="stress-rs",
            cpu_usage_m=300.0,
        )
    ]

    result = rebalancer.rebalance(decision, pods, mode="automatic")

    assert result.success is True
    assert result.executed is True
    assert result.evicted_pod == "stress-pod-1"
    assert result.replacement_pod == "stress-pod-replacement"
    assert result.new_node == "worker-2"

    # Verify safeguard lifecycle
    mock_cm.cordon_node.assert_called_once_with("worker-1")
    mock_cm.evict_pod.assert_called_once_with(name="stress-pod-1", namespace="default")
    mock_cm.uncordon_node.assert_called_once_with("worker-1")
