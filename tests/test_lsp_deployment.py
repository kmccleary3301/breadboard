import os
import uuid
import pytest
import ray
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from breadboard.lsp_deployment import (
    LSPClusterManager,
    LSPDeploymentConfig,
    LSPProxyRouter,
    deploy_lsp_system,
    example_usage
)
from breadboard.lsp_manager_v2 import LSP_SERVER_CONFIGS


@pytest.fixture(scope="module")
def ray_cluster():
    """Initialize Ray cluster for testing"""
    if not ray.is_initialized():
        ray.init()
    yield
    if ray.is_initialized():
        ray.shutdown()


@pytest.fixture
def test_workspace(tmp_path):
    """Create a test workspace for deployment testing"""
    workspace = tmp_path / f"deployment_test_{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=True)
    
    # Create sample files for different languages
    test_files = {
        "app.py": '''
#!/usr/bin/env python3
"""Sample Python application."""

def main():
    print("Hello from Python!")
    return 0

if __name__ == "__main__":
    main()
''',
        "server.ts": '''
import express from 'express';

const app = express();
const PORT = process.env.PORT || 3000;

app.get('/', (req, res) => {
    res.send('Hello from TypeScript!');
});

app.listen(PORT, () => {
    console.log(`Server running on port ${PORT}`);
});
''',
        "main.go": '''
package main

import "fmt"

func main() {
    fmt.Println("Hello from Go!")
}
''',
        "lib.rs": '''
fn main() {
    println!("Hello from Rust!");
}

#[cfg(test)]
mod tests {
    #[test]
    fn test_hello() {
        assert_eq!("Hello from Rust!", "Hello from Rust!");
    }
}
'''
    }
    
    for filename, content in test_files.items():
        (workspace / filename).write_text(content)
    
    # Create configuration files
    configs = {
        "package.json": {
            "name": "deployment-test",
            "version": "1.0.0",
            "dependencies": {"express": "^4.18.0"},
            "devDependencies": {"typescript": "^5.0.0", "@types/node": "^18.0.0"}
        },
        "go.mod": "module deployment-test\n\ngo 1.20\n",
        "Cargo.toml": '''[package]
name = "deployment-test"
version = "0.1.0"
edition = "2021"
''',
        "pyproject.toml": '''[build-system]
requires = ["setuptools", "wheel"]

[tool.pyright]
typeCheckingMode = "basic"
'''
    }
    
    (workspace / "package.json").write_text(json.dumps(configs["package.json"], indent=2))
    (workspace / "go.mod").write_text(configs["go.mod"])
    (workspace / "Cargo.toml").write_text(configs["Cargo.toml"])
    (workspace / "pyproject.toml").write_text(configs["pyproject.toml"])
    
    return workspace


