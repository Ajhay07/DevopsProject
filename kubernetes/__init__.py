"""
Package namespace integration.
Allows importing both official Kubernetes library submodules (client, config, etc.)
and local cluster_manager from this directory.
"""
import os
import sys
import pkgutil

# Extend path so both site-packages kubernetes and local kubernetes/ are accessible
__path__ = pkgutil.extend_path(__path__, __name__)

# Expose ClusterManager
from .cluster_manager import ClusterManager

__all__ = ["ClusterManager"]
