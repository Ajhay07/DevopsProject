"""
Continuous cluster monitoring engine.
Retrieves real-time CPU and memory metrics from Kubernetes nodes and pods,
calculates utilization percentages, tracks sample history, and writes CSV logs.
"""
from __future__ import annotations
import csv
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from .metrics import (
    ClusterMetricsSummary,
    NodeMetrics,
    PodMetrics,
    parse_cpu,
    parse_memory,
)

logger = logging.getLogger("clusterbalancer.monitor")


class ClusterMonitor:
    """Collects and aggregates real-time cluster resource metrics."""

    def __init__(self, cluster_manager: Any, config: Dict[str, Any]):
        self.cm = cluster_manager
        self.config = config
        self.history: List[ClusterMetricsSummary] = []
        self.csv_path = config.get("logging", {}).get("metrics_csv", "logs/metrics_history.csv")
        self._init_csv()

    def _init_csv(self):
        """Initializes the CSV metrics log file with headers if not present."""
        if not self.csv_path:
            return
        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
        if not os.path.exists(self.csv_path):
            try:
                with open(self.csv_path, mode="w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "timestamp",
                        "node_name",
                        "status",
                        "cpu_usage_m",
                        "cpu_allocatable_m",
                        "cpu_percent",
                        "memory_usage_mib",
                        "memory_allocatable_mib",
                        "memory_percent",
                        "pod_count",
                    ])
            except Exception as e:
                logger.warning("Could not initialize metrics CSV file: %s", e)

    def collect_metrics(self) -> ClusterMetricsSummary:
        """
        Polls the Kubernetes cluster and Metrics Server to build
        an accurate snapshot of node and pod resource consumption.
        """
        summary = ClusterMetricsSummary()

        # 1. Fetch raw nodes and metrics
        raw_nodes = self.cm.get_nodes()
        node_metrics_raw = self.cm.get_node_metrics()

        for node_info in raw_nodes:
            node_name = node_info["name"]
            status = node_info["status"]

            # Parse allocatable capacity
            alloc_cpu_m = parse_cpu(node_info.get("allocatable_cpu", "0"))
            alloc_mem_mib = parse_memory(node_info.get("allocatable_memory", "0"))

            # Parse live usage if available
            usage_info = node_metrics_raw.get(node_name, {})
            used_cpu_m = parse_cpu(usage_info.get("cpu", "0"))
            used_mem_mib = parse_memory(usage_info.get("memory", "0"))

            nm = NodeMetrics(
                name=node_name,
                status=status,
                cpu_usage_m=used_cpu_m,
                cpu_allocatable_m=alloc_cpu_m,
                memory_usage_mib=used_mem_mib,
                memory_allocatable_mib=alloc_mem_mib,
                roles=node_info.get("roles", []),
            )
            nm.calculate_percentages()
            summary.nodes[node_name] = nm

        # 2. Fetch pods and their usage
        raw_pods = self.cm.get_pods()
        raw_pod_metrics = self.cm.get_pod_metrics()
        pod_metrics_map = {}
        for pm in raw_pod_metrics:
            key = f"{pm.get('namespace')}/{pm.get('name')}"
            total_cpu = 0.0
            total_mem = 0.0
            for c in pm.get("containers", []):
                u = c.get("usage", {})
                total_cpu += parse_cpu(u.get("cpu", "0"))
                total_mem += parse_memory(u.get("memory", "0"))
            pod_metrics_map[key] = (total_cpu, total_mem)

        # Build PodMetrics objects
        exclude_ns = set(self.config.get("safeguards", {}).get("exclude_namespaces", []))
        for p in raw_pods:
            p_name = p["name"]
            p_ns = p["namespace"]
            p_node = p["node_name"]
            phase = p["phase"]
            controller_kind = p.get("controller_kind")
            controller_name = p.get("controller_name")

            if p_node in summary.nodes:
                summary.nodes[p_node].pod_count += 1

            usage_key = f"{p_ns}/{p_name}"
            used_cpu, used_mem = pod_metrics_map.get(usage_key, (0.0, 0.0))

            # Determine baseline evictability
            is_evictable = (
                phase == "Running"
                and p_ns not in exclude_ns
                and controller_kind in ("ReplicaSet", "Deployment")
            )

            pod_obj = PodMetrics(
                name=p_name,
                namespace=p_ns,
                node_name=p_node,
                status=phase,
                cpu_usage_m=used_cpu,
                memory_usage_mib=used_mem,
                controller_kind=controller_kind,
                controller_name=controller_name,
                is_evictable=is_evictable,
                labels=p.get("labels", {}),
            )
            summary.pods.append(pod_obj)

        summary.calculate_summary()

        # Update sliding history (keep last 20 snapshots)
        self.history.append(summary)
        if len(self.history) > 20:
            self.history.pop(0)

        self._record_to_csv(summary)
        return summary

    def _record_to_csv(self, summary: ClusterMetricsSummary):
        """Appends current snapshot data to the metrics CSV log."""
        if not self.csv_path:
            return
        try:
            with open(self.csv_path, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                ts = summary.timestamp.isoformat()
                for nm in summary.nodes.values():
                    writer.writerow([
                        ts,
                        nm.name,
                        nm.status,
                        nm.cpu_usage_m,
                        nm.cpu_allocatable_m,
                        nm.cpu_percent,
                        nm.memory_usage_mib,
                        nm.memory_allocatable_mib,
                        nm.memory_percent,
                        nm.pod_count,
                    ])
        except Exception as e:
            logger.debug("Failed to record metrics to CSV: %s", e)

    def get_recent_history(self, count: int = 5) -> List[ClusterMetricsSummary]:
        """Returns the most recent N metric snapshots."""
        return self.history[-count:]