class TestLSPDeploymentConfig:
    """Test deployment configuration management"""
    
    def test_development_config_structure(self):
        """Test development configuration structure"""
        config = LSPDeploymentConfig.create_development_config()
        
        # Verify required sections
        assert "cluster" in config
        assert "languages" in config
        assert "resources" in config
        assert "workspace" in config
        
        # Verify cluster settings
        cluster_config = config["cluster"]
        assert "max_replicas_per_language" in cluster_config
        assert "container_image" in cluster_config
        assert "enable_containers" in cluster_config
        assert "health_check_interval" in cluster_config
        
        # Verify development-specific settings
        assert cluster_config["max_replicas_per_language"] <= 2  # Conservative for dev
        assert cluster_config["enable_containers"] is False  # Local processes for dev
        
        # Verify language settings
        lang_config = config["languages"]
        assert "enabled" in lang_config
        assert "disabled" in lang_config
        assert len(lang_config["enabled"]) >= 1  # Should have at least one language
    
    def test_production_config_structure(self):
        """Test production configuration structure"""
        config = LSPDeploymentConfig.create_production_config()
        
        # Verify production-specific settings
        cluster_config = config["cluster"]
        assert cluster_config["max_replicas_per_language"] >= 3  # More replicas for prod
        assert cluster_config["enable_containers"] is True  # Use containers in prod
        
        # Verify resource settings
        resources_config = config["resources"]
        assert "enable_resource_limits" in resources_config
        assert resources_config["enable_resource_limits"] is True  # Enforce limits in prod
        
        # Verify more languages enabled in production
        lang_config = config["languages"]
        assert len(lang_config["enabled"]) >= len(LSPDeploymentConfig.create_development_config()["languages"]["enabled"])
    
    def test_config_save_and_load(self, tmp_path):
        """Test configuration save and load functionality"""
        config = LSPDeploymentConfig.create_development_config()
        config_file = tmp_path / "test_config.json"
        
        # Save configuration
        LSPDeploymentConfig.save_config(config, str(config_file))
        assert config_file.exists()
        
        # Load configuration
        loaded_config = LSPDeploymentConfig.load_config(str(config_file))
        
        # Verify loaded config matches original
        assert loaded_config == config
    
    def test_config_customization(self):
        """Test configuration customization"""
        config = LSPDeploymentConfig.create_development_config()
        
        # Customize settings
        config["cluster"]["max_replicas_per_language"] = 5
        config["languages"]["enabled"].append("custom-language")
        config["resources"]["custom_setting"] = "custom_value"
        
        # Verify customizations are preserved
        assert config["cluster"]["max_replicas_per_language"] == 5
        assert "custom-language" in config["languages"]["enabled"]
        assert config["resources"]["custom_setting"] == "custom_value"


