# CPU-Aware Cluster Load Balancing System (ClusterBalancer)

**Academic Engineering Project**  
**Course Code:** 21IPE333P – Essentials in Cloud and DevOps  
**Department:** Cloud and DevOps, SRM Institute of Science and Technology  
**Authors:** Ajhay A.M (RA2311003011998) & S.Thasarrathi (RA2311003012027)  
**Supervisor:** Dr. Velliangiri S, Assistant Professor  

---

## 1. Project Overview

**ClusterBalancer** is an intelligent, automated Kubernetes workload monitoring and rebalancing system. In production and private Kubernetes deployments, workload placement performed at pod creation time frequently leads to node hotspotting, resource starvation, and asymmetric node utilization over time as application demands fluctuate.

ClusterBalancer continuously collects real-time CPU and memory metrics via the Kubernetes Metrics API, detects sustained node imbalances using a rule-based decision engine with hysteresis validation, and executes controlled, verifiable pod redistribution across underutilized cluster nodes using the Kubernetes Eviction API and scheduling safeguards.

---

## 2. Problem Statement

The default Kubernetes scheduler (`kube-scheduler`) evaluates node scores and binds pods to nodes **only once at creation time**. It has no native mechanism to re-evaluate or relocate running workloads when:
- Certain pods begin consuming significantly more CPU than their initial request baseline.
- Workloads become concentrated on a subset of nodes, causing CPU throttling and latency spikes.
- Neighboring nodes remain substantially underutilized, leading to poor cost efficiency and resource wastage.

Existing solutions such as the static Descheduler operate primarily on declared *resource requests* rather than live utilization. ClusterBalancer solves this by monitoring **live, actual CPU and memory utilization**, identifying overloaded nodes, and orchestrating safe workload redistribution.

---

## 3. Project Objectives

- **Continuous Metrics Collection**: Query `metrics.k8s.io` to parse raw CPU (nanocores/millicores) and RAM (Ki/Mi/Gi) without simulated or fabricated values.
- **Intelligent Imbalance Detection**: Implement a configurable decision engine classifying nodes as `OVERLOADED` ($\ge 75\%$), `UNDERUTILIZED` ($\le 30\%$), or `NORMAL`, evaluating CPU spreads.
- **Hysteresis & Flapping Protection**: Require consecutive sample validation and post-eviction cooldowns to prevent flapping caused by transient spikes.
- **Safe Workload Redistribution**: Exclude system pods (`kube-system`), DaemonSets, and unmanaged pods. Prioritize high-consumption managed Deployment pods.
- **Verified Placement**: Apply safe temporary cordons to overloaded source nodes during eviction so the Kubernetes scheduler is guaranteed to place replacement pods onto available target nodes.
- **Dual Operating Modes**:
  - `Recommendation Mode` (Default, non-destructive audit and recommendation).
  - `Automatic Mode` (Explicitly enabled controlled eviction and rescheduling).

---

## 4. System Architecture

```text
+---------------------------------------------------------------------------------+
|                          KUBERNETES MULTI-NODE CLUSTER                          |
|                                                                                 |
|  +--------------------+   +--------------------+   +--------------------+       |
|  |      minikube      |   |    minikube-m02    |   |    minikube-m03    |       |
|  |  (Control-Plane)   |   | (Worker 1 - High)  |   | (Worker 2 - Low)   |       |
|  +--------------------+   +--------------------+   +--------------------+       |
+-----------|-------------------------|------------------------|------------------+
            |                         |                        |
            +-------------------------v------------------------+
                                      |
                        +---------------------------+
                        |      metrics-server       |
                        | (metrics.k8s.io /v1beta1) |
                        +-------------|-------------+
                                      | (Live Node/Pod Metrics)
+-------------------------------------v-------------------------------------------+
|                              CLUSTERBALANCER                                    |
|                                                                                 |
|   1. Cluster Manager (kubernetes/cluster_manager.py)                            |
|      - Authenticates via Kubeconfig / In-Cluster SA                             |
|      - Node condition checks, Eviction API, Cordon / Uncordon                   |
|                                                                                 |
|   2. Monitoring Engine (monitoring/monitor.py & metrics.py)                     |
|      - CPU nanocores -> millicores, Memory bytes -> MiB                         |
|      - Node & Pod utilization %, Historical CSV metrics logging                 |
|                                                                                 |
|   3. Decision Engine (decision/decision_engine.py)                              |
|      - Threshold evaluation, Hysteresis (2 samples), Cooldown (60s)             |
|      - Overloaded source & underutilized target designation                     |
|                                                                                 |
|   4. Workload Rebalancer (rebalancer/rebalance.py)                              |
|      - Safety filters (Exclude kube-system, DaemonSets, Standalone pods)        |
|      - Cordon source -> Evict pod -> Verify placement -> Uncordon source        |
|                                                                                 |
|   5. Main Controller (main.py)                                                  |
|      - CLI dashboard, cycle management, audit trail                             |
+---------------------------------------------------------------------------------+
```

