"""
Kubernetes resource quantity parsing and metrics data models.
Provides accurate conversions for CPU (cores, millicores, nanocores)
and Memory (bytes, Ki, Mi, Gi, Ti, etc.).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


def parse_cpu(val: str | int | float) -> float:
    """
    Parses a Kubernetes CPU quantity string and returns millicores (m).
    Examples:
      '2' -> 2000.0 m
      '250m' -> 250.0 m
      '150000000n' -> 150.0 m
      '50000u' -> 50.0 m
    """
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val) * 1000.0

    val_str = str(val).strip()
    if not val_str:
        return 0.0

    # Nanocores (n) -> 1m = 1,000,000n
    if val_str.endswith("n"):
        num = float(val_str[:-1])
        return num / 1_000_000.0

    # Microcores (u) -> 1m = 1,000u
    if val_str.endswith("u"):
        num = float(val_str[:-1])
        return num / 1_000.0

    # Millicores (m)
    if val_str.endswith("m"):
        return float(val_str[:-1])

    # Plain float/int representing whole cores
    try:
        return float(val_str) * 1000.0
    except ValueError:
        raise ValueError(f"Unrecognized CPU quantity format: {val!r}")


def parse_memory(val: str | int | float) -> float:
    """
    Parses a Kubernetes memory quantity string and returns Mebibytes (MiB).
    Examples:
      '1024Ki' -> 1.0 MiB
      '256Mi' -> 256.0 MiB
      '2Gi' -> 2048.0 MiB
      '1048576' (bytes) -> 1.0 MiB
    """
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val) / (1024.0 * 1024.0)

    val_str = str(val).strip()
    if not val_str:
        return 0.0

    # Binary SI units (powers of 1024)
    binary_units = {
        "Ki": 1024.0,
        "Mi": 1024.0 ** 2,
        "Gi": 1024.0 ** 3,
        "Ti": 1024.0 ** 4,
        "Pi": 1024.0 ** 5,
        "Ei": 1024.0 ** 6,
    }
    for unit, factor in binary_units.items():
        if val_str.endswith(unit):
            num = float(val_str[: -len(unit)])
            bytes_val = num * factor
            return bytes_val / (1024.0 * 1024.0)

    # Decimal SI units (powers of 1000)
    decimal_units = {
        "k": 1000.0,
        "M": 1000.0 ** 2,
        "G": 1000.0 ** 3,
        "T": 1000.0 ** 4,
        "P": 1000.0 ** 5,
        "E": 1000.0 ** 6,
    }
    for unit, factor in decimal_units.items():
        if val_str.endswith(unit):
            num = float(val_str[: -len(unit)])
            bytes_val = num * factor
            return bytes_val / (1024.0 * 1024.0)

    # Pure integer / float in bytes
    try:
        bytes_val = float(val_str)
        return bytes_val / (1024.0 * 1024.0)
    except ValueError:
        raise ValueError(f"Unrecognized memory quantity format: {val!r}")


@dataclass
class NodeMetrics:
    name: str
    status: str = "Ready"  # Ready, NotReady
    cpu_usage_m: float = 0.0
    cpu_allocatable_m: float = 0.0
    cpu_percent: float = 0.0
    memory_usage_mib: float = 0.0
    memory_allocatable_mib: float = 0.0
    memory_percent: float = 0.0
    pod_count: int = 0
    roles: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def calculate_percentages(self):
        if self.cpu_allocatable_m > 0:
            self.cpu_percent = round((self.cpu_usage_m / self.cpu_allocatable_m) * 100.0, 2)
        else:
            self.cpu_percent = 0.0

        if self.memory_allocatable_mib > 0:
            self.memory_percent = round((self.memory_usage_mib / self.memory_allocatable_mib) * 100.0, 2)
        else:
            self.memory_percent = 0.0


@dataclass
class PodMetrics:
    name: str
    namespace: str
    node_name: str
    status: str
    cpu_usage_m: float = 0.0
    memory_usage_mib: float = 0.0
    controller_kind: Optional[str] = None  # e.g., ReplicaSet, Deployment, DaemonSet, StatefulSet, Job
    controller_name: Optional[str] = None
    is_evictable: bool = False
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ClusterMetricsSummary:
    nodes: Dict[str, NodeMetrics] = field(default_factory=dict)
    pods: List[PodMetrics] = field(default_factory=list)
    avg_cpu_percent: float = 0.0
    avg_memory_percent: float = 0.0
    max_cpu_node: Optional[str] = None
    min_cpu_node: Optional[str] = None
    cpu_spread: float = 0.0  # max_cpu_percent - min_cpu_percent
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def calculate_summary(self):
        if not self.nodes:
            self.avg_cpu_percent = 0.0
            self.avg_memory_percent = 0.0
            self.max_cpu_node = None
            self.min_cpu_node = None
            self.cpu_spread = 0.0
            return

        cpu_vals = [(name, n.cpu_percent) for name, n in self.nodes.items() if n.status == "Ready"]
        mem_vals = [n.memory_percent for n in self.nodes.values() if n.status == "Ready"]

        if cpu_vals:
            self.avg_cpu_percent = round(sum(v for _, v in cpu_vals) / len(cpu_vals), 2)
            sorted_cpu = sorted(cpu_vals, key=lambda x: x[1])
            self.min_cpu_node = sorted_cpu[0][0]
            self.max_cpu_node = sorted_cpu[-1][0]
            self.cpu_spread = round(sorted_cpu[-1][1] - sorted_cpu[0][1], 2)
        else:
            self.avg_cpu_percent = 0.0
            self.max_cpu_node = None
            self.min_cpu_node = None
            self.cpu_spread = 0.0

        if mem_vals:
            self.avg_memory_percent = round(sum(mem_vals) / len(mem_vals), 2)
        else:
            self.avg_memory_percent = 0.0
