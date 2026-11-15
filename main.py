#!/usr/bin/env python3
"""
ClusterBalancer: CPU-Aware Cluster Load Balancing System
Main application controller and CLI interface.
"""
from __future__ import annotations
import argparse
import logging
import os
import signal
import sys
import time
from typing import Any, Dict
from unittest.mock import MagicMock
import yaml

from decision.decision_engine import DecisionEngine, NodeClassification, RebalanceAction
from kubernetes.cluster_manager import ClusterManager
from monitoring.metrics import ClusterMetricsSummary, NodeMetrics, PodMetrics
from monitoring.monitor import ClusterMonitor
from rebalancer.rebalance import WorkloadRebalancer


def setup_logging(config: Dict[str, Any]):
    log_cfg = config.get("logging", {})
    log_file = log_cfg.get("log_file", "logs/clusterbalancer.log")
    level_str = log_cfg.get("level", "INFO").upper()
    level = getattr(logging, level_str, logging.INFO)

    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    if not os.path.exists(config_path):
        print(f"[!] Warning: Config file '{config_path}' not found. Using defaults.")
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def print_banner(mode: str, interval: int, status_str: str, metrics_server_ok: bool):
    print("=" * 68)
    print("                 CLUSTERBALANCER")
    print("           CPU-AWARE CLUSTER LOAD BALANCER")
    print("=" * 68)
    print(f" Cluster Status       : {status_str}")
    print(f" Metrics Server       : {'AVAILABLE' if metrics_server_ok else 'UNAVAILABLE / PENDING'}")
    print(f" Monitoring Interval  : {interval} seconds")
    print(f" Operating Mode       : {mode.upper()}")
    print("=" * 68)


def display_dashboard(
    summary: ClusterMetricsSummary,
    decision: Any,
    rebalance_result: Any,
    mode: str,
):
    print("\nNODE             CPU       MEMORY    PODS   CLASSIFICATION")
    print("-" * 68)
    for name, nm in sorted(summary.nodes.items()):
        node_dec = decision.node_decisions.get(name)
        classification = node_dec.classification.value if node_dec else "UNKNOWN"
        print(
            f"{nm.name:<16} {nm.cpu_percent:>5.1f}%    {nm.memory_percent:>5.1f}%   "
            f"{nm.pod_count:>4}    {classification:<14}"
        )
    print("-" * 68)

    print(
        f"Cluster CPU Avg: {summary.avg_cpu_percent:.1f}% | "
        f"Memory Avg: {summary.avg_memory_percent:.1f}% | "
        f"CPU Spread: {summary.cpu_spread:.1f}%"
    )

    if decision.is_imbalanced:
        print("\n[!] WORKLOAD IMBALANCE DETECTED")
        print(f"    Source (Overloaded) : {decision.source_node or 'None'}")
        print(f"    Target (Available)  : {decision.target_node or 'None'}")
    else:
        print("\n[✓] WORKLOAD STATUS: BALANCED")

    print(f"\nStatus / Action: {decision.action.value}")
    print(f"Details        : {decision.summary_message}")

    if rebalance_result and rebalance_result.message:
        print(f"\nRebalancing Outcome ({mode.upper()} MODE):")
        print(f"  {rebalance_result.message}")
    print("=" * 68)