---

## 5. Technology Stack

| Technology | Purpose |
|---|---|
| **Python 3.10+** | Core monitoring, decision engine, and rebalancer implementation |
| **Kubernetes** | Container orchestration platform |
| **Minikube** | Local 3-node cluster virtualization environment |
| **Docker Desktop** | Container runtime engine |
| **kubectl** | Kubernetes command-line cluster management |
| **Metrics Server** | Node and pod real-time resource metrics source |
| **Kubernetes Python Client** | Official Python API for cluster interactions |
| **PyYAML** | YAML configuration file management |
| **pytest** | Automated unit and integration test framework |
| **Git & GitHub** | Version control and collaborative source code management |
| **GitHub Actions** | CI/CD automated linting and unit test pipeline |

---

## 6. Directory Structure

```text
DevopsProject/
│
├── monitoring/
│   ├── __init__.py
│   ├── monitor.py               # Live node/pod metrics collection & CSV logging
│   └── metrics.py               # Unit parsing (CPU m/n/u, Mem Ki/Mi/Gi) & dataclasses
│
├── decision/
│   ├── __init__.py
│   └── decision_engine.py       # Threshold evaluation, hysteresis & cooldown
│
├── rebalancer/
│   ├── __init__.py
│   └── rebalance.py             # Safe pod filtering, cordon safeguard, eviction & verification
│
├── kubernetes/
│   ├── __init__.py
│   ├── cluster_manager.py       # Kube API authentication, node status, eviction & cordon
│   └── descheduler.yaml         # Official Descheduler LowNodeUtilization manifest
│
├── deployment/
│   ├── namespace.yaml           # Dedicated demo namespace
│   ├── workload.yaml            # 6-replica CPU-stress deployment
│   └── stress-test.yaml         # Targeted uneven stress test deployment
│
├── tests/
│   ├── test_metrics.py          # Unit tests for quantity parsing & data models
│   ├── test_decision.py         # Unit tests for threshold & hysteresis logic
│   ├── test_rebalancer.py       # Unit tests for pod filtering & safe eviction flow
│   ├── test_monitor.py          # Unit tests for metrics aggregation & CSV recording
│   └── test_simulation.py       # Integration test for end-to-end rebalancing flow
│
├── logs/
│   ├── clusterbalancer.log      # Application execution and audit log
│   └── metrics_history.csv      # Telemetry history for before/after comparison
│
├── docs/
│   ├── architecture.md          # Comprehensive system architecture & diagrams
│   ├── workflow.md              # Detailed state machine & operational workflow
│   └── demo-guide.md            # Step-by-step demonstration instructions
│
├── .github/
│   └── workflows/
│       └── ci.yml               # GitHub Actions CI pipeline
│
├── config.yaml                  # System configuration (thresholds, safeguards, intervals)
├── requirements.txt             # Python dependencies
├── main.py                      # Application entrypoint & CLI interface
├── Dockerfile                   # Production container image definition
├── README.md                    # Project documentation
└── .gitignore                   # Git exclusion rules
```

---

## 7. Prerequisites and Setup Instructions

### 7.1 System Prerequisites
- **Operating System:** Windows 10/11, Linux, or macOS
- **Hardware:** 8GB+ RAM, 4+ CPU cores, 20GB free disk space
- **Software:** Python 3.10+, Docker Desktop, Minikube, kubectl, Git

### 7.2 Python Environment Setup
```powershell
Set-Location "C:\Users\Ajhay\DevopsProject"
pip install -r requirements.txt
```

### 7.3 Multi-Node Cluster Creation
Start a 3-node Minikube cluster using Docker:
```powershell
minikube start --nodes=3 --driver=docker --cpus=2 --memory=2048
```

Verify nodes:
```powershell
kubectl get nodes -o wide
```

Enable and verify Metrics Server:
```powershell
minikube addons enable metrics-server
kubectl top nodes
```

### 7.4 Workload Deployment
Deploy the 6-replica CPU-stress workload:
```powershell
kubectl apply -f deployment/workload.yaml
kubectl get pods -o wide -l app=cpu-stress
```

---

## 8. Running ClusterBalancer

