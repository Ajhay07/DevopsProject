"""
Cluster Manager module for interacting with the Kubernetes API.
Handles cluster authentication, node/pod listing, Metrics Server checks,
Eviction API calls, and safe node cordon/uncordon operations.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger("clusterbalancer.cluster_manager")


class ClusterManager:
    """Manages connections and operations on a Kubernetes cluster."""

    def __init__(self, kubeconfig_path: Optional[str] = None, context: Optional[str] = None):
        self.kubeconfig_path = kubeconfig_path
        self.context = context
        self.v1: Optional[client.CoreV1Api] = None
        self.apps_v1: Optional[client.AppsV1Api] = None
        self.policy_v1: Optional[client.PolicyV1Api] = None
        self.custom_api: Optional[client.CustomObjectsApi] = None
        self._connected = False

    def connect(self) -> bool:
        """Attempts to authenticate with the Kubernetes cluster."""
        try:
            # Try in-cluster first (e.g. if running as a pod)
            try:
                config.load_incluster_config()
                logger.info("Loaded in-cluster Kubernetes configuration.")
            except config.ConfigException:
                # Fall back to kubeconfig
                config.load_kube_config(config_file=self.kubeconfig_path, context=self.context)
                logger.info("Loaded local kubeconfig successfully.")

            self.v1 = client.CoreV1Api()
            self.apps_v1 = client.AppsV1Api()
            self.policy_v1 = client.PolicyV1Api()
            self.custom_api = client.CustomObjectsApi()
            self._connected = True
            return True
        except Exception as e:
            logger.error("Failed to connect to Kubernetes cluster: %s", e)
            self._connected = False
            return False

    def is_connected(self) -> bool:
        return self._connected

    def verify_connectivity(self) -> bool:
        """Verifies cluster connectivity by pinging the API server."""
        if not self._connected or self.v1 is None:
            return False
        try:
            self.v1.get_api_resources()
            return True
        except Exception as e:
            logger.warning("Cluster connectivity verification failed: %s", e)
            return False

    def is_metrics_server_available(self) -> bool:
        """Checks if the metrics.k8s.io API is reachable and responding."""
        if not self._connected or self.custom_api is None:
            return False
        try:
            # List node metrics via custom objects api
            self.custom_api.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
            )
            return True
        except ApiException as e:
            logger.debug("Metrics Server API check failed with status %s: %s", e.status, e.reason)
            return False
        except Exception as e:
            logger.debug("Metrics Server API check failed: %s", e)
            return False

    def get_nodes(self) -> List[Dict[str, Any]]:
        """Retrieves raw node information including allocatable capacity and status."""
        if not self._connected or self.v1 is None:
            return []
        try:
            node_list = self.v1.list_node()
            nodes_data = []
            for item in node_list.items:
                name = item.metadata.name
                labels = item.metadata.labels or {}

                # Check if Ready
                status = "NotReady"
                for condition in (item.status.conditions or []):
                    if condition.type == "Ready" and condition.status == "True":
                        status = "Ready"
                        break

                # Extract roles
                roles = []
                for label in labels:
                    if label.startswith("node-role.kubernetes.io/"):
                        roles.append(label.split("/")[1])
                if not roles and "node.kubernetes.io/role" in labels:
                    roles.append(labels["node.kubernetes.io/role"])

                allocatable = item.status.allocatable or {}
                unschedulable = bool(item.spec.unschedulable)

                nodes_data.append({
                    "name": name,
                    "status": status,
                    "roles": roles,
                    "allocatable_cpu": allocatable.get("cpu", "0"),
                    "allocatable_memory": allocatable.get("memory", "0"),
                    "unschedulable": unschedulable,
                    "taints": [t.to_dict() for t in (item.spec.taints or [])],
                    "labels": labels,
                })
            return nodes_data
        except ApiException as e:
            logger.error("Failed to list nodes: %s", e)
            return []

    def get_node_metrics(self) -> Dict[str, Dict[str, str]]:
        """Retrieves live node metrics from metrics.k8s.io."""
        if not self._connected or self.custom_api is None:
            return {}
        try:
            data = self.custom_api.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
            )
            metrics_map = {}
            for item in data.get("items", []):
                name = item.get("metadata", {}).get("name")
                usage = item.get("usage", {})
                if name:
                    metrics_map[name] = {
                        "cpu": usage.get("cpu", "0"),
                        "memory": usage.get("memory", "0"),
                    }
            return metrics_map
        except Exception as e:
            logger.warning("Could not retrieve node metrics: %s", e)
            return {}

    def get_pods(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves pods with owner references, status, and node mapping."""
        if not self._connected or self.v1 is None:
            return []
        try:
            if namespace:
                pod_list = self.v1.list_namespaced_pod(namespace=namespace)
            else:
                pod_list = self.v1.list_pod_for_all_namespaces()

            pods_data = []
            for item in pod_list.items:
                name = item.metadata.name
                ns = item.metadata.namespace
                node_name = item.spec.node_name or "Unassigned"
                phase = item.status.phase or "Unknown"

                # Extract controller/owner reference
                controller_kind = None
                controller_name = None
                if item.metadata.owner_references:
                    for owner in item.metadata.owner_references:
                        if owner.controller:
                            controller_kind = owner.kind
                            controller_name = owner.name
                            break
                    if not controller_kind and item.metadata.owner_references:
                        controller_kind = item.metadata.owner_references[0].kind
                        controller_name = item.metadata.owner_references[0].name

                # Extract container requests and limits
                total_req_cpu = 0
                total_req_mem = 0
                for c in (item.spec.containers or []):
                    res = c.resources
                    if res and res.requests:
                        total_req_cpu += 1
                    if res and res.limits:
                        total_req_mem += 1

                pods_data.append({
                    "name": name,
                    "namespace": ns,
                    "node_name": node_name,
                    "phase": phase,
                    "controller_kind": controller_kind,
                    "controller_name": controller_name,
                    "labels": item.metadata.labels or {},
                })
            return pods_data
        except ApiException as e:
            logger.error("Failed to list pods: %s", e)
            return []

    def get_pod_metrics(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves live pod metrics from metrics.k8s.io."""
        if not self._connected or self.custom_api is None:
            return []
        try:
            if namespace:
                data = self.custom_api.list_namespaced_custom_object(
                    group="metrics.k8s.io",
                    version="v1beta1",
                    namespace=namespace,
                    plural="pods",
                )
            else:
                data = self.custom_api.list_cluster_custom_object(
                    group="metrics.k8s.io",
                    version="v1beta1",
                    plural="pods",
                )

            pod_metrics_list = []
            for item in data.get("items", []):
                name = item.get("metadata", {}).get("name")
                ns = item.get("metadata", {}).get("namespace")
                containers = item.get("containers", [])
                cpu_total = 0
                mem_total = 0
                # Containers inside pod
                pod_metrics_list.append({
                    "name": name,
                    "namespace": ns,
                    "containers": containers,
                })
            return pod_metrics_list
        except Exception as e:
            logger.debug("Could not retrieve pod metrics: %s", e)
            return []

    def evict_pod(self, name: str, namespace: str) -> bool:
        """
        Evicts a pod using the official Kubernetes Eviction API.
        Falls back to graceful deletion if Eviction API is unavailable.
        """
        if not self._connected or self.v1 is None:
            return False

        logger.info("Initiating eviction for pod %s in namespace %s", name, namespace)
        eviction_body = client.V1Eviction(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            delete_options=client.V1DeleteOptions(grace_period_seconds=30),
        )

        try:
            self.v1.create_namespaced_pod_eviction(
                name=name,
                namespace=namespace,
                body=eviction_body,
            )
            logger.info("Eviction API call succeeded for pod %s/%s", namespace, name)
            return True
        except ApiException as e:
            if e.status == 429:
                logger.warning("Eviction rejected due to PodDisruptionBudget for pod %s/%s", namespace, name)
                return False
            logger.warning(
                "Eviction API failed (%s: %s). Falling back to controlled deletion.",
                e.status,
                e.reason,
            )
            try:
                self.v1.delete_namespaced_pod(
                    name=name,
                    namespace=namespace,
                    grace_period_seconds=30,
                )
                logger.info("Controlled deletion succeeded for pod %s/%s", namespace, name)
                return True
            except ApiException as del_err:
                logger.error("Failed to delete pod %s/%s: %s", namespace, name, del_err)
                return False

    def cordon_node(self, node_name: str) -> bool:
        """Marks a node as unschedulable (cordoned) to prevent new pods from landing on it."""
        if not self._connected or self.v1 is None:
            return False
        try:
            body = {"spec": {"unschedulable": True}}
            self.v1.patch_node(name=node_name, body=body)
            logger.info("Node %s successfully cordoned (unschedulable=True)", node_name)
            return True
        except ApiException as e:
            logger.error("Failed to cordon node %s: %s", node_name, e)
            return False

    def uncordon_node(self, node_name: str) -> bool:
        """Marks a node as schedulable (uncordoned)."""
        if not self._connected or self.v1 is None:
            return False
        try:
            body = {"spec": {"unschedulable": False}}
            self.v1.patch_node(name=node_name, body=body)
            logger.info("Node %s successfully uncordoned (unschedulable=False)", node_name)
            return True
        except ApiException as e:
            logger.error("Failed to uncordon node %s: %s", node_name, e)
            return False
