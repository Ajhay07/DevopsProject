"""
Unit tests for the DecisionEngine.
Validates node classification, imbalance detection, consecutive-sample validation,
and cooldown mechanisms.
"""
import pytest
from decision.decision_engine import (
    DecisionEngine,
    NodeClassification,
    RebalanceAction,
)
from monitoring.metrics import ClusterMetricsSummary, NodeMetrics


@pytest.fixture
def config():
    return {
        "thresholds": {
            "cpu_overload_percent": 75.0,
            "cpu_underutilized_percent": 30.0,
            "cpu_imbalance_delta": 25.0,
            "memory_overload_percent": 80.0,
        },
        "engine": {
            "consecutive_samples": 2,
            "cooldown_seconds": 60,
        },
    }


def make_summary(n1_cpu: float, n2_cpu: float, n3_cpu: float = 25.0) -> ClusterMetricsSummary:
    nodes = {
        "minikube": NodeMetrics("minikube", "Ready", n3_cpu * 10, 1000.0),
        "minikube-m02": NodeMetrics("minikube-m02", "Ready", n1_cpu * 10, 1000.0),
        "minikube-m03": NodeMetrics("minikube-m03", "Ready", n2_cpu * 10, 1000.0),
    }
    for n in nodes.values():
        n.calculate_percentages()

    summary = ClusterMetricsSummary(nodes=nodes)
    summary.calculate_summary()
    return summary


def test_balanced_cluster(config):
    engine = DecisionEngine(config)
    summary = make_summary(n1_cpu=45.0, n2_cpu=40.0, n3_cpu=42.0)

    decision = engine.evaluate(summary)
    assert not decision.is_imbalanced
    assert decision.action == RebalanceAction.NONE
    for d in decision.node_decisions.values():
        assert d.classification == NodeClassification.NORMAL


def test_overloaded_and_underutilized_classification(config):
    engine = DecisionEngine(config)
    summary = make_summary(n1_cpu=85.0, n2_cpu=20.0, n3_cpu=40.0)

    # First evaluation: Validating consecutive samples (1/2)
    dec1 = engine.evaluate(summary)
    assert dec1.is_imbalanced
    assert dec1.source_node == "minikube-m02"
    assert dec1.target_node == "minikube-m03"
    assert dec1.node_decisions["minikube-m02"].classification == NodeClassification.OVERLOADED
    assert dec1.node_decisions["minikube-m03"].classification == NodeClassification.UNDERUTILIZED
    assert dec1.action == RebalanceAction.VALIDATING_SAMPLES

    # Second evaluation with sustained load: Confirms REBALANCE_WORKLOAD (2/2)
    dec2 = engine.evaluate(summary)
    assert dec2.action == RebalanceAction.REBALANCE_WORKLOAD
    assert dec2.consecutive_hits == 2


def test_cooldown_mechanism(config):
    engine = DecisionEngine(config)
    summary = make_summary(n1_cpu=88.0, n2_cpu=18.0)

    # Advance through validation
    engine.evaluate(summary)
    dec = engine.evaluate(summary)
    assert dec.action == RebalanceAction.REBALANCE_WORKLOAD

    # Trigger rebalance record
    engine.record_rebalance_executed()
    assert engine.is_in_cooldown()

    # Next evaluation during cooldown
    dec_cooldown = engine.evaluate(summary)
    assert dec_cooldown.action == RebalanceAction.COOLDOWN_ACTIVE
    assert "cooldown is active" in dec_cooldown.summary_message.lower()


def test_insufficient_nodes(config):
    engine = DecisionEngine(config)
    single_node = {
        "minikube": NodeMetrics("minikube", "Ready", 800.0, 1000.0)
    }
    single_node["minikube"].calculate_percentages()
    summary = ClusterMetricsSummary(nodes=single_node)
    summary.calculate_summary()

    decision = engine.evaluate(summary)
    assert decision.action == RebalanceAction.NONE
    assert "insufficient" in decision.summary_message.lower()