class TestLSPClusterManager:
    """Test LSP cluster management functionality"""
    
    @pytest.fixture
    def cluster_manager(self):
        """Create cluster manager for testing"""
        return LSPClusterManager(max_replicas_per_language=2, container_image="lsp-universal:test")
    
    def test_cluster_manager_initialization(self, cluster_manager):
        """Test cluster manager initialization"""
        assert cluster_manager.max_replicas_per_language == 2
        assert cluster_manager.container_image == "lsp-universal:test"
        assert isinstance(cluster_manager.lsp_pools, dict)
        assert len(cluster_manager.lsp_pools) == 0  # No servers deployed initially
        assert isinstance(cluster_manager.broken_servers, set)
    
    def test_resource_specifications(self, cluster_manager):
        """Test resource specification logic"""
        # Test known languages
        ts_resources = cluster_manager._get_resource_specs("typescript")
        assert "cpu" in ts_resources
        assert "memory" in ts_resources
        assert ts_resources["cpu"] > 0
        assert ts_resources["memory"] > 0
        
        python_resources = cluster_manager._get_resource_specs("python")
        assert python_resources["cpu"] > 0
        assert python_resources["memory"] > 0
        
        # Test unknown language defaults
        unknown_resources = cluster_manager._get_resource_specs("unknown-language")
        assert "cpu" in unknown_resources
        assert "memory" in unknown_resources
    
    def test_single_server_deployment(self, ray_cluster, cluster_manager, test_workspace):
        """Test deployment of a single server"""
        # Test deployment with real LSPManagerV2 (will fail gracefully if no language servers available)
        server = cluster_manager._deploy_single_server("python", str(test_workspace), 0)
        
        # Server could be None if language server requirements aren't met, but method should not crash
        assert server is None or hasattr(server, '__ray_ready__')  # Either None or a Ray actor
    
    def test_health_check_empty_cluster(self, cluster_manager):
        """Test health check on empty cluster"""
        health_status = cluster_manager.health_check()
        
        assert "timestamp" in health_status
        assert "healthy_servers" in health_status
        assert "unhealthy_servers" in health_status
        assert "language_status" in health_status
        
        assert health_status["healthy_servers"] == 0
        assert health_status["unhealthy_servers"] == 0
        assert isinstance(health_status["language_status"], dict)
    
    @patch('lsp_deployment.LSPManagerV2')
    def test_cluster_deployment(self, mock_lsp_manager, ray_cluster, cluster_manager, test_workspace):
        """Test full cluster deployment"""
        # Mock successful LSP manager creation
        mock_server = Mock()
        mock_lsp_manager.options.return_value.remote.return_value = mock_server
        
        # Mock register_root to succeed
        mock_server.register_root.remote.return_value = ray.put(None)
        
        # Deploy cluster with limited languages for testing
        original_configs = LSP_SERVER_CONFIGS.copy()
        test_configs = {
            "python": original_configs["python"],
            "typescript": original_configs["typescript"]
        }
        
        with patch.dict(LSP_SERVER_CONFIGS, test_configs, clear=True):
            deployment_status = cluster_manager.deploy_lsp_cluster([str(test_workspace)])
        
        # Verify deployment status structure
        assert "deployed_languages" in deployment_status
        assert "failed_languages" in deployment_status
        assert "total_servers" in deployment_status
        assert "workspace_roots" in deployment_status
        
        assert isinstance(deployment_status["deployed_languages"], list)
        assert isinstance(deployment_status["failed_languages"], list)
        assert isinstance(deployment_status["total_servers"], int)
    
    def test_server_load_balancing(self, cluster_manager):
        """Test server load balancing logic"""
        # Create mock servers
        mock_servers = [Mock() for _ in range(3)]
        cluster_manager.lsp_pools["python"] = mock_servers
        
        # Get servers multiple times
        selected_servers = []
        for _ in range(9):  # 3x the number of servers
            server = cluster_manager.get_lsp_server("python")
            selected_servers.append(server)
        
        # Should distribute requests across all servers
        assert len(set(id(s) for s in selected_servers)) == 3
        
        # Test with non-existent language
        no_server = cluster_manager.get_lsp_server("nonexistent")
        assert no_server is None
    
    @patch('lsp_deployment.LSPManagerV2')
    def test_server_scaling(self, mock_lsp_manager, cluster_manager, test_workspace):
        """Test server scaling functionality"""
        # Mock LSP manager creation
        mock_server = Mock()
        mock_lsp_manager.options.return_value.remote.return_value = mock_server
        mock_server.register_root.remote.return_value = ray.put(None)
        
        # Initial deployment
        cluster_manager.lsp_pools["python"] = [mock_server]
        
        # Scale up
        success = cluster_manager.scale_language_servers("python", 3, [str(test_workspace)])
        assert success is True
        
        # Scale down
        success = cluster_manager.scale_language_servers("python", 1, [str(test_workspace)])
        assert success is True
    
    def test_cluster_shutdown(self, cluster_manager):
        """Test cluster shutdown functionality"""
        # Create mock servers with shutdown methods
        mock_servers = []
        for _ in range(3):
            mock_server = Mock()
            mock_server.shutdown.remote.return_value = ray.put(None)
            mock_servers.append(mock_server)
        
        cluster_manager.lsp_pools["python"] = mock_servers
        cluster_manager.lsp_pools["typescript"] = mock_servers.copy()
        
        # Shutdown cluster
        cluster_manager.shutdown_all()
        
        # Verify all servers were shut down
        for server in mock_servers:
            server.shutdown.remote.assert_called()
        
        # Verify pools are cleared
        assert len(cluster_manager.lsp_pools) == 0


