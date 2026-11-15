"""
Rule-based decision engine for Kubernetes workload rebalancing.
Evaluates CPU and memory metrics against configurable thresholds, applies
consecutive-sample validation and cooldown checks, and generates structured
rebalancing recommendations.
"""
from __future__ import annotations
import enum
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from monitoring.metrics import ClusterMetricsSummary, NodeMetrics

logger = logging.getLogger("clusterbalancer.decision")


class NodeClassification(str, enum.Enum):
    OVERLOADED = "OVERLOADED"
    UNDERUTILIZED = "UNDERUTILIZED"
    NORMAL = "NORMAL"
    UNKNOWN = "UNKNOWN"


class RebalanceAction(str, enum.Enum):
    NONE = "NONE"
    REBALANCE_WORKLOAD = "REBALANCE_WORKLOAD"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    VALIDATING_SAMPLES = "VALIDATING_SAMPLES"


@dataclass
class NodeDecision:
    node_name: str
    cpu_percent: float
    memory_percent: float
    classification: NodeClassification
    is_ready: bool
    reason: str


@dataclass
class ClusterDecision:
    timestamp: datetime = field(default_factory=datetime.utcnow)
    is_imbalanced: bool = False
    action: RebalanceAction = RebalanceAction.NONE
    source_node: Optional[str] = None       # Overloaded node from which to evict
    target_node: Optional[str] = None       # Underutilized/normal node to receive workload
    cpu_spread: float = 0.0                 # Max CPU - Min CPU
    node_decisions: Dict[str, NodeDecision] = field(default_factory=dict)
    summary_message: str = "Cluster is in balanced state."
    consecutive_hits: int = 0


