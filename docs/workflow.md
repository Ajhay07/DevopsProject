# ClusterBalancer Operational Workflow

This document describes the operational phases, state transitions, and decision loops executed by the ClusterBalancer system.

```
+-------------------------------------------------------------------------------+
|                               OPERATIONAL PHASES                              |
+-------------------------------------------------------------------------------+
  [ Phase 1: Ingestion ]
           │
           ▼
  [ Phase 2: Analysis ]
           │
           ▼
  [ Phase 3: Decision ]
           │
     ┌─────┴────────────────┐
     ▼                      ▼
  [ Mode A: Recommend ]   [ Mode B: Automatic ]
     │                      │
     │                      ├─> Step 1: Candidate Pod Selection
     │                      ├─> Step 2: Source Node Cordon
     │                      ├─> Step 3: Eviction API Call
     │                      ├─> Step 4: Verification of Rescheduling
     │                      └─> Step 5: Source Node Uncordon
     │                      │
     └─────────────┬────────┘
                   ▼
  [ Phase 4: Cooldown & Telemetry Logging ]
                   │
                   ▼
         (Repeat at Interval)
```

---

## Phase 1: Metrics Collection & Ingestion
1. ClusterManager authenticates to the Kubernetes API server using the active kubeconfig or service account token.
2. ClusterManager verifies that the `metrics.k8s.io` API group is registered and reachable.
3. Node capacities (allocatable CPU millicores and memory MiB) are retrieved via `CoreV1Api.list_node()`.
4. Real-time usage metrics are fetched via `CustomObjectsApi.list_cluster_custom_object(group="metrics.k8s.io", version="v1beta1", plural="nodes")`.
5. Pod listings and per-container CPU/memory metrics are fetched.
6. The `ClusterMetricsSummary` object is assembled and recorded to `logs/metrics_history.csv`.

---

## Phase 2: Analysis & Node Classification
1. Every Ready node is evaluated against configured thresholds:
   - If $\text{CPU \%} \ge 75\% \implies \text{NodeClassification.OVERLOADED}$
   - If $\text{CPU \%} \le 30\% \implies \text{NodeClassification.UNDERUTILIZED}$
   - If $30\% < \text{CPU \%} < 75\% \implies \text{NodeClassification.NORMAL}$
2. Cluster-wide average CPU and memory percentages are calculated.
3. The CPU spread ($\max - \min$) is computed.
4. If an overloaded node exists and another node has capacity (or if the CPU spread $\ge 25\%$), the cluster is flagged as **IMBALANCED**.

---

## Phase 3: Decision & Hysteresis Validation
1. **Cooldown Check**: If a rebalancing was executed within the last 60 seconds (`cooldown_seconds`), the engine holds and returns status `COOLDOWN_ACTIVE`.
2. **Consecutive Sample Check**:
   - Sample #1: Status `VALIDATING_SAMPLES (1/2)`. No action is taken.
   - Sample #2 (if imbalance persists on the same source node): Status `REBALANCE_WORKLOAD (2/2)`. Action is sanctioned.
3. Overloaded source node and target node are designated:
   - `source_node`: Node with highest CPU utilization.
   - `target_node`: Node with lowest CPU utilization.

---

## Phase 4: Rebalancing Execution

### Path A: Recommendation Mode (`--mode recommend`)
- Identifies the highest-consuming managed Deployment pod on the source node.
- Validates that the pod is not in an excluded namespace or DaemonSet.
- Prints the recommendation to the terminal dashboard:
  ```text
  [RECOMMENDATION] Candidate workload identified: Pod 'cpu-stress-...' on overloaded node 'minikube-m02'.
  Target node for redistribution: 'minikube-m03'.
  ```
- No changes or mutations are made to Kubernetes resources.

### Path B: Automatic Mode (`--mode automatic`)
1. **Candidate Selection**:
   - Queries pods running on `source_node`.
   - Filters out system pods, DaemonSets, and unmanaged pods.
   - Ranks eligible pods by CPU usage descending.
2. **Scheduling Safeguard (Cordon)**:
   - Sets `source_node` to `spec.unschedulable = True`.
   - Prevents the standard kube-scheduler from placing the replacement pod back on the overloaded node.
3. **Eviction**:
   - Calls `create_namespaced_pod_eviction` with a 30-second grace period.
   - Respects active PodDisruptionBudgets.
4. **Rescheduling Verification**:
   - Polls the Kubernetes API to detect the new replacement pod created by the ReplicaSet controller.
   - Confirms that the replacement pod is placed onto an eligible node (e.g., `target_node`).
5. **Safeguard Release (Uncordon)**:
   - Sets `source_node` to `spec.unschedulable = False` inside a `finally` block to guarantee node availability.
6. **Telemetry & Audit**:
   - Logs the eviction event, evicted pod name, replacement pod name, and assigned node in `logs/clusterbalancer.log`.
   - Enters post-rebalance cooldown.