class TestLSPProxyRouter:
    """Test LSP proxy routing functionality"""
    
    @pytest.fixture
    def proxy_router(self, ray_cluster):
        """Create proxy router for testing"""
        cluster_manager = LSPClusterManager()
        return LSPProxyRouter.remote(cluster_manager)
    
    def test_router_initialization(self, ray_cluster):
        """Test proxy router initialization"""
        cluster_manager = LSPClusterManager()
        router = LSPProxyRouter.remote(cluster_manager)
        
        # Should be created without errors
        assert router is not None
    
    @patch('lsp_deployment.LSPClusterManager.get_lsp_server')
    def test_diagnostics_routing(self, mock_get_server, proxy_router, test_workspace):
        """Test diagnostics routing logic"""
        # Mock server with diagnostics capability
        mock_server = Mock()
        mock_server.touch_file.remote.return_value = ray.put(None)
        mock_server.diagnostics.remote.return_value = ray.put({"test.py": []})
        mock_get_server.return_value = mock_server
        
        test_file = test_workspace / "test.py"
        
        # Route diagnostics request
        result = ray.get(proxy_router.route_diagnostics.remote(str(test_file)))
        
        assert isinstance(result, dict)
    
    @patch('lsp_deployment.LSPClusterManager.get_lsp_server') 
    def test_hover_routing(self, mock_get_server, proxy_router, test_workspace):
        """Test hover request routing"""
        # Mock server with hover capability
        mock_server = Mock()
        mock_server.hover.remote.return_value = ray.put({"contents": "test hover"})
        mock_get_server.return_value = mock_server
        
        test_file = test_workspace / "test.py"
        
        # Route hover request
        result = ray.get(proxy_router.route_hover.remote(str(test_file), 1, 5))
        
        assert isinstance(result, dict)
    
    def test_routing_with_no_servers(self, proxy_router, test_workspace):
        """Test routing behavior when no servers are available"""
        test_file = test_workspace / "test.py"
        
        # Should handle gracefully when no servers available
        diagnostics_result = ray.get(proxy_router.route_diagnostics.remote(str(test_file)))
        assert isinstance(diagnostics_result, dict)
        
        hover_result = ray.get(proxy_router.route_hover.remote(str(test_file), 1, 5))
        assert isinstance(hover_result, dict)


class TestDeploymentIntegration:
    """Integration tests for deployment system"""
    
    def test_deploy_lsp_system_development(self, ray_cluster, test_workspace):
        """Test development deployment"""
        config = LSPDeploymentConfig.create_development_config()
        
        # Limit languages for faster testing
        config["languages"]["enabled"] = ["python"]
        config["languages"]["disabled"] = list(set(LSP_SERVER_CONFIGS.keys()) - {"python"})
        
        # Deploy system
        cluster_manager = deploy_lsp_system(config, [str(test_workspace)])
        
        assert isinstance(cluster_manager, LSPClusterManager)
        
        # Test health check
        health = cluster_manager.health_check()
        assert isinstance(health, dict)
        
        # Cleanup
        cluster_manager.shutdown_all()
    
    def test_deploy_lsp_system_with_invalid_config(self, ray_cluster, test_workspace):
        """Test deployment with invalid configuration"""
        # Create invalid config
        invalid_config = {
            "cluster": {"max_replicas_per_language": -1},  # Invalid negative value
            "languages": {"enabled": []},  # No languages enabled
            "resources": {},
            "workspace": {}
        }
        
        # Should handle gracefully
        cluster_manager = deploy_lsp_system(invalid_config, [str(test_workspace)])
        assert isinstance(cluster_manager, LSPClusterManager)
        
        # Cleanup
        cluster_manager.shutdown_all()
    
    def test_deploy_with_multiple_workspaces(self, ray_cluster, tmp_path):
        """Test deployment with multiple workspace roots"""
        # Create multiple workspaces
        workspaces = []
        for i in range(3):
            workspace = tmp_path / f"workspace_{i}"
            workspace.mkdir()
            (workspace / "test.py").write_text(f"def function_{i}(): pass")
            workspaces.append(str(workspace))
        
        config = LSPDeploymentConfig.create_development_config()
        config["languages"]["enabled"] = ["python"]
        
        # Deploy to multiple workspaces
        cluster_manager = deploy_lsp_system(config, workspaces)
        
        assert isinstance(cluster_manager, LSPClusterManager)
        
        # Cleanup
        cluster_manager.shutdown_all()