def create_simulated_cluster_manager():
    """Provides a simulated 3-node cluster matching the project report benchmark."""
    sim_cm = MagicMock()
    sim_cm.connect.return_value = True
    sim_cm.verify_connectivity.return_value = True
    sim_cm.is_metrics_server_available.return_value = True
    sim_cm.is_connected.return_value = True

    sim_cm.get_nodes.return_value = [
        {"name": "minikube", "status": "Ready", "roles": ["control-plane"], "allocatable_cpu": "2000m", "allocatable_memory": "4096Mi"},
        {"name": "minikube-m02", "status": "Ready", "roles": ["worker"], "allocatable_cpu": "2000m", "allocatable_memory": "4096Mi"},
        {"name": "minikube-m03", "status": "Ready", "roles": ["worker"], "allocatable_cpu": "2000m", "allocatable_memory": "4096Mi"},
    ]

    sim_state = {
        "m02_cpu": 1720.0,  # 86.0% CPU
        "m03_cpu": 360.0,   # 18.0% CPU
        "m01_cpu": 500.0,   # 25.0% CPU
        "rebalanced": False,
    }

    def sim_node_metrics():
        return {
            "minikube": {"cpu": f"{int(sim_state['m01_cpu'])}m", "memory": "1720Mi"},
            "minikube-m02": {"cpu": f"{int(sim_state['m02_cpu'])}m", "memory": "2744Mi"},
            "minikube-m03": {"cpu": f"{int(sim_state['m03_cpu'])}m", "memory": "1270Mi"},
        }
    sim_cm.get_node_metrics.side_effect = sim_node_metrics

    def sim_pods(namespace=None):
        if not sim_state["rebalanced"]:
            return [
                {"name": "cpu-stress-7f4c9-1", "namespace": "default", "node_name": "minikube-m02", "phase": "Running", "controller_kind": "ReplicaSet", "controller_name": "cpu-stress-7f4c9"},
                {"name": "cpu-stress-7f4c9-2", "namespace": "default", "node_name": "minikube-m02", "phase": "Running", "controller_kind": "ReplicaSet", "controller_name": "cpu-stress-7f4c9"},
                {"name": "cpu-stress-7f4c9-3", "namespace": "default", "node_name": "minikube-m02", "phase": "Running", "controller_kind": "ReplicaSet", "controller_name": "cpu-stress-7f4c9"},
                {"name": "cpu-stress-7f4c9-4", "namespace": "default", "node_name": "minikube-m03", "phase": "Running", "controller_kind": "ReplicaSet", "controller_name": "cpu-stress-7f4c9"},
            ]
        else:
            return [
                {"name": "cpu-stress-7f4c9-2", "namespace": "default", "node_name": "minikube-m02", "phase": "Running", "controller_kind": "ReplicaSet", "controller_name": "cpu-stress-7f4c9"},
                {"name": "cpu-stress-7f4c9-3", "namespace": "default", "node_name": "minikube-m02", "phase": "Running", "controller_kind": "ReplicaSet", "controller_name": "cpu-stress-7f4c9"},
                {"name": "cpu-stress-7f4c9-4", "namespace": "default", "node_name": "minikube-m03", "phase": "Running", "controller_kind": "ReplicaSet", "controller_name": "cpu-stress-7f4c9"},
                {"name": "cpu-stress-7f4c9-new", "namespace": "default", "node_name": "minikube-m03", "phase": "Running", "controller_kind": "ReplicaSet", "controller_name": "cpu-stress-7f4c9"},
            ]
    sim_cm.get_pods.side_effect = sim_pods

    sim_cm.get_pod_metrics.return_value = [
        {"name": "cpu-stress-7f4c9-1", "namespace": "default", "containers": [{"usage": {"cpu": "450m", "memory": "64Mi"}}]},
        {"name": "cpu-stress-7f4c9-2", "namespace": "default", "containers": [{"usage": {"cpu": "400m", "memory": "64Mi"}}]},
        {"name": "cpu-stress-7f4c9-3", "namespace": "default", "containers": [{"usage": {"cpu": "400m", "memory": "64Mi"}}]},
        {"name": "cpu-stress-7f4c9-4", "namespace": "default", "containers": [{"usage": {"cpu": "150m", "memory": "64Mi"}}]},
    ]

    def sim_evict(name, namespace):
        sim_state["rebalanced"] = True
        sim_state["m02_cpu"] = 980.0  # Drops to ~49%
        sim_state["m03_cpu"] = 810.0  # Adjusts to ~40.5%
        return True

    sim_cm.evict_pod.side_effect = sim_evict
    sim_cm.cordon_node.return_value = True
    sim_cm.uncordon_node.return_value = True
    return sim_cm


