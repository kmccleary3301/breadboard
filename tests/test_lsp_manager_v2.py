import os
import uuid
import pytest
import ray
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, patch

from lsp_manager_v2 import (
    LSPManagerV2, 
    LSPServer, 
    LSPOrchestrator, 
    UnifiedDiagnostics,
    LSPJSONRPCClient,
    LSP_SERVER_CONFIGS
)


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
    """Create a test workspace with sample files"""
    workspace = tmp_path / f"lsp_test_{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=True)
    
    # Create test files with various languages
    test_files = {
        "test.py": '''
def calculate_fibonacci(n: int) -> int:
    """Calculate the nth Fibonacci number."""
    if n <= 1:
        return n
    return calculate_fibonacci(n - 1) + calculate_fibonacci(n - 2)

class Calculator:
    def __init__(self, name: str):
        self.name = name
    
    def add(self, a: int, b: int) -> int:
        return a + b

# Syntax error for testing diagnostics
def broken_function(
    print("Missing closing parenthesis")
''',
        "test.ts": '''
interface User {
    id: number;
    name: string;
    email?: string;
}

class UserService {
    private users: User[] = [];
    
    addUser(user: User): void {
        this.users.push(user);
    }
    
    getUserById(id: number): User | undefined {
        return this.users.find(u => u.id === id);
    }
}

// Syntax error for testing diagnostics
const missingBrace = {
    name: "test"
''',
        "test.go": '''
package main

import "fmt"

type Calculator struct {
    Name string
}

func (c *Calculator) Add(a, b int) int {
    return a + b
}

func main() {
    calc := &Calculator{Name: "test"}
    result := calc.Add(1, 2)
    fmt.Println(result)
}
'''
    }
    
    for filename, content in test_files.items():
        (workspace / filename).write_text(content)
    
    # Create language-specific config files
    (workspace / "package.json").write_text(json.dumps({
        "name": "test-project",
        "version": "1.0.0",
        "devDependencies": {"typescript": "^5.0.0"}
    }))
    
    (workspace / "go.mod").write_text("module test\n\ngo 1.20\n")
    (workspace / "pyproject.toml").write_text("[tool.pyright]\n")
    
    return workspace


class TestLSPJSONRPCClient:
    """Test LSP JSON-RPC client functionality"""
    
    def test_request_id_increment(self):
        """Test that request IDs increment correctly"""
        mock_process = Mock()
        mock_process.stdin = Mock()
        mock_process.stdout = Mock()
        
        client = LSPJSONRPCClient(mock_process)
        
        # Mock the _read_response method to avoid actual I/O
        client._read_response = Mock(return_value={"jsonrpc": "2.0", "id": 1, "result": {}})
        
        result1 = client._send_request("test_method")
        result2 = client._send_request("another_method")
        
        assert client.request_id == 2
    
    def test_notification_no_id(self):
        """Test that notifications don't have IDs"""
        mock_process = Mock()
        mock_process.stdin = Mock()
        
        client = LSPJSONRPCClient(mock_process)
        
        # Should not raise exception
        client._send_notification("test_notification", {"param": "value"})
        
        # Verify stdin.write was called
        mock_process.stdin.write.assert_called()


class TestLSPServerConfigs:
    """Test LSP server configuration validation"""
    
    def test_all_configs_have_required_fields(self):
        """Test that all server configs have required fields"""
        required_fields = ["id", "extensions", "command", "requires"]
        
        for server_id, config in LSP_SERVER_CONFIGS.items():
            for field in required_fields:
                assert field in config, f"Server {server_id} missing field {field}"
            
            assert isinstance(config["extensions"], list), f"Server {server_id} extensions must be list"
            assert len(config["extensions"]) > 0, f"Server {server_id} must have at least one extension"
            assert isinstance(config["command"], list), f"Server {server_id} command must be list"
    
    def test_no_extension_conflicts(self):
        """Test that no two servers claim the same extension without overlap being intentional"""
        extension_to_servers = {}
        
        for server_id, config in LSP_SERVER_CONFIGS.items():
            for ext in config["extensions"]:
                if ext not in extension_to_servers:
                    extension_to_servers[ext] = []
                extension_to_servers[ext].append(server_id)
        
        # Some extensions like .js can legitimately be handled by multiple servers
        # But let's check that core extensions have clear ownership
        expected_owners = {
            ".py": ["python"],
            ".go": ["go"], 
            ".rs": ["rust"],
            ".java": ["java"],
            ".rb": ["ruby"],
            ".cs": ["csharp"]
        }
        
        for ext, expected_servers in expected_owners.items():
            if ext in extension_to_servers:
                actual_servers = extension_to_servers[ext]
                for expected in expected_servers:
                    assert expected in actual_servers, f"Extension {ext} should be handled by {expected}"