class TestEnvironmentIntegration:
    """Test integration with different environments"""
    
    def test_container_environment_variables(self):
        """Test container environment variable handling"""
        # Test with containers enabled
        os.environ["LSP_USE_CONTAINERS"] = "1"
        config = LSPDeploymentConfig.create_production_config()
        
        # Should respect container setting
        assert config["cluster"]["enable_containers"] is True
        
        # Test with containers disabled
        os.environ["LSP_USE_CONTAINERS"] = "0"
        config = LSPDeploymentConfig.create_development_config()
        
        # Should respect local process setting
        assert config["cluster"]["enable_containers"] is False
        
        # Cleanup environment
        os.environ.pop("LSP_USE_CONTAINERS", None)
    
    def test_resource_limit_environment(self):
        """Test resource limit configuration"""
        # Test with resource limits enabled
        config = LSPDeploymentConfig.create_production_config()
        assert config["resources"]["enable_resource_limits"] is True
        
        # Test with resource limits disabled
        config = LSPDeploymentConfig.create_development_config()
        assert config["resources"]["enable_resource_limits"] is False
    
    @patch('subprocess.run')
    def test_docker_availability_detection(self, mock_subprocess):
        """Test Docker availability detection"""
        # Mock Docker available
        mock_subprocess.return_value.returncode = 0
        
        cluster_manager = LSPClusterManager(container_image="test:latest")
        assert cluster_manager.container_image == "test:latest"
        
        # Mock Docker unavailable  
        mock_subprocess.return_value.returncode = 1
        
        # Should still create cluster manager
        cluster_manager = LSPClusterManager()
        assert cluster_manager is not None


class TestFailureScenarios:
    """Test failure scenarios and recovery"""
    
    def test_server_startup_failure_handling(self, ray_cluster, test_workspace):
        """Test handling of server startup failures"""
        cluster_manager = LSPClusterManager(max_replicas_per_language=1)
        
        # Deploy with non-existent language
        fake_config = {"fake_language": {
            "id": "fake",
            "extensions": [".fake"],
            "command": ["nonexistent-server"],
            "requires": ["nonexistent-tool"],
            "initialization": {}
        }}
        
        with patch.dict(LSP_SERVER_CONFIGS, fake_config):
            deployment_status = cluster_manager.deploy_lsp_cluster([str(test_workspace)])
        
        # Should track failed deployments
        assert isinstance(deployment_status["failed_languages"], list)
        
        # Cleanup
        cluster_manager.shutdown_all()
    
    def test_health_check_with_unhealthy_servers(self, ray_cluster):
        """Test health check with unhealthy servers"""
        cluster_manager = LSPClusterManager()
        
        # Create mock servers that will fail health checks
        failing_server = Mock()
        failing_server.diagnostics.remote.side_effect = Exception("Server unhealthy")
        
        healthy_server = Mock()
        healthy_server.diagnostics.remote.return_value = ray.put({})
        
        cluster_manager.lsp_pools["python"] = [healthy_server, failing_server]
        
        # Health check should handle failures gracefully
        health_status = cluster_manager.health_check()
        
        assert health_status["healthy_servers"] >= 1
        assert health_status["unhealthy_servers"] >= 1
        
        # Cleanup
        cluster_manager.shutdown_all()
    
    def test_partial_deployment_recovery(self, ray_cluster, test_workspace):
        """Test recovery from partial deployment failures"""
        cluster_manager = LSPClusterManager()
        
        # Simulate partial deployment
        mock_server = Mock()
        mock_server.shutdown.remote.return_value = ray.put(None)
        cluster_manager.lsp_pools["python"] = [mock_server]
        cluster_manager.broken_servers.add("typescript:workspace")
        
        # System should still be functional
        available_server = cluster_manager.get_lsp_server("python")
        assert available_server is not None
        
        unavailable_server = cluster_manager.get_lsp_server("typescript")
        assert unavailable_server is None or len(cluster_manager.lsp_pools.get("typescript", [])) == 0
        
        # Cleanup
        cluster_manager.shutdown_all()


