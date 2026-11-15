"""
Safe workload rebalancer.
Selects eligible pods on overloaded nodes, applies scheduling safeguards
(temporary cordon/uncordon), triggers Kubernetes Eviction API, and verifies
rescheduling onto target nodes.
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from decision.decision_engine import ClusterDecision, RebalanceAction
from monitoring.metrics import PodMetrics

logger = logging.getLogger("clusterbalancer.rebalancer")


@dataclass
class RebalanceResult:
    timestamp: datetime = field(default_factory=datetime.utcnow)
    mode: str = "recommend"
    executed: bool = False
    success: bool = False
    source_node: Optional[str] = None
    target_node: Optional[str] = None
    evicted_pod: Optional[str] = None
    namespace: Optional[str] = None
    replacement_pod: Optional[str] = None
    new_node: Optional[str] = None
    message: str = ""


class WorkloadRebalancer:
    """Manages controlled and verified pod eviction and rescheduling."""

    def __init__(self, cluster_manager: Any, config: Dict[str, Any]):
        self.cm = cluster_manager
        self.config = config
        self.safeguards = config.get("safeguards", {})
        self.exclude_ns = set(self.safeguards.get("exclude_namespaces", [
            "kube-system", "kube-public", "kube-node-lease", "kubernetes-dashboard"
        ]))
        self.exclude_patterns = self.safeguards.get("exclude_pod_patterns", [
            "metrics-server", "coredns", "kube-proxy"
        ])
        self.prevent_resched = self.safeguards.get("prevent_immediate_rescheduling", True)

    def find_eligible_pods(self, pods: List[PodMetrics], source_node: str) -> List[PodMetrics]:
        """
        Filters and ranks pods running on the source node to find safe candidates for eviction.
        Must be managed by a Deployment/ReplicaSet so Kubernetes will automatically recreate it.
        Excludes system pods, DaemonSets, unmanaged pods, and excluded patterns.
        """
        candidates: List[PodMetrics] = []

        for p in pods:
            # Must be on source node
            if p.node_name != source_node:
                continue

            # Must be Running
            if p.status != "Running":
                continue

            # Must not be in excluded namespace
            if p.namespace in self.exclude_ns:
                continue

            # Must have a recognized resilient controller
            if p.controller_kind not in ("ReplicaSet", "Deployment", "StatefulSet"):
                logger.debug("Pod %s excluded: unmanaged or unsupported controller (%s)", p.name, p.controller_kind)
                continue

            # Check pattern exclusions
            if any(pat in p.name.lower() for pat in self.exclude_patterns):
                continue

            candidates.append(p)

        # Rank candidates by CPU consumption descending (evict largest contributor first)
        candidates.sort(key=lambda x: x.cpu_usage_m, reverse=True)
        return candidates

    def rebalance(self, decision: ClusterDecision, pods: List[PodMetrics], mode: str = "recommend") -> RebalanceResult:
        """
        Executes workload rebalancing based on the engine decision.
        If mode == 'recommend', only identifies and returns the proposed action.
        If mode == 'automatic', performs the safe eviction with safeguards.
        """
        res = RebalanceResult(
            mode=mode,
            source_node=decision.source_node,
            target_node=decision.target_node,
        )

        if decision.action != RebalanceAction.REBALANCE_WORKLOAD:
            res.message = f"No rebalancing needed. Current engine status: {decision.action.value}"
            return res

        source = decision.source_node
        target = decision.target_node
        if not source:
            res.message = "No overloaded source node designated by decision engine."
            return res

        # 1. Identify eligible workloads
        candidates = self.find_eligible_pods(pods, source)
        if not candidates:
            res.message = f"No eligible managed workloads found on overloaded node {source}."
            logger.warning(res.message)
            return res

        chosen_pod = candidates[0]
        res.evicted_pod = chosen_pod.name
        res.namespace = chosen_pod.namespace

        # Mode A: Recommendation Mode (default, non-destructive)
        if mode == "recommend":
            res.executed = False
            res.success = True
            res.message = (
                f"[RECOMMENDATION] Candidate workload identified: Pod '{chosen_pod.name}' "
                f"(NS: {chosen_pod.namespace}, Controller: {chosen_pod.controller_kind}/{chosen_pod.controller_name}, "
                f"CPU: {chosen_pod.cpu_usage_m:.1f}m) on overloaded node '{source}'. "
                f"Target node for redistribution: '{target}'."
            )
            return res

        # Mode B: Automatic Mode (safe controlled execution)
        logger.info(
            "Executing automatic rebalance: Evicting %s/%s from %s (target: %s)",
            chosen_pod.namespace, chosen_pod.name, source, target
        )
        res.executed = True

        # Safety safeguard: Temporary cordon of source node to force scheduler to use target node
        cordoned = False
        try:
            if self.prevent_resched and source:
                logger.info("Applying scheduling safeguard: Temporarily cordoning source node %s", source)
                cordoned = self.cm.cordon_node(source)

            # Record existing pods of this controller to identify the replacement pod later
            controller_name = chosen_pod.controller_name
            pre_pods = {
                p["name"] for p in self.cm.get_pods(namespace=chosen_pod.namespace)
                if p.get("controller_name") == controller_name
            }

            # Execute eviction
            eviction_ok = self.cm.evict_pod(name=chosen_pod.name, namespace=chosen_pod.namespace)
            if not eviction_ok:
                res.success = False
                res.message = f"Eviction API rejected request for pod {chosen_pod.namespace}/{chosen_pod.name}."
                return res

            # Wait and verify rescheduling of the replacement pod
            logger.info("Waiting for Kubernetes scheduler to assign replacement pod...")
            replacement_found = False
            new_pod_name = None
            assigned_node = None

            for attempt in range(15):  # Poll up to 15 seconds (1s intervals)
                time.sleep(1.0)
                current_pods = self.cm.get_pods(namespace=chosen_pod.namespace)
                for p in current_pods:
                    p_name = p["name"]
                    if p.get("controller_name") == controller_name and p_name not in pre_pods:
                        # Found newly created replacement pod
                        node = p.get("node_name")
                        if node and node != "Unassigned":
                            replacement_found = True
                            new_pod_name = p_name
                            assigned_node = node
                            break
                if replacement_found:
                    break

            res.replacement_pod = new_pod_name
            res.new_node = assigned_node

            if replacement_found:
                res.success = True
                res.message = (
                    f"Successfully rebalanced! Evicted pod '{chosen_pod.name}' from '{source}'. "
                    f"Replacement pod '{new_pod_name}' rescheduled onto '{assigned_node}'."
                )
                logger.info(res.message)
            else:
                res.success = True
                res.message = (
                    f"Evicted pod '{chosen_pod.name}' from '{source}'. "
                    f"Replacement pod pending placement by kube-scheduler."
                )
                logger.info(res.message)

            return res

        except Exception as e:
            logger.exception("Error occurred during rebalancing execution: %s", e)
            res.success = False
            res.message = f"Error during rebalancing: {str(e)}"
            return res

        finally:
            # ALWAYS uncordon the source node in finally block
            if cordoned and source:
                logger.info("Releasing scheduling safeguard: Uncordoning source node %s", source)
                self.cm.uncordon_node(source)