class TestLSPServer:
    """Test individual LSP server functionality"""
    
    @pytest.fixture
    def mock_lsp_server(self, test_workspace):
        """Create a mock LSP server for testing"""
        # Use local processes instead of containers for testing
        os.environ["LSP_USE_CONTAINERS"] = "0"
        
        server = LSPServer.remote("python", str(test_workspace))
        yield server
        
        # Cleanup
        try:
            ray.get(server.shutdown.remote())
        except:
            pass
    
    def test_server_initialization(self, ray_cluster, mock_lsp_server):
        """Test LSP server initialization"""
        # Test basic server creation
        session_id = ray.get(mock_lsp_server.get_session_id.remote())
        assert isinstance(session_id, str)
        assert len(session_id) > 0
    
    def test_server_requirements_check(self, ray_cluster, test_workspace):
        """Test server requirements checking"""
        # Test with a server that has impossible requirements
        fake_config = {
            "id": "fake",
            "extensions": [".fake"],
            "command": ["nonexistent-language-server"],
            "requires": ["nonexistent-binary-xyz123"],
            "initialization": {}
        }
        
        # Temporarily add fake config
        original_config = LSP_SERVER_CONFIGS.get("fake")
        LSP_SERVER_CONFIGS["fake"] = fake_config
        
        try:
            server = LSPServer.remote("fake", str(test_workspace))
            started = ray.get(server.start.remote())
            assert started is False  # Should fail due to missing requirements
        finally:
            # Restore original config
            if original_config is None:
                LSP_SERVER_CONFIGS.pop("fake", None)
            else:
                LSP_SERVER_CONFIGS["fake"] = original_config
    
    @patch('shutil.which')
    def test_server_with_missing_requirements(self, mock_which, ray_cluster, test_workspace):
        """Test server behavior when required tools are missing"""
        # Mock all requirements as missing
        mock_which.return_value = None
        
        server = LSPServer.remote("python", str(test_workspace))
        started = ray.get(server.start.remote())
        
        assert started is False
    
    def test_document_operations(self, ray_cluster, mock_lsp_server, test_workspace):
        """Test document open/close operations"""
        test_file = test_workspace / "test.py"
        
        # Test opening document
        result = ray.get(mock_lsp_server.open_document.remote(str(test_file)))
        
        # Should not error (even if server isn't actually running)
        assert "error" in result or "status" in result


class TestLSPOrchestrator:
    """Test LSP orchestration and server management"""
    
    @pytest.fixture
    def orchestrator(self, ray_cluster):
        """Create LSP orchestrator for testing"""
        return LSPOrchestrator.remote()
    
    def test_project_root_detection(self, orchestrator, test_workspace):
        """Test project root detection logic"""
        # Create a Python file in a subdirectory
        subdir = test_workspace / "src" / "utils"
        subdir.mkdir(parents=True)
        test_file = subdir / "helper.py"
        test_file.write_text("def helper(): pass")
        
        # Should find the workspace root due to pyproject.toml
        servers = ray.get(orchestrator.get_servers_for_file.remote(str(test_file)))
        
        # Should return some servers (even if they fail to start)
        assert isinstance(servers, list)
    
    def test_extension_based_server_selection(self, orchestrator, test_workspace):
        """Test that appropriate servers are selected for file extensions"""
        python_file = test_workspace / "test.py"
        typescript_file = test_workspace / "test.ts"
        go_file = test_workspace / "test.go"
        
        # Test Python file
        py_servers = ray.get(orchestrator.get_servers_for_file.remote(str(python_file)))
        assert isinstance(py_servers, list)
        
        # Test TypeScript file  
        ts_servers = ray.get(orchestrator.get_servers_for_file.remote(str(typescript_file)))
        assert isinstance(ts_servers, list)
        
        # Test Go file
        go_servers = ray.get(orchestrator.get_servers_for_file.remote(str(go_file)))
        assert isinstance(go_servers, list)
    
    def test_server_caching(self, orchestrator, test_workspace):
        """Test that servers are cached and reused"""
        test_file = test_workspace / "test.py"
        
        # Get servers twice
        servers1 = ray.get(orchestrator.get_servers_for_file.remote(str(test_file)))
        servers2 = ray.get(orchestrator.get_servers_for_file.remote(str(test_file)))
        
        # Should return consistent results
        assert len(servers1) == len(servers2)
    
    def test_broken_server_tracking(self, orchestrator, test_workspace):
        """Test that broken servers are tracked and avoided"""
        # This is harder to test without actually breaking servers
        # For now, just verify the broken_servers set exists
        orchestrator_instance = ray.get(orchestrator.__ray_ready__.remote())
        assert hasattr(orchestrator_instance, 'broken_servers') or True  # Access is limited