class TestScalingBehavior:
    """Test scaling behavior under different loads"""
    
    def test_scaling_up_and_down(self, ray_cluster, test_workspace):
        """Test scaling servers up and down"""
        cluster_manager = LSPClusterManager()
        
        # Mock initial server
        mock_server = Mock()
        mock_server.shutdown.remote.return_value = ray.put(None)
        cluster_manager.lsp_pools["python"] = [mock_server]
        
        # Test scaling up
        with patch.object(cluster_manager, '_deploy_single_server') as mock_deploy:
            mock_deploy.return_value = Mock()
            
            success = cluster_manager.scale_language_servers("python", 5, [str(test_workspace)])
            assert success is True
        
        # Test scaling down
        cluster_manager.lsp_pools["python"] = [Mock() for _ in range(5)]
        
        success = cluster_manager.scale_language_servers("python", 2, [str(test_workspace)])
        assert success is True
        
        # Cleanup
        cluster_manager.shutdown_all()
    
    def test_concurrent_scaling_operations(self, ray_cluster, test_workspace):
        """Test concurrent scaling operations"""
        cluster_manager = LSPClusterManager()
        
        # Initialize with some servers
        for language in ["python", "typescript", "go"]:
            cluster_manager.lsp_pools[language] = [Mock() for _ in range(2)]
        
        # Perform concurrent scaling
        import threading
        
        def scale_language(lang, target):
            cluster_manager.scale_language_servers(lang, target, [str(test_workspace)])
        
        threads = [
            threading.Thread(target=scale_language, args=("python", 4)),
            threading.Thread(target=scale_language, args=("typescript", 1)),
            threading.Thread(target=scale_language, args=("go", 3))
        ]
        
        for thread in threads:
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # System should remain stable
        assert isinstance(cluster_manager.lsp_pools, dict)
        
        # Cleanup
        cluster_manager.shutdown_all()


class TestMonitoringAndObservability:
    """Test monitoring and observability features"""
    
    def test_health_monitoring_metrics(self, ray_cluster):
        """Test health monitoring metrics collection"""
        cluster_manager = LSPClusterManager()
        
        # Create servers with different health states
        healthy_servers = [Mock() for _ in range(3)]
        unhealthy_servers = [Mock() for _ in range(2)]
        
        for server in healthy_servers:
            server.diagnostics.remote.return_value = ray.put({})
        
        for server in unhealthy_servers:
            server.diagnostics.remote.side_effect = Exception("Unhealthy")
        
        cluster_manager.lsp_pools["python"] = healthy_servers + unhealthy_servers
        
        # Collect health metrics
        health_status = cluster_manager.health_check()
        
        # Verify metrics structure
        assert "timestamp" in health_status
        assert isinstance(health_status["timestamp"], (int, float))
        assert health_status["timestamp"] > 0
        
        assert "language_status" in health_status
        assert "python" in health_status["language_status"]
        
        python_status = health_status["language_status"]["python"]
        assert "healthy" in python_status
        assert "unhealthy" in python_status
        assert "total" in python_status
        
        # Cleanup
        cluster_manager.shutdown_all()
    
    def test_deployment_status_tracking(self, ray_cluster, test_workspace):
        """Test deployment status tracking"""
        cluster_manager = LSPClusterManager(max_replicas_per_language=1)
        
        # Mock some successful and failed deployments
        with patch.object(cluster_manager, '_deploy_single_server') as mock_deploy:
            def deploy_side_effect(lang_id, workspace, replica_id):
                if lang_id == "python":
                    return Mock()  # Success
                else:
                    return None  # Failure
            
            mock_deploy.side_effect = deploy_side_effect
            
            # Deploy limited set for testing
            test_configs = {
                "python": LSP_SERVER_CONFIGS["python"],
                "fake": {"id": "fake", "extensions": [".fake"], "command": ["fake"], "requires": ["fake"], "initialization": {}}
            }
            
            with patch.dict(LSP_SERVER_CONFIGS, test_configs, clear=True):
                status = cluster_manager.deploy_lsp_cluster([str(test_workspace)])
        
        # Verify status tracking
        assert isinstance(status["deployed_languages"], list)
        assert isinstance(status["failed_languages"], list)
        assert isinstance(status["total_servers"], int)
        
        # Cleanup
        cluster_manager.shutdown_all()


# Test markers and configuration
# pytestmark = pytest.mark.asyncio  # Removed: only for async test functions

# Skip marks for optional dependencies
docker_required = pytest.mark.skipif(
    not any(os.path.exists(p) for p in ["/usr/bin/docker", "/usr/local/bin/docker"]),
    reason="Docker required for container tests"
)

ray_cluster_required = pytest.mark.skipif(
    False,  # Skip this check for now since Ray gets initialized in fixtures
    reason="Ray cluster required"
)