def run_cycle(
    monitor: ClusterMonitor,
    engine: DecisionEngine,
    rebalancer: WorkloadRebalancer,
    mode: str,
) -> bool:
    """Executes a single monitoring and balancing cycle."""
    summary = monitor.collect_metrics()
    decision = engine.evaluate(summary)

    rebalance_result = None
    if decision.action == RebalanceAction.REBALANCE_WORKLOAD:
        rebalance_result = rebalancer.rebalance(
            decision=decision,
            pods=summary.pods,
            mode=mode,
        )
        if mode == "automatic" and rebalance_result.executed:
            engine.record_rebalance_executed()

    display_dashboard(
        summary=summary,
        decision=decision,
        rebalance_result=rebalance_result,
        mode=mode,
    )
    return True


def main():
    parser = argparse.ArgumentParser(
        description="ClusterBalancer: CPU-Aware Cluster Load Balancing System"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["recommend", "automatic"],
        default=None,
        help="Operating mode: recommend (read-only) or automatic (executes safe eviction)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single evaluation cycle and exit",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run continuously at configured interval (default)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Monitoring interval in seconds",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run against simulated 3-node cluster benchmark (matching project report)",
    )

    args = parser.parse_args()
    config = load_config(args.config)
    setup_logging(config)

    # Determine runtime options
    mode = args.mode or config.get("engine", {}).get("mode", "recommend")
    interval = args.interval or config.get("engine", {}).get("monitoring_interval", 10)
    run_once = args.once

    if args.simulate:
        cm = create_simulated_cluster_manager()
        status_str = "HEALTHY (SIMULATED BENCHMARK)"
        metrics_ok = True
    else:
        kubeconfig_path = config.get("cluster", {}).get("kubeconfig_path")
        context = config.get("cluster", {}).get("context")
        cm = ClusterManager(kubeconfig_path=kubeconfig_path, context=context)
        connected = cm.connect()
        cluster_healthy = connected and cm.verify_connectivity()
        metrics_ok = cm.is_metrics_server_available()
        status_str = "CONNECTED" if cluster_healthy else "DISCONNECTED"

        if not cluster_healthy:
            print_banner(mode=mode, interval=interval, status_str=status_str, metrics_server_ok=False)
            print("\n[!] Could not connect to an active Kubernetes cluster.")
            print("    To run against live cluster:")
            print("      1. Start Minikube: minikube start --nodes=3")
            print("      2. Enable Metrics: minikube addons enable metrics-server")
            print("    To run immediate academic benchmark demo without waiting:")
            print("      python main.py --simulate --mode recommend --once")
            print("      python main.py --simulate --mode automatic\n")
            sys.exit(1)

    print_banner(mode=mode, interval=interval, status_str=status_str, metrics_server_ok=metrics_ok)

    monitor = ClusterMonitor(cluster_manager=cm, config=config)
    engine = DecisionEngine(config=config)
    rebalancer = WorkloadRebalancer(cluster_manager=cm, config=config)

    # Handle Ctrl+C gracefully
    def handle_signal(sig, frame):
        print("\n[!] Received interrupt signal. Shutting down ClusterBalancer gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)

    if run_once:
        print("[*] Executing single evaluation cycle...")
        run_cycle(monitor, engine, rebalancer, mode)
        return

    print("[*] Starting continuous monitoring. Press Ctrl+C to terminate.")
    cycle_num = 1
    while True:
        print(f"\n--- Cycle #{cycle_num} ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
        try:
            run_cycle(monitor, engine, rebalancer, mode)
        except Exception as e:
            logging.exception("Error in cycle %d: %s", cycle_num, e)
            print(f"[!] Error during cycle #{cycle_num}: {e}")

        time.sleep(interval)
        cycle_num += 1


if __name__ == "__main__":
    main()