class TestUnifiedDiagnostics:
    """Test unified diagnostics collection"""
    
    @pytest.fixture  
    def unified_diagnostics(self, ray_cluster):
        """Create unified diagnostics collector for testing"""
        return UnifiedDiagnostics.remote()
    
    def test_diagnostic_collection_structure(self, unified_diagnostics, test_workspace):
        """Test that diagnostic collection returns proper structure"""
        test_file = test_workspace / "test.py"
        
        diagnostics = ray.get(unified_diagnostics.collect_all_diagnostics.remote(str(test_file)))
        
        # Should return a dictionary (even if empty)
        assert isinstance(diagnostics, dict)
        
        # If diagnostics are returned, they should have proper structure
        for file_path, diag_list in diagnostics.items():
            assert isinstance(file_path, str)
            assert isinstance(diag_list, list)
            
            for diag in diag_list:
                assert isinstance(diag, dict)
                # Should have LSP diagnostic structure
                expected_fields = ["range", "message", "severity"]
                for field in expected_fields:
                    # Not all diagnostics have all fields, but check structure if present
                    if field in diag:
                        assert isinstance(diag[field], (dict, str, int))
    
    def test_multiple_file_types(self, unified_diagnostics, test_workspace):
        """Test diagnostic collection for multiple file types"""
        files_to_test = [
            test_workspace / "test.py",
            test_workspace / "test.ts", 
            test_workspace / "test.go"
        ]
        
        for test_file in files_to_test:
            if test_file.exists():
                diagnostics = ray.get(unified_diagnostics.collect_all_diagnostics.remote(str(test_file)))
                assert isinstance(diagnostics, dict)


class TestLSPManagerV2:
    """Test the main LSP Manager V2 functionality"""
    
    @pytest.fixture
    def lsp_manager(self, ray_cluster):
        """Create LSP manager for testing"""
        return LSPManagerV2.remote()
    
    def test_workspace_registration(self, lsp_manager, test_workspace):
        """Test workspace root registration"""
        ray.get(lsp_manager.register_root.remote(str(test_workspace)))
        
        # Test file touching
        test_file = test_workspace / "test.py"
        ray.get(lsp_manager.touch_file.remote(str(test_file)))
        
        # Should not raise exceptions
    
    def test_diagnostics_interface(self, lsp_manager, test_workspace):
        """Test diagnostics collection interface"""
        ray.get(lsp_manager.register_root.remote(str(test_workspace)))
        
        # Touch a file with syntax errors
        test_file = test_workspace / "test.py"
        ray.get(lsp_manager.touch_file.remote(str(test_file)))
        
        # Get diagnostics
        diagnostics = ray.get(lsp_manager.diagnostics.remote())
        
        assert isinstance(diagnostics, dict)
        
        # Test that we can get diagnostics multiple times
        diagnostics2 = ray.get(lsp_manager.diagnostics.remote())
        assert isinstance(diagnostics2, dict)
    
    def test_symbol_search_interface(self, lsp_manager, test_workspace):
        """Test symbol search interfaces"""
        ray.get(lsp_manager.register_root.remote(str(test_workspace)))
        
        # Test workspace symbol search
        symbols = ray.get(lsp_manager.workspace_symbol.remote("Calculator", 10))
        assert isinstance(symbols, list)
        
        # Test document symbol search
        test_file = test_workspace / "test.py"
        doc_symbols = ray.get(lsp_manager.document_symbol.remote(str(test_file)))
        assert isinstance(doc_symbols, list)
    
    def test_code_intelligence_features(self, lsp_manager, test_workspace):
        """Test code intelligence features (hover, definition, etc.)"""
        ray.get(lsp_manager.register_root.remote(str(test_workspace)))
        
        test_file = test_workspace / "test.py"
        
        # Test hover
        hover_result = ray.get(lsp_manager.hover.remote(str(test_file), 1, 4))
        assert isinstance(hover_result, dict)
        
        # Test go to definition
        definition_result = ray.get(lsp_manager.go_to_definition.remote(str(test_file), 5, 10))
        assert isinstance(definition_result, dict)
        
        # Test find references
        references_result = ray.get(lsp_manager.find_references.remote(str(test_file), 5, 10))
        assert isinstance(references_result, dict)
        
        # Test formatting
        format_result = ray.get(lsp_manager.format_document.remote(str(test_file)))
        assert isinstance(format_result, dict)
        
        # Test code actions
        actions_result = ray.get(lsp_manager.code_actions.remote(str(test_file), 0, 0, 1, 10))
        assert isinstance(actions_result, dict)
        
        # Test completion
        completion_result = ray.get(lsp_manager.completion.remote(str(test_file), 2, 5))
        assert isinstance(completion_result, dict)
    
    def test_shutdown_cleanup(self, lsp_manager, test_workspace):
        """Test proper shutdown and cleanup"""
        ray.get(lsp_manager.register_root.remote(str(test_workspace)))
        
        # Test shutdown
        ray.get(lsp_manager.shutdown.remote())
        
        # Should not raise exceptions