### 8.1 Recommendation Mode (Default / Safe)
```powershell
python main.py --mode recommend --continuous --interval 10
```
- Queries live metrics every 10 seconds.
- Evaluates CPU and RAM against thresholds.
- Identifies overloaded nodes and recommends candidate workloads without modifying the cluster.

### 8.2 Automatic Mode (Controlled Rebalancing)
```powershell
python main.py --mode automatic --continuous --interval 10
```
- Applies temporary cordon to the overloaded source node.
- Evicts the selected deployment pod via Kubernetes Eviction API.
- Confirms replacement pod is placed onto an underutilized node.
- Uncordons the source node and logs the audit event.

### 8.3 Instant Offline / Simulated Benchmark Demonstration
To demonstrate the full detection, recommendation, eviction, and rescheduling lifecycle immediately:
```powershell
# Single-cycle evaluation
python main.py --simulate --mode recommend --once

# Multi-cycle automated rebalancing demonstration
python main.py --simulate --mode automatic --interval 5
```

---

## 9. Decision Engine & Rebalancer Logic

### 9.1 Classification Thresholds
- **CPU Overloaded**: Node CPU $\ge 75\%$
- **CPU Underutilized**: Node CPU $\le 30\%$
- **CPU Normal**: $30\% < \text{CPU} < 75\%$
- **Imbalance Delta**: CPU Spread $(\max - \min) \ge 25\%$

### 9.2 Hysteresis and Cooldown
- **Consecutive Sample Requirement**: 2 consecutive cycles confirming the same overloaded source node are required before triggering a rebalance action.
- **Cooldown Window**: 60 seconds of post-rebalancing quiet period to allow cluster metrics to stabilize.

### 9.3 Safety Filters
The rebalancer strictly inspects candidate workloads:
1. `kube-system` and critical system namespaces are ignored.
2. DaemonSets are ignored (they are pinned to nodes).
3. Standalone unmanaged pods are ignored (they will not be recreated).
4. System components (`metrics-server`, `coredns`, `kube-proxy`) are excluded.
5. Eligible Deployment/ReplicaSet pods are ranked by CPU consumption descending.

---

## 10. Technical Comparison with References

### 10.1 Kubernetes Descheduler (`kubernetes-sigs/descheduler`)
- **Descheduler**: Evicts pods based on declared resource *requests*, node taints, or affinity violations.
- **ClusterBalancer**: Reacts dynamically to **live, real-time CPU consumption** measured by `metrics-server`, rebalancing workloads whose runtime behavior exceeds expected baselines.
- An optional Descheduler integration manifest is provided in `kubernetes/descheduler.yaml`.

### 10.2 Kubernetes Autoscaler (`kubernetes/autoscaler`)
- **Cluster Autoscaler**: Adds or removes physical/VM nodes when pods cannot be scheduled due to lack of capacity.
- **ClusterBalancer**: Balances workloads **within existing nodes**, maximizing cluster resource utilization and avoiding unnecessary infrastructure scaling costs.

---

## 11. Testing and Verification

Run the automated test suite covering unit calculations, decision rules, safety exclusions, and end-to-end rebalancing:

```powershell
python -m pytest tests/ -v
```

**Results:**
```text
============================= 14 passed in 2.98s ==============================
```

---

## 12. Troubleshooting

| Issue | Cause | Solution |
|---|---|---|
| `Metrics Server: UNAVAILABLE` | Metrics Server addon not yet active | Run `minikube addons enable metrics-server` and wait 60s |
| `Could not connect to cluster` | Minikube stopped or kubeconfig missing | Run `minikube start --nodes=3` |
| `Replacement pod scheduled on same node` | Scheduler selected same node | Ensure `prevent_immediate_rescheduling: true` is enabled in `config.yaml` |

---

## 13. Future Enhancements

- **Predictive AI/ML Rebalancing**: Forecast CPU spikes using ARIMA or LSTM models.
- **Network I/O and Disk Bandwidth Rebalancing**: Extend multi-resource balancing beyond CPU and RAM.
- **Cloud Provider Autoscaler Integration**: Bridge intra-cluster rebalancing with dynamic AWS EKS / GCP GKE node pool scaling.

---

## 14. References

1. Kubernetes Descheduler: https://github.com/kubernetes-sigs/descheduler
2. Kubernetes Autoscaler: https://github.com/kubernetes/autoscaler
3. Kubernetes Metrics Server: https://github.com/kubernetes-sigs/metrics-server
4. Official Kubernetes Python Client: https://github.com/kubernetes-client/python