class DecisionEngine:
    """Evaluates cluster resource utilization and produces rebalancing decisions."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        thresholds = config.get("thresholds", {})
        self.cpu_overload_th = float(thresholds.get("cpu_overload_percent", 75.0))
        self.cpu_underutilized_th = float(thresholds.get("cpu_underutilized_percent", 30.0))
        self.cpu_imbalance_delta = float(thresholds.get("cpu_imbalance_delta", 25.0))
        self.mem_overload_th = float(thresholds.get("memory_overload_percent", 80.0))

        engine_cfg = config.get("engine", {})
        self.consecutive_required = int(engine_cfg.get("consecutive_samples", 2))
        self.cooldown_seconds = int(engine_cfg.get("cooldown_seconds", 60))

        self.last_rebalance_time: Optional[float] = None
        self._consecutive_imbalance_counter: int = 0
        self._last_overloaded_source: Optional[str] = None

    def record_rebalance_executed(self):
        """Notifies the engine that a rebalance action was executed, resetting counters."""
        self.last_rebalance_time = time.time()
        self._consecutive_imbalance_counter = 0
        self._last_overloaded_source = None
        logger.info("Recorded rebalance execution. Cooldown active for %ds.", self.cooldown_seconds)

    def is_in_cooldown(self) -> bool:
        """Returns True if the engine is currently inside the post-rebalance cooldown."""
        if self.last_rebalance_time is None:
            return False
        elapsed = time.time() - self.last_rebalance_time
        return elapsed < self.cooldown_seconds

    def get_cooldown_remaining(self) -> int:
        """Returns remaining cooldown seconds, or 0."""
        if not self.is_in_cooldown():
            return 0
        elapsed = time.time() - self.last_rebalance_time
        return max(0, int(self.cooldown_seconds - elapsed))

    def evaluate(self, summary: ClusterMetricsSummary) -> ClusterDecision:
        """
        Analyzes a ClusterMetricsSummary and returns a comprehensive ClusterDecision.
        """
        decision = ClusterDecision(
            cpu_spread=summary.cpu_spread,
            timestamp=summary.timestamp,
        )

        ready_nodes = [n for n in summary.nodes.values() if n.status == "Ready"]
        if len(ready_nodes) < 2:
            decision.summary_message = "Insufficient active ready nodes to balance (need >= 2)."
            decision.action = RebalanceAction.NONE
            return decision

        # 1. Classify each node
        for n in summary.nodes.values():
            classification = NodeClassification.NORMAL
            reasons = []

            if n.status != "Ready":
                classification = NodeClassification.UNKNOWN
                reasons.append("Node is not Ready")
            elif n.cpu_percent >= self.cpu_overload_th:
                classification = NodeClassification.OVERLOADED
                reasons.append(f"CPU {n.cpu_percent}% >= overload threshold {self.cpu_overload_th}%")
            elif n.cpu_percent <= self.cpu_underutilized_th:
                classification = NodeClassification.UNDERUTILIZED
                reasons.append(f"CPU {n.cpu_percent}% <= underutilized threshold {self.cpu_underutilized_th}%")
            else:
                classification = NodeClassification.NORMAL
                reasons.append(f"CPU {n.cpu_percent}% is within normal range")

            if n.memory_percent >= self.mem_overload_th:
                reasons.append(f"Memory {n.memory_percent}% >= threshold {self.mem_overload_th}%")

            decision.node_decisions[n.name] = NodeDecision(
                node_name=n.name,
                cpu_percent=n.cpu_percent,
                memory_percent=n.memory_percent,
                classification=classification,
                is_ready=(n.status == "Ready"),
                reason="; ".join(reasons),
            )

        # 2. Identify potential source (overloaded) and target (underutilized or lowest load)
        overloaded_nodes = [
            n for n in ready_nodes
            if decision.node_decisions[n.name].classification == NodeClassification.OVERLOADED
        ]
        # Sort by highest CPU first
        overloaded_nodes.sort(key=lambda x: x.cpu_percent, reverse=True)

        underutilized_nodes = [
            n for n in ready_nodes
            if decision.node_decisions[n.name].classification == NodeClassification.UNDERUTILIZED
        ]
        # Sort by lowest CPU first
        underutilized_nodes.sort(key=lambda x: x.cpu_percent)

        # Other candidate targets if no underutilized exists but spread is high
        normal_nodes = [
            n for n in ready_nodes
            if decision.node_decisions[n.name].classification == NodeClassification.NORMAL
        ]
        normal_nodes.sort(key=lambda x: x.cpu_percent)

        candidate_source = overloaded_nodes[0].name if overloaded_nodes else None
        candidate_target = None
        if underutilized_nodes:
            candidate_target = underutilized_nodes[0].name
        elif normal_nodes and overloaded_nodes:
            # Fall back to least utilized normal node
            candidate_target = normal_nodes[0].name

        # 3. Check imbalance condition
        has_imbalance = False
        if candidate_source and candidate_target and candidate_source != candidate_target:
            # Check if spread exceeds threshold or source is overloaded while target has capacity
            source_cpu = summary.nodes[candidate_source].cpu_percent
            target_cpu = summary.nodes[candidate_target].cpu_percent
            spread = source_cpu - target_cpu

            if spread >= self.cpu_imbalance_delta or source_cpu >= self.cpu_overload_th:
                has_imbalance = True

        decision.is_imbalanced = has_imbalance
        decision.source_node = candidate_source
        decision.target_node = candidate_target

        if not has_imbalance:
            self._consecutive_imbalance_counter = 0
            self._last_overloaded_source = None
            decision.action = RebalanceAction.NONE
            decision.summary_message = (
                f"Cluster balanced. CPU spread ({summary.cpu_spread:.1f}%) below delta ({self.cpu_imbalance_delta:.1f}%)."
            )
            return decision

        # 4. Check Cooldown
        if self.is_in_cooldown():
            rem = self.get_cooldown_remaining()
            decision.action = RebalanceAction.COOLDOWN_ACTIVE
            decision.summary_message = (
                f"Imbalance detected ({candidate_source} -> {candidate_target}), but cooldown is active ({rem}s remaining)."
            )
            return decision

        # 5. Consecutive sample hysteresis validation
        if candidate_source == self._last_overloaded_source:
            self._consecutive_imbalance_counter += 1
        else:
            self._consecutive_imbalance_counter = 1
            self._last_overloaded_source = candidate_source

        decision.consecutive_hits = self._consecutive_imbalance_counter

        if self._consecutive_imbalance_counter < self.consecutive_required:
            decision.action = RebalanceAction.VALIDATING_SAMPLES
            decision.summary_message = (
                f"Imbalance detected on {candidate_source}. Validating consecutive samples "
                f"({self._consecutive_imbalance_counter}/{self.consecutive_required})."
            )
            return decision

        # 6. Recommendation confirmed
        decision.action = RebalanceAction.REBALANCE_WORKLOAD
        decision.summary_message = (
            f"Sustained imbalance detected! Recommend rebalancing workload from overloaded node "
            f"'{candidate_source}' ({summary.nodes[candidate_source].cpu_percent}%) to target node "
            f"'{candidate_target}' ({summary.nodes[candidate_target].cpu_percent}%)."
        )
        return decision
