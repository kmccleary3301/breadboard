"""
LSP Deployment Configuration and Management
Handles scaling, health monitoring, and container orchestration for LSP servers.
"""

import os
import time
import json
from typing import Dict, List, Optional, Any
from pathlib import Path

import ray
from lsp_manager_v2 import LSPManagerV2, LSP_SERVER_CONFIGS
from sandbox_lsp_integration import LSPSandboxFactory, integrate_lsp_with_agent_session


class LSPClusterManager:
    """Manages LSP server deployment across Ray cluster"""
    
    def __init__(self, max_replicas_per_language: int = 3, container_image: str = "lsp-universal:latest"):
        self.max_replicas_per_language = max_replicas_per_language
        self.container_image = container_image
        self.lsp_pools: Dict[str, List[ray.ObjectRef]] = {}
        self.broken_servers: set = set()
        self.health_check_interval = 30  # seconds
        
    def deploy_lsp_cluster(self, workspace_roots: List[str]) -> Dict[str, Any]:
        """Deploy LSP servers across the Ray cluster"""
        deployment_status = {
            "deployed_languages": [],
            "failed_languages": [],
            "total_servers": 0,
            "workspace_roots": workspace_roots
        }
        
        for language_id, config in LSP_SERVER_CONFIGS.items():
            try:
                # Create multiple replicas for each language
                replicas = []
                for i in range(self.max_replicas_per_language):
                    for workspace_root in workspace_roots:
                        # Deploy server with resource constraints
                        server = self._deploy_single_server(language_id, workspace_root, i)
                        if server:
                            replicas.append(server)
                
                if replicas:
                    self.lsp_pools[language_id] = replicas
                    deployment_status["deployed_languages"].append(language_id)
                    deployment_status["total_servers"] += len(replicas)
                else:
                    deployment_status["failed_languages"].append(language_id)
                    
            except Exception as e:
                print(f"Failed to deploy {language_id}: {e}")
                deployment_status["failed_languages"].append(language_id)
        
        return deployment_status
    
    def _deploy_single_server(self, language_id: str, workspace_root: str, replica_id: int) -> Optional[ray.ObjectRef]:
        """Deploy a single LSP server instance with resource constraints"""
        try:
            # Resource constraints based on language server requirements
            resource_specs = self._get_resource_specs(language_id)
            
            # Create LSP manager with specific resources
            lsp_manager = LSPManagerV2.options(
                num_cpus=resource_specs["cpu"],
                memory=resource_specs["memory"],
                name=f"lsp-{language_id}-{replica_id}-{hash(workspace_root) % 1000}"
            ).remote()
            
            # Register workspace
            ray.get(lsp_manager.register_root.remote(workspace_root))
            
            return lsp_manager
            
        except Exception as e:
            print(f"Failed to deploy {language_id} server: {e}")
            return None
    
    def _get_resource_specs(self, language_id: str) -> Dict[str, float]:
        """Get resource specifications for different language servers"""
        resource_map = {
            "typescript": {"cpu": 1.0, "memory": 512 * 1024 * 1024},  # 512MB
            "python": {"cpu": 0.5, "memory": 256 * 1024 * 1024},      # 256MB
            "java": {"cpu": 2.0, "memory": 1024 * 1024 * 1024},       # 1GB
            "rust": {"cpu": 1.5, "memory": 512 * 1024 * 1024},        # 512MB
            "go": {"cpu": 0.5, "memory": 256 * 1024 * 1024},          # 256MB
            "cpp": {"cpu": 1.0, "memory": 512 * 1024 * 1024},         # 512MB
            "csharp": {"cpu": 1.0, "memory": 512 * 1024 * 1024},      # 512MB
            "ruby": {"cpu": 0.5, "memory": 256 * 1024 * 1024},        # 256MB
        }
        return resource_map.get(language_id, {"cpu": 0.5, "memory": 256 * 1024 * 1024})
    
    def get_lsp_server(self, language_id: str) -> Optional[ray.ObjectRef]:
        """Get an available LSP server for a language (load balancing)"""
        if language_id not in self.lsp_pools or not self.lsp_pools[language_id]:
            return None
            
        # Simple round-robin selection
        servers = self.lsp_pools[language_id]
        if not hasattr(self, '_server_indices'):
            self._server_indices = {}
        
        if language_id not in self._server_indices:
            self._server_indices[language_id] = 0
        
        index = self._server_indices[language_id]
        server = servers[index % len(servers)]
        self._server_indices[language_id] = (index + 1) % len(servers)
        
        return server
    
    def health_check(self) -> Dict[str, Any]:
        """Perform health check on all LSP servers"""
        health_status = {
            "timestamp": time.time(),
            "healthy_servers": 0,
            "unhealthy_servers": 0,
            "language_status": {}
        }
        
        for language_id, servers in self.lsp_pools.items():
            language_health = {"healthy": 0, "unhealthy": 0, "total": len(servers)}
            
            for server in servers:
                try:
                    # Simple health check - try to get diagnostics
                    ray.get(server.diagnostics.remote(), timeout=5)
                    language_health["healthy"] += 1
                    health_status["healthy_servers"] += 1
                except Exception:
                    language_health["unhealthy"] += 1
                    health_status["unhealthy_servers"] += 1
            
            health_status["language_status"][language_id] = language_health
        
        return health_status
    
    def scale_language_servers(self, language_id: str, target_replicas: int, workspace_roots: List[str]) -> bool:
        """Scale LSP servers for a specific language"""
        current_servers = self.lsp_pools.get(language_id, [])
        current_count = len(current_servers)
        
        if target_replicas > current_count:
            # Scale up
            for i in range(current_count, target_replicas):
                for workspace_root in workspace_roots:
                    server = self._deploy_single_server(language_id, workspace_root, i)
                    if server:
                        current_servers.append(server)
        
        elif target_replicas < current_count:
            # Scale down
            servers_to_remove = current_servers[target_replicas:]
            for server in servers_to_remove:
                try:
                    ray.get(server.shutdown.remote())
                except Exception:
                    pass
            current_servers = current_servers[:target_replicas]
        
        self.lsp_pools[language_id] = current_servers
        return True
    
    def shutdown_all(self):
        """Shutdown all LSP servers"""
        for language_id, servers in self.lsp_pools.items():
            print(f"Shutting down {len(servers)} {language_id} servers...")
            for server in servers:
                try:
                    ray.get(server.shutdown.remote())
                except Exception:
                    pass
        
        self.lsp_pools.clear()


