# ClusterBalancer Demonstration & Verification Guide

This guide provides step-by-step instructions for running, testing, and demonstrating the **CPU-Aware Cluster Load Balancing System (ClusterBalancer)**.

---

## 1. Prerequisites Installation (Windows)

If not already installed on your machine, install the prerequisites using `winget`:

```powershell
# 1. Install Docker Desktop
winget install -e --id Docker.DockerDesktop

# 2. Install kubectl
winget install -e --id Kubernetes.kubectl

# 3. Install Minikube
winget install -e --id Kubernetes.minikube

# 4. Install Git (if needed)
winget install -e --id Git.Git
```

> **Note:** After installing Docker Desktop, launch it and ensure the Linux container engine is running.

---

## 2. Multi-Node Local Kubernetes Setup

### Step 2.1 — Start a 3-Node Minikube Cluster
Open PowerShell as Administrator (or standard PowerShell if Docker permissions allow) and run:

```powershell
minikube start --nodes=3 --driver=docker --cpus=2 --memory=2048
```

Verify that all three nodes are online and Ready:
```powershell
kubectl get nodes -o wide
```

Expected output:
```text
NAME           STATUS   ROLES           AGE   VERSION   INTERNAL-IP   OS-IMAGE             KERNEL-VERSION
minikube       Ready    control-plane   2m    v1.31.0   192.168.49.2  Ubuntu 22.04.4 LTS   ...
minikube-m02   Ready    <none>          1m    v1.31.0   192.168.49.3  Ubuntu 22.04.4 LTS   ...
minikube-m03   Ready    <none>          1m    v1.31.0   192.168.49.4  Ubuntu 22.04.4 LTS   ...
```

### Step 2.2 — Enable the Kubernetes Metrics Server
```powershell
minikube addons enable metrics-server
```

Verify that the metrics server is collecting data (allow 30-60 seconds to initialize):
```powershell
kubectl top nodes
```

Expected output:
```text
NAME           CPU(cores)   CPU%   MEMORY(bytes)   MEMORY%
minikube       210m         10%    950Mi           46%
minikube-m02   85m          4%     420Mi           20%
minikube-m03   75m          3%     410Mi           20%
```

---

## 3. Workload Deployment & Stress Simulation

### Step 3.1 — Deploy the CPU-Stress Workload
Deploy the 6-replica CPU-intensive application:
```powershell
kubectl apply -f deployment/workload.yaml
```

Check the initial distribution across the worker nodes:
```powershell
kubectl get pods -o wide -l app=cpu-stress
```

Allow approximately 60 seconds for the containers to run the `stress` utility. Then check node resource consumption:
```powershell
kubectl top nodes
```

---

## 4. Running ClusterBalancer

Navigate to the project directory:
```powershell
Set-Location "C:\Users\Ajhay\DevopsProject"
```

### Option A: Live Cluster Execution

#### 1. Recommendation Mode (Default & Safe)
```powershell
python main.py --mode recommend --continuous --interval 10
```
- Monitors real-time node CPU and RAM.
- Detects overloaded nodes ($\ge 75\%$) and underutilized nodes ($\le 30\%$).
- Performs consecutive sample validation (2 cycles) to prevent flapping.
- Outputs detailed recommendations in the terminal without modifying cluster resources.

#### 2. Automatic Mode (Controlled Rebalancing)
```powershell
python main.py --mode automatic --continuous --interval 10
```
- Identifies eligible Deployment pods on the overloaded node.
- Applies a temporary cordon to the overloaded source node.
- Evicts the pod using the Kubernetes Eviction API.
- Verifies that the replacement pod is placed onto an underutilized target node.
- Releases the cordon on the source node.
- Enters a 60-second cooldown period.

---

### Option B: Immediate Simulated Benchmark Demonstration

To demonstrate the full detection, recommendation, eviction, and rescheduling lifecycle instantly (without waiting for Minikube startup):

```powershell
# Run single cycle in recommendation mode
python main.py --simulate --mode recommend --once

# Run continuous simulation demonstrating multi-cycle validation and automatic rebalancing
python main.py --simulate --mode automatic --interval 5
```

---

## 5. Automated Testing & Verification

Run the complete test suite:

```powershell
Set-Location "C:\Users\Ajhay\DevopsProject"
python -m pytest tests/ -v
```

Expected output:
```text
tests/test_decision.py::test_balanced_cluster PASSED
tests/test_decision.py::test_overloaded_and_underutilized_classification PASSED
tests/test_decision.py::test_cooldown_mechanism PASSED
tests/test_decision.py::test_insufficient_nodes PASSED
tests/test_metrics.py::test_parse_cpu_formats PASSED
tests/test_metrics.py::test_parse_cpu_invalid PASSED
tests/test_metrics.py::test_parse_memory_formats PASSED
tests/test_metrics.py::test_node_metrics_calculations PASSED
tests/test_metrics.py::test_cluster_metrics_summary PASSED
tests/test_monitor.py::test_collect_metrics_aggregation PASSED
tests/test_rebalancer.py::test_find_eligible_pods_filters PASSED
tests/test_rebalancer.py::test_recommendation_mode PASSED
tests/test_rebalancer.py::test_automatic_mode_eviction_and_safeguards PASSED
tests/test_simulation.py::test_end_to_end_rebalance_flow PASSED

============================= 14 passed in 2.98s ==============================
```

---

## 6. Inspecting Telemetry and Audit Logs

- **Application Logs**: Check `logs/clusterbalancer.log` for timestamped eviction and decision events:
  ```powershell
  Get-Content logs/clusterbalancer.log -Tail 30
  ```
- **Historical Metrics CSV**: Check `logs/metrics_history.csv` for recorded node utilization over time:
  ```powershell
  Get-Content logs/metrics_history.csv | Select-Object -First 10
  ```
