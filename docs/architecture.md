# ClusterBalancer System Architecture

## 1. System Overview

**ClusterBalancer** is a CPU-aware Kubernetes workload monitoring and intelligent rebalancing system developed for academic evaluation under **21IPE333P – Essentials in Cloud and DevOps** (SRM Institute of Science and Technology).

The core mission of the project is to continuously monitor real-time CPU and memory utilization across Kubernetes nodes, detect sustained resource imbalances, identify suitable candidate workloads, and dynamically redistribute pods across underutilized nodes using Kubernetes Eviction and scheduling safeguards.

```
+-----------------------------------------------------------------------------------+
|                            KUBERNETES MULTI-NODE CLUSTER                          |
|                                                                                   |
|  +--------------------+    +--------------------+    +--------------------+       |
|  |      minikube      |    |    minikube-m02    |    |    minikube-m03    |       |
|  |  (Control-Plane)   |    |  (Worker 1 - High) |    |  (Worker 2 - Low)  |       |
|  +--------------------+    +--------------------+    +--------------------+       |
+-----------|--------------------------|-------------------------|------------------+
            |                          |                         |
            +--------------------------v-------------------------+
                                       |
                         +-----------------------------+
                         |       metrics-server        |
                         |  (metrics.k8s.io /v1beta1)  |
                         +--------------|--------------+
                                        | (Real-time CPU/Mem Usage)
+---------------------------------------v-------------------------------------------+
|                               CLUSTERBALANCER                                     |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  | Module 1: Cluster Manager (kubernetes/cluster_manager.py)                   |  |
|  | - Authenticates with Kubernetes API (In-Cluster / Kubeconfig)               |  |
|  | - Node allocatable capacity & condition tracking (Ready/NotReady)           |  |
|  | - Metrics Server health check                                               |  |
|  | - Pod Eviction API & Safe Node Cordon / Uncordon operations                 |  |
|  +------------------------------------|----------------------------------------+  |
|                                       v                                           |
|  +-----------------------------------------------------------------------------+  |
|  | Module 2: Monitoring Engine (monitoring/monitor.py & metrics.py)            |  |
|  | - Quantity conversion (nanocores/millicores, Ki/Mi/Gi to MiB)              |  |
|  | - Node & Pod utilization percentage calculation                            |  |
|  | - Historical CSV metric logging (logs/metrics_history.csv)                  |  |
|  +------------------------------------|----------------------------------------+  |
|                                       v                                           |
|  +-----------------------------------------------------------------------------+  |
|  | Module 3: Decision Engine (decision/decision_engine.py)                     |  |
|  | - Configurable threshold evaluation (CPU Overload >75%, Underutilized <30%)|  |
|  | - Node Classification: OVERLOADED, UNDERUTILIZED, NORMAL, UNKNOWN           |  |
|  | - Multi-sample Hysteresis Validation (consecutive_samples: 2)               |  |
|  | - Post-rebalance Cooldown Protection (cooldown_seconds: 60)                 |  |
|  | - Action Designation: NONE, VALIDATING_SAMPLES, REBALANCE_WORKLOAD        |  |
|  +------------------------------------|----------------------------------------+  |
|                                       v                                           |
|  +-----------------------------------------------------------------------------+  |
|  | Module 4: Workload Rebalancer (rebalancer/rebalance.py)                     |  |
|  | - Safety Exclusions: kube-system, DaemonSets, Standalone unmanaged pods    |  |
|  | - Candidate Ranking: Deployment pods sorted by CPU consumption desc        |  |
|  | - Mode A (Recommend): Identifies candidate and prints proposed action      |  |
|  | - Mode B (Automatic): Applies temporary cordon -> Eviction API ->           |  |
|  |   Verifies new pod placement on target node -> Uncordons source node        |  |
|  +------------------------------------|----------------------------------------+  |
|                                       v                                           |
|  +-----------------------------------------------------------------------------+  |
|  | Module 5: Main Controller (main.py)                                         |  |
|  | - CLI interface (--mode, --interval, --once, --continuous, --simulate)     |  |
|  | - Structured terminal dashboard & continuous cycle execution                |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Component Breakdown

### 2.1 Kubernetes Cluster Manager (`kubernetes/cluster_manager.py`)
- Provides an abstraction layer over the Kubernetes Python Client (`client.CoreV1Api`, `client.CustomObjectsApi`, `client.PolicyV1Api`).
- Queries node conditions (`Ready`/`NotReady`), labels, taints, and allocatable CPU/RAM.
- Connects to `metrics.k8s.io/v1beta1` to fetch actual real-time consumption.
- Implements `cordon_node` and `uncordon_node` by patching node `spec.unschedulable`.
- Implements `evict_pod` using the official `V1Eviction` API with fallback to controlled deletion.

### 2.2 Monitoring Engine (`monitoring/monitor.py`, `monitoring/metrics.py`)
- Standardizes CPU quantities into **millicores (m)**:
  $$\text{CPU (m)} = \frac{\text{nanocores}}{1,000,000}$$
- Standardizes memory quantities into **Mebibytes (MiB)**:
  $$\text{RAM (MiB)} = \frac{\text{bytes}}{1024 \times 1024}$$
- Computes node utilization percentages:
  $$\text{CPU \%} = \frac{\text{CPU Usage (m)}}{\text{Allocatable CPU (m)}} \times 100$$
- Maps pods to their respective nodes, tracking which controller manages them (`ReplicaSet`, `Deployment`, `DaemonSet`, or `None`).
- Appends telemetry records to `logs/metrics_history.csv` for before-and-after auditability.

### 2.3 Decision Engine (`decision/decision_engine.py`)
- **Threshold Analysis**:
  - `OVERLOADED`: Node CPU $\ge 75\%$
  - `UNDERUTILIZED`: Node CPU $\le 30\%$
  - `NORMAL`: Node CPU between $30\%$ and $75\%$
- **Spread & Imbalance Detection**:
  $$\text{Spread} = \max(\text{Node CPU \%}) - \min(\text{Node CPU \%})$$
  Imbalance is flagged when $\text{Spread} \ge 25\%$ or an active node exceeds the overload threshold while another has available capacity.
- **Hysteresis & Consecutive Sampling**:
  Requires $N$ consecutive monitoring cycles (default: 2) confirming the same overloaded source node before recommending eviction. This prevents flapping caused by transient CPU spikes.
- **Cooldown Timer**:
  Suppresses new rebalancing actions for $T$ seconds (default: 60s) after an eviction to allow the cluster state and Metrics Server window to stabilize.

### 2.4 Workload Rebalancer (`rebalancer/rebalance.py`)
- **Safety Gate**:
  - Never evicts system namespaces (`kube-system`, `kube-public`, `kube-node-lease`).
  - Never touches DaemonSet pods (they will only re-land on the exact same node).
  - Never evicts unmanaged standalone pods (they have no replica controller to recreate them).
  - Never evicts critical system components (`metrics-server`, `coredns`, `kube-proxy`).
- **Scheduling Safeguards**:
  - Evicting a pod alone does not guarantee the standard kube-scheduler will place it elsewhere.
  - ClusterBalancer temporarily cordons the overloaded source node prior to eviction.
  - The replacement pod created by the Deployment controller is forced to schedule on available worker nodes.
  - The source node is guaranteed to be uncordoned immediately after verification (or via `finally` block).

---

## 3. Reference Systems & Academic Context

### 3.1 Kubernetes Descheduler (`kubernetes-sigs/descheduler`)
- **Descheduler Focus**: Official SIG project that evicts pods based on static rules, node taints, affinity violations, topology spread constraints, or resource *requests* (declared reservations).
- **ClusterBalancer Distinction**: Operates on **live, real-time CPU and memory metrics** collected from the `metrics-server` rather than declared requests. Provides continuous dynamic adaptation for bursty workloads.

### 3.2 Kubernetes Autoscaler (`kubernetes/autoscaler`)
- **Cluster Autoscaler Focus**: Provisions and terminates physical/cloud VM nodes in response to unschedulable pods.
- **Horizontal Pod Autoscaler (HPA) Focus**: Scales replica counts ($N \to N+k$) of Deployments.
- **ClusterBalancer Distinction**: Focuses on **intra-cluster workload distribution** across existing nodes. Optimizes density and prevents hotspots without triggering expensive infrastructure provisioning.