@ray.remote
class LSPProxyRouter:
    """Routes LSP requests to appropriate server instances with load balancing"""
    
    def __init__(self, cluster_manager: LSPClusterManager):
        self.cluster_manager = cluster_manager
        self.request_counts = {}
    
    def route_diagnostics(self, file_path: str) -> Dict[str, List[Dict[str, Any]]]:
        """Route diagnostics request to appropriate LSP servers"""
        extension = Path(file_path).suffix
        
        # Find matching language servers
        for language_id, config in LSP_SERVER_CONFIGS.items():
            if extension in config["extensions"]:
                server = self.cluster_manager.get_lsp_server(language_id)
                if server:
                    try:
                        ray.get(server.touch_file.remote(file_path))
                        return ray.get(server.diagnostics.remote())
                    except Exception as e:
                        print(f"Error routing diagnostics for {language_id}: {e}")
                        continue
        
        return {}
    
    def route_hover(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Route hover request to appropriate LSP server"""
        extension = Path(file_path).suffix
        
        for language_id, config in LSP_SERVER_CONFIGS.items():
            if extension in config["extensions"]:
                server = self.cluster_manager.get_lsp_server(language_id)
                if server:
                    try:
                        return ray.get(server.hover.remote(file_path, line, character))
                    except Exception as e:
                        print(f"Error routing hover for {language_id}: {e}")
                        continue
        
        return {}


class LSPDeploymentConfig:
    """Configuration management for LSP deployments"""
    
    @staticmethod
    def create_development_config() -> Dict[str, Any]:
        """Create development deployment configuration"""
        return {
            "cluster": {
                "max_replicas_per_language": 1,
                "container_image": "lsp-universal:dev",
                "enable_containers": False,  # Use local processes for development
                "health_check_interval": 60
            },
            "languages": {
                "enabled": ["python", "typescript", "go"],
                "disabled": ["java", "rust", "cpp"],  # Disable heavy servers for development
            },
            "resources": {
                "default_cpu": 0.25,
                "default_memory": 128 * 1024 * 1024,  # 128MB
                "enable_resource_limits": False
            },
            "workspace": {
                "auto_detect_roots": True,
                "default_roots": ["."],
                "ignore_patterns": ["node_modules", "target", "build", ".git"]
            }
        }
    
    @staticmethod
    def create_production_config() -> Dict[str, Any]:
        """Create production deployment configuration"""
        return {
            "cluster": {
                "max_replicas_per_language": 3,
                "container_image": "lsp-universal:latest",
                "enable_containers": True,
                "health_check_interval": 30
            },
            "languages": {
                "enabled": ["python", "typescript", "go", "rust", "cpp", "java"],
                "disabled": [],
            },
            "resources": {
                "default_cpu": 1.0,
                "default_memory": 512 * 1024 * 1024,  # 512MB
                "enable_resource_limits": True
            },
            "workspace": {
                "auto_detect_roots": True,
                "default_roots": ["/workspace"],
                "ignore_patterns": ["node_modules", "target", "build", ".git", "__pycache__"]
            }
        }
    
    @staticmethod
    def save_config(config: Dict[str, Any], config_path: str):
        """Save deployment configuration to file"""
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
    
    @staticmethod
    def load_config(config_path: str) -> Dict[str, Any]:
        """Load deployment configuration from file"""
        with open(config_path, 'r') as f:
            return json.load(f)


def deploy_lsp_system(config: Dict[str, Any], workspace_roots: List[str]) -> LSPClusterManager:
    """Deploy complete LSP system with given configuration"""
    
    print("Deploying LSP system...")
    
    # Set environment variables for container usage
    cluster_config = config.get("cluster", {})
    if cluster_config.get("enable_containers", False):
        os.environ["LSP_USE_CONTAINERS"] = "1"
    else:
        os.environ["LSP_USE_CONTAINERS"] = "0"
    
    # Create cluster manager with defaults for missing config
    cluster_manager = LSPClusterManager(
        max_replicas_per_language=cluster_config.get("max_replicas_per_language", 1),
        container_image=cluster_config.get("container_image", "lsp-universal:latest")
    )
    
    # Deploy the cluster
    deployment_status = cluster_manager.deploy_lsp_cluster(workspace_roots)
    
    print(f"Deployment complete:")
    print(f"  - Successfully deployed: {', '.join(deployment_status['deployed_languages'])}")
    print(f"  - Failed deployments: {', '.join(deployment_status['failed_languages'])}")
    print(f"  - Total servers: {deployment_status['total_servers']}")
    
    return cluster_manager


# Example usage function
def example_usage():
    """Example of how to use the LSP system"""
    
    # Initialize Ray
    if not ray.is_initialized():
        ray.init()
    
    try:
        # Create development configuration
        config = LSPDeploymentConfig.create_development_config()
        
        # Deploy LSP system
        cluster_manager = deploy_lsp_system(config, [os.getcwd()])
        
        # Create enhanced sandbox using the factory
        enhanced_sandbox = LSPSandboxFactory.create_enhanced_sandbox(
            image="python-dev:latest",
            workspace=os.getcwd()
        )
        
        # Example: Use LSP features
        print("Testing LSP features...")
        
        # Test diagnostics
        diagnostics = ray.get(enhanced_sandbox.lsp_diagnostics.remote("lsp_manager_v2.py"))
        print(f"Found {len(diagnostics)} diagnostic sources")
        
        # Test workspace symbols
        symbols = ray.get(enhanced_sandbox.lsp_workspace_symbols.remote("LSP"))
        print(f"Found {len(symbols)} symbols matching 'LSP'")
        
        # Health check
        health = cluster_manager.health_check()
        print(f"Cluster health: {health['healthy_servers']} healthy, {health['unhealthy_servers']} unhealthy")
        
    finally:
        # Cleanup
        if 'cluster_manager' in locals():
            cluster_manager.shutdown_all()


if __name__ == "__main__":
    example_usage()