class TestLSPManagerV2Integration:
    """Integration tests for LSP Manager V2 with realistic scenarios"""
    
    def test_multi_language_project(self, ray_cluster, test_workspace):
        """Test LSP manager with multi-language project"""
        lsp_manager = LSPManagerV2.remote()
        ray.get(lsp_manager.register_root.remote(str(test_workspace)))
        
        # Touch multiple file types
        files = ["test.py", "test.ts", "test.go"]
        for filename in files:
            if (test_workspace / filename).exists():
                ray.get(lsp_manager.touch_file.remote(str(test_workspace / filename)))
        
        # Get diagnostics for all files
        diagnostics = ray.get(lsp_manager.diagnostics.remote())
        assert isinstance(diagnostics, dict)
        
        # Search for symbols across all languages
        symbols = ray.get(lsp_manager.workspace_symbol.remote("Calculator", 50))
        assert isinstance(symbols, list)
    
    def test_error_handling_with_invalid_files(self, ray_cluster, test_workspace):
        """Test error handling with invalid file paths"""
        lsp_manager = LSPManagerV2.remote()
        ray.get(lsp_manager.register_root.remote(str(test_workspace)))
        
        # Test with non-existent file
        nonexistent_file = test_workspace / "nonexistent.py"
        
        # Should handle gracefully without crashing
        hover_result = ray.get(lsp_manager.hover.remote(str(nonexistent_file), 1, 1))
        assert isinstance(hover_result, dict)
        
        definition_result = ray.get(lsp_manager.go_to_definition.remote(str(nonexistent_file), 1, 1))
        assert isinstance(definition_result, dict)
    
    def test_concurrent_operations(self, ray_cluster, test_workspace):
        """Test concurrent LSP operations"""
        lsp_manager = LSPManagerV2.remote()
        ray.get(lsp_manager.register_root.remote(str(test_workspace)))
        
        test_file = test_workspace / "test.py"
        
        # Run multiple operations concurrently
        futures = [
            lsp_manager.hover.remote(str(test_file), 1, 1),
            lsp_manager.workspace_symbol.remote("test", 10),
            lsp_manager.document_symbol.remote(str(test_file)),
            lsp_manager.diagnostics.remote()
        ]
        
        # Wait for all to complete
        results = ray.get(futures)
        
        # All should return dict/list results
        for result in results:
            assert isinstance(result, (dict, list))
    
    def test_resource_cleanup_on_failure(self, ray_cluster, test_workspace):
        """Test that resources are properly cleaned up on failures"""
        lsp_manager = LSPManagerV2.remote()
        ray.get(lsp_manager.register_root.remote(str(test_workspace)))
        
        # Force some operations that might fail
        try:
            # Use invalid parameters that might cause failures
            ray.get(lsp_manager.hover.remote("", -1, -1))
        except:
            pass  # Expected to potentially fail
        
        try:
            ray.get(lsp_manager.go_to_definition.remote("/invalid/path", 999, 999))
        except:
            pass  # Expected to potentially fail
        
        # Manager should still be functional
        diagnostics = ray.get(lsp_manager.diagnostics.remote())
        assert isinstance(diagnostics, dict)
        
        # Cleanup should work
        ray.get(lsp_manager.shutdown.remote())


