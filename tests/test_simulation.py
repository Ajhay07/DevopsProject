"""
End-to-end integration test verifying multi-cycle monitoring,
decision engine validation, recommendation generation,
and automatic rebalancing with post-rebalance verification.
"""
from main import create_simulated_cluster_manager, load_config, run_cycle
from monitoring.monitor import ClusterMonitor
from decision.decision_engine import DecisionEngine, RebalanceAction
from rebalancer.rebalance import WorkloadRebalancer


def test_end_to_end_rebalance_flow():
    config = load_config("config.yaml")
    cm = create_simulated_cluster_manager()
    monitor = ClusterMonitor(cm, config)
    engine = DecisionEngine(config)
    rebalancer = WorkloadRebalancer(cm, config)

    # Cycle 1: Initial detection, sample validation (1/2)
    s1 = monitor.collect_metrics()
    d1 = engine.evaluate(s1)
    assert d1.is_imbalanced is True
    assert d1.action == RebalanceAction.VALIDATING_SAMPLES
    assert d1.source_node == "minikube-m02"
    assert d1.target_node == "minikube-m03"

    # Cycle 2: Sustained imbalance confirmed, recommendation generated
    s2 = monitor.collect_metrics()
    d2 = engine.evaluate(s2)
    assert d2.action == RebalanceAction.REBALANCE_WORKLOAD
    res_rec = rebalancer.rebalance(d2, s2.pods, mode="recommend")
    assert res_rec.success is True
    assert res_rec.executed is False
    assert res_rec.evicted_pod == "cpu-stress-7f4c9-1"
    assert "[RECOMMENDATION]" in res_rec.message

    # Cycle 3: Automatic execution with safe eviction and replacement tracking
    res_auto = rebalancer.rebalance(d2, s2.pods, mode="automatic")
    assert res_auto.success is True
    assert res_auto.executed is True
    assert res_auto.new_node == "minikube-m03"
    engine.record_rebalance_executed()

    # Cycle 4: Verification of balanced state
    s4 = monitor.collect_metrics()
    d4 = engine.evaluate(s4)
    # CPU on m02 dropped from 86% to 49%, m03 rose from 18% to 40.5%
    assert s4.nodes["minikube-m02"].cpu_percent == 49.0
    assert s4.nodes["minikube-m03"].cpu_percent == 40.5
    assert s4.cpu_spread == 24.0
    assert d4.is_imbalanced is False
    assert d4.action == RebalanceAction.NONE