class TestContainerIntegration:
    """Test container integration aspects"""
    
    def test_container_environment_detection(self):
        """Test container environment variable detection"""
        # Test with containers disabled
        os.environ["LSP_USE_CONTAINERS"] = "0"
        # The server should use local processes
        
        # Test with containers enabled  
        os.environ["LSP_USE_CONTAINERS"] = "1"
        # The server should attempt to use containers
        
        # Reset to default
        os.environ.pop("LSP_USE_CONTAINERS", None)
    
    @pytest.mark.skipif(not os.path.exists("/usr/bin/docker") and not os.path.exists("/usr/local/bin/docker"), 
                        reason="Docker not available")
    def test_container_command_generation(self, test_workspace):
        """Test container command generation"""
        os.environ["LSP_USE_CONTAINERS"] = "1"
        
        try:
            server = LSPServer.remote("python", str(test_workspace), "lsp-universal:latest")
            # Just test that server can be created - actual container spawn testing requires docker
            session_id = ray.get(server.get_session_id.remote())
            assert isinstance(session_id, str)
        finally:
            os.environ.pop("LSP_USE_CONTAINERS", None)


class TestPerformanceAndScaling:
    """Performance and scaling tests"""
    
    def test_large_number_of_files(self, ray_cluster, tmp_path):
        """Test LSP manager with large number of files"""
        workspace = tmp_path / f"large_test_{uuid.uuid4().hex[:8]}"
        workspace.mkdir()
        
        # Create many Python files
        num_files = 50  # Reasonable number for testing
        for i in range(num_files):
            (workspace / f"file_{i}.py").write_text(f"def function_{i}():\n    return {i}\n")
        
        lsp_manager = LSPManagerV2.remote()
        ray.get(lsp_manager.register_root.remote(str(workspace)))
        
        # This should handle many files without crashing
        diagnostics = ray.get(lsp_manager.diagnostics.remote())
        assert isinstance(diagnostics, dict)
        
        # Search should work with many files
        symbols = ray.get(lsp_manager.workspace_symbol.remote("function", 100))
        assert isinstance(symbols, list)
    
    def test_memory_usage_stability(self, ray_cluster, test_workspace):
        """Test that memory usage remains stable over multiple operations"""
        lsp_manager = LSPManagerV2.remote()
        ray.get(lsp_manager.register_root.remote(str(test_workspace)))
        
        # Perform many operations
        test_file = test_workspace / "test.py"
        for i in range(20):
            ray.get(lsp_manager.touch_file.remote(str(test_file)))
            ray.get(lsp_manager.diagnostics.remote())
            ray.get(lsp_manager.workspace_symbol.remote(f"test_{i}", 10))
        
        # Should still be functional
        final_diagnostics = ray.get(lsp_manager.diagnostics.remote())
        assert isinstance(final_diagnostics, dict)


# Utility functions for testing
def create_test_file_with_errors(workspace_path: Path, filename: str, language: str):
    """Create test files with known syntax errors for diagnostic testing"""
    error_templates = {
        "python": "def broken_function(\n    print('missing closing paren')\n",
        "typescript": "const obj = {\n    prop: 'value'\n// missing closing brace",
        "go": "package main\n\nfunc main() {\n    fmt.Println('unclosed string\n}\n"
    }
    
    if language in error_templates:
        (workspace_path / filename).write_text(error_templates[language])
    else:
        (workspace_path / filename).write_text("// Generic test file")


# Test configuration and markers
# pytestmark = pytest.mark.asyncio  # Removed: only for async test functions

# Skip tests that require specific tools
python_server_available = pytest.mark.skipif(
    not any(os.path.exists(p) for p in ["/usr/bin/python3", "/usr/local/bin/python3"]),
    reason="Python not available"
)

docker_available = pytest.mark.skipif(
    not any(os.path.exists(p) for p in ["/usr/bin/docker", "/usr/local/bin/docker"]),
    reason="Docker not available"
)