import os
import uuid
import pytest
import ray
import json
from pathlib import Path
from unittest.mock import Mock, patch

from sandbox_lsp_integration import (
    LSPEnhancedSandbox,
    LSPSandboxFactory,
    integrate_lsp_with_agent_session,
    LSP_TOOL_DEFINITIONS
)
from sandbox_v2 import DevSandboxV2


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
    workspace = tmp_path / f"sandbox_lsp_test_{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=True)
    
    # Create test files
    test_files = {
        "simple.py": '''
def hello_world():
    """Simple hello world function."""
    print("Hello, World!")
    return "hello"

class SimpleClass:
    def __init__(self, name: str):
        self.name = name
    
    def greet(self) -> str:
        return f"Hello, {self.name}!"
''',
        "syntax_error.py": '''
def broken_function(
    print("Missing closing parenthesis")
    return "error"
''',
        "complex.ts": '''
interface DataItem {
    id: number;
    name: string;
    value?: number;
}

class DataProcessor {
    private items: DataItem[] = [];
    
    public addItem(item: DataItem): void {
        this.items.push(item);
    }
    
    public getItem(id: number): DataItem | undefined {
        return this.items.find(item => item.id === id);
    }
}
'''
    }
    
    for filename, content in test_files.items():
        (workspace / filename).write_text(content)
    
    # Create configuration files
    (workspace / "package.json").write_text(json.dumps({
        "name": "sandbox-lsp-test",
        "version": "1.0.0"
    }))
    
    return workspace


@pytest.fixture
def base_sandbox(ray_cluster, test_workspace):
    """Create a base DevSandboxV2 for testing"""
    # Use local processes for testing
    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"
    
    sandbox = DevSandboxV2.options(
        name=f"test-sandbox-{uuid.uuid4().hex[:8]}"
    ).remote(
        image="python-dev:latest",
        workspace=str(test_workspace)
    )
    
    yield sandbox
    
    # Cleanup
    try:
        ray.kill(sandbox)
    except:
        pass


class TestLSPEnhancedSandbox:
    """Test the LSP-enhanced sandbox wrapper"""
    
    @pytest.fixture
    def enhanced_sandbox(self, base_sandbox, test_workspace):
        """Create an LSP-enhanced sandbox"""
        enhanced = LSPEnhancedSandbox.remote(base_sandbox, str(test_workspace))
        yield enhanced
        
        try:
            ray.get(enhanced.shutdown.remote())
        except:
            pass
    
    def test_basic_file_operations(self, enhanced_sandbox, test_workspace):
        """Test that basic file operations work with LSP enhancement"""
        # Test write_text
        result = ray.get(enhanced_sandbox.write_text.remote("test_write.py", "print('test')"))
        
        assert isinstance(result, dict)
        assert "path" in result
        assert "lsp_diagnostics" in result
        assert isinstance(result["lsp_diagnostics"], list)
        
        # Test read_text
        read_result = ray.get(enhanced_sandbox.read_text.remote("test_write.py"))
        assert isinstance(read_result, dict)
        assert "content" in read_result
        assert "print('test')" in read_result["content"]
        
        # Test file existence
        exists = ray.get(enhanced_sandbox.exists.remote("test_write.py"))
        assert exists is True
        
        # Test non-existent file
        not_exists = ray.get(enhanced_sandbox.exists.remote("nonexistent.py"))
        assert not_exists is False
    
    def test_edit_operations_with_diagnostics(self, enhanced_sandbox, test_workspace):
        """Test edit operations return diagnostics"""
        # Create a file with content
        ray.get(enhanced_sandbox.write_text.remote("edit_test.py", "def original_function():\n    pass"))
        
        # Edit the file
        edit_result = ray.get(enhanced_sandbox.edit_replace.remote(
            "edit_test.py", 
            "original_function", 
            "new_function"
        ))
        
        assert isinstance(edit_result, dict)
        assert "replacements" in edit_result
        assert edit_result["replacements"] >= 1
        assert "lsp_diagnostics" in edit_result
        
        # Verify the edit was applied
        content = ray.get(enhanced_sandbox.read_text.remote("edit_test.py"))
        assert "new_function" in content["content"]
    
    def test_multiedit_with_diagnostics(self, enhanced_sandbox, test_workspace):
        """Test multiedit operations with diagnostic collection"""
        # Create initial file
        ray.get(enhanced_sandbox.write_text.remote("multi_test.py", '''
def func1():
    pass

def func2():
    pass

def func3():
    pass
'''))
        
        # Perform multiple edits
        edits = [
            {"path": "multi_test.py", "old": "func1", "new": "function_one", "count": 1},
            {"path": "multi_test.py", "old": "func2", "new": "function_two", "count": 1},
            {"path": "multi_test.py", "old": "pass", "new": "return None", "count": 3}
        ]
        
        result = ray.get(enhanced_sandbox.multiedit.remote(edits))
        
        assert isinstance(result, dict)
        assert "results" in result
        assert len(result["results"]) == 3
        assert "lsp_diagnostics" in result
        
        # Verify edits were applied
        content = ray.get(enhanced_sandbox.read_text.remote("multi_test.py"))
        assert "function_one" in content["content"]
        assert "function_two" in content["content"]
        assert "return None" in content["content"]
    
    def test_lsp_diagnostic_methods(self, enhanced_sandbox, test_workspace):
        """Test LSP-specific diagnostic methods"""
        # Create a file with syntax errors
        ray.get(enhanced_sandbox.write_text.remote("error_test.py", '''
def broken_function(
    print("Missing closing parenthesis")
    return "error"
'''))
        
        # Test diagnostics collection
        diagnostics = ray.get(enhanced_sandbox.lsp_diagnostics.remote("error_test.py"))
        assert isinstance(diagnostics, dict)
        
        # Test all-file diagnostics
        all_diagnostics = ray.get(enhanced_sandbox.lsp_diagnostics.remote())
        assert isinstance(all_diagnostics, dict)
    
    def test_lsp_hover_functionality(self, enhanced_sandbox, test_workspace):
        """Test LSP hover functionality"""
        # Create a file with documented functions
        ray.get(enhanced_sandbox.write_text.remote("hover_test.py", '''
def documented_function():
    """This function has documentation."""
    return "documented"
'''))
        
        # Test hover on the function
        hover_result = ray.get(enhanced_sandbox.lsp_hover.remote("hover_test.py", 1, 4))
        assert isinstance(hover_result, dict)
    
    def test_lsp_symbol_search(self, enhanced_sandbox, test_workspace):
        """Test LSP symbol search functionality"""
        # Create files with various symbols
        ray.get(enhanced_sandbox.write_text.remote("symbols_test.py", '''
class TestClass:
    def test_method(self):
        pass

def test_function():
    pass

TEST_CONSTANT = "value"
'''))
        
        # Test workspace symbol search
        workspace_symbols = ray.get(enhanced_sandbox.lsp_workspace_symbols.remote("test"))
        assert isinstance(workspace_symbols, list)
        
        # Test document symbol search
        doc_symbols = ray.get(enhanced_sandbox.lsp_document_symbols.remote("symbols_test.py"))
        assert isinstance(doc_symbols, list)
    
    def test_lsp_code_intelligence(self, enhanced_sandbox, test_workspace):
        """Test code intelligence features"""
        # Create a file for testing
        ray.get(enhanced_sandbox.write_text.remote("intelligence_test.py", '''
def target_function():
    return "target"

def caller_function():
    return target_function()
'''))
        
        # Test go to definition
        definition_result = ray.get(enhanced_sandbox.lsp_go_to_definition.remote("intelligence_test.py", 5, 11))
        assert isinstance(definition_result, dict)
        
        # Test find references
        references_result = ray.get(enhanced_sandbox.lsp_find_references.remote("intelligence_test.py", 1, 4))
        assert isinstance(references_result, dict)
        
        # Test completion
        completion_result = ray.get(enhanced_sandbox.lsp_completion.remote("intelligence_test.py", 3, 0))
        assert isinstance(completion_result, dict)
        
        # Test formatting
        format_result = ray.get(enhanced_sandbox.lsp_format_document.remote("intelligence_test.py"))
        assert isinstance(format_result, dict)
        
        # Test code actions
        actions_result = ray.get(enhanced_sandbox.lsp_code_actions.remote("intelligence_test.py", 0, 0, 2, 0))
        assert isinstance(actions_result, dict)
    
    def test_forwarded_methods(self, enhanced_sandbox, test_workspace):
        """Test that base sandbox methods are properly forwarded"""
        # Test basic methods that should be forwarded
        session_id = ray.get(enhanced_sandbox.get_session_id.remote())
        assert isinstance(session_id, str)
        
        workspace = ray.get(enhanced_sandbox.get_workspace.remote())
        assert isinstance(workspace, str)
        
        # Test ls method
        files = ray.get(enhanced_sandbox.ls.remote("."))
        assert isinstance(files, list)
        
        # Test glob method
        py_files = ray.get(enhanced_sandbox.glob.remote("*.py"))
        assert isinstance(py_files, list)
        
        # Test grep method
        grep_result = ray.get(enhanced_sandbox.grep.remote("def", "."))
        assert isinstance(grep_result, dict)
    
    def test_vcs_integration(self, enhanced_sandbox, test_workspace):
        """Test version control integration"""
        # Test VCS initialization
        init_result = ray.get(enhanced_sandbox.vcs.remote({
            "action": "init",
            "user": {"name": "Test User", "email": "test@example.com"}
        }))
        assert isinstance(init_result, dict)
        
        # Test status check
        status_result = ray.get(enhanced_sandbox.vcs.remote({"action": "status"}))
        assert isinstance(status_result, dict)


class TestLSPSandboxFactory:
    """Test the LSP sandbox factory"""
    
    def test_factory_creation(self, ray_cluster, test_workspace):
        """Test sandbox creation through factory"""
        enhanced_sandbox = LSPSandboxFactory.create_enhanced_sandbox(
            image="python-dev:latest",
            workspace=str(test_workspace)
        )
        
        # Test that the enhanced sandbox works
        session_id = ray.get(enhanced_sandbox.get_session_id.remote())
        assert isinstance(session_id, str)
        
        # Test basic LSP functionality
        diagnostics = ray.get(enhanced_sandbox.lsp_diagnostics.remote())
        assert isinstance(diagnostics, dict)
        
        # Cleanup
        try:
            ray.get(enhanced_sandbox.shutdown.remote())
        except:
            pass
    
    def test_factory_with_session_id(self, ray_cluster, test_workspace):
        """Test factory with custom session ID"""
        custom_session_id = f"custom-session-{uuid.uuid4().hex[:8]}"
        
        enhanced_sandbox = LSPSandboxFactory.create_enhanced_sandbox(
            image="python-dev:latest",
            workspace=str(test_workspace),
            session_id=custom_session_id
        )
        
        # Should work with custom session ID
        session_id = ray.get(enhanced_sandbox.get_session_id.remote())
        assert isinstance(session_id, str)
        
        # Cleanup
        try:
            ray.get(enhanced_sandbox.shutdown.remote())
        except:
            pass


class TestAgentIntegration:
    """Test integration with agent systems"""
    
    def test_agent_decorator_integration(self, ray_cluster, test_workspace):
        """Test agent decorator integration"""
        # Create a mock agent class
        class MockAgent:
            def __init__(self, workspace: str):
                self.workspace = workspace
                self.sandbox = LSPSandboxFactory.create_enhanced_sandbox(
                    image="python-dev:latest",
                    workspace=workspace
                )
            
            def _execute_tool(self, name: str, args: dict):
                """Original tool execution method"""
                return {"output": f"original_{name}", "metadata": {"type": "original"}}
        
        # Apply the integration decorator
        @integrate_lsp_with_agent_session
        class EnhancedMockAgent(MockAgent):
            pass
        
        # Create enhanced agent
        agent = EnhancedMockAgent(str(test_workspace))
        
        # Test that LSP tools are handled
        lsp_hover_result = agent._execute_tool("lsp_hover", {
            "file_path": "simple.py", 
            "line": 1, 
            "character": 4
        })
        assert isinstance(lsp_hover_result, dict)
        assert "output" in lsp_hover_result
        assert "metadata" in lsp_hover_result
        assert lsp_hover_result["metadata"]["type"] == "lsp_hover"
        
        # Test that non-LSP tools still work
        original_result = agent._execute_tool("original_tool", {"param": "value"})
        assert original_result["output"] == "original_original_tool"
        assert original_result["metadata"]["type"] == "original"
    
    def test_lsp_tool_definitions(self):
        """Test LSP tool definitions structure"""
        assert isinstance(LSP_TOOL_DEFINITIONS, list)
        assert len(LSP_TOOL_DEFINITIONS) > 0
        
        for tool_def in LSP_TOOL_DEFINITIONS:
            assert isinstance(tool_def, dict)
            assert "name" in tool_def
            assert "description" in tool_def
            assert "parameters" in tool_def
            
            # Validate parameter structure
            params = tool_def["parameters"]
            assert "type" in params
            assert "properties" in params
            assert "required" in params
            
            # Check that required parameters are in properties
            for required_param in params["required"]:
                assert required_param in params["properties"]
    
    def test_all_lsp_tools_integration(self, ray_cluster, test_workspace):
        """Test all LSP tools through agent integration"""
        class TestAgent:
            def __init__(self, workspace: str):
                self.sandbox = LSPSandboxFactory.create_enhanced_sandbox(
                    image="python-dev:latest",
                    workspace=workspace
                )
            
            def _execute_tool(self, name: str, args: dict):
                return {"output": f"base_{name}", "metadata": {"type": "base"}}
        
        @integrate_lsp_with_agent_session
        class FullLSPAgent(TestAgent):
            pass
        
        agent = FullLSPAgent(str(test_workspace))
        
        # Create a test file
        ray.get(agent.sandbox.write_text.remote("test_all.py", '''
def test_function():
    """Test function for LSP tools."""
    return "test"

class TestClass:
    def __init__(self):
        self.value = 42
'''))
        
        # Test all LSP tools
        lsp_tools = [
            ("lsp_hover", {"file_path": "test_all.py", "line": 1, "character": 4}),
            ("lsp_go_to_definition", {"file_path": "test_all.py", "line": 1, "character": 4}),
            ("lsp_find_references", {"file_path": "test_all.py", "line": 1, "character": 4}),
            ("lsp_workspace_symbols", {"query": "test"}),
            ("lsp_document_symbols", {"file_path": "test_all.py"}),
            ("lsp_format_document", {"file_path": "test_all.py"}),
            ("lsp_code_actions", {"file_path": "test_all.py", "start_line": 0, "start_char": 0, "end_line": 2, "end_char": 0}),
            ("lsp_completion", {"file_path": "test_all.py", "line": 3, "character": 0})
        ]
        
        for tool_name, tool_args in lsp_tools:
            result = agent._execute_tool(tool_name, tool_args)
            assert isinstance(result, dict)
            assert "output" in result
            assert "metadata" in result
            assert result["metadata"]["type"].startswith("lsp_")


class TestErrorHandling:
    """Test error handling in sandbox integration"""
    
    def test_invalid_file_operations(self, ray_cluster, test_workspace):
        """Test error handling with invalid file operations"""
        enhanced_sandbox = LSPSandboxFactory.create_enhanced_sandbox(
            image="python-dev:latest",
            workspace=str(test_workspace)
        )
        
        # Test operations on non-existent files
        hover_result = ray.get(enhanced_sandbox.lsp_hover.remote("nonexistent.py", 1, 1))
        assert isinstance(hover_result, dict)
        
        # Test invalid line/character positions
        definition_result = ray.get(enhanced_sandbox.lsp_go_to_definition.remote("simple.py", -1, -1))
        assert isinstance(definition_result, dict)
        
        # Test with empty file path
        symbols_result = ray.get(enhanced_sandbox.lsp_document_symbols.remote(""))
        assert isinstance(symbols_result, list)
        
        # Cleanup
        try:
            ray.get(enhanced_sandbox.shutdown.remote())
        except:
            pass
    
    def test_lsp_manager_failure_handling(self, ray_cluster, test_workspace):
        """Test behavior when LSP manager fails"""
        # Create a sandbox but don't register workspace
        enhanced_sandbox = LSPSandboxFactory.create_enhanced_sandbox(
            image="python-dev:latest",
            workspace=str(test_workspace)
        )
        
        # Operations should handle LSP failures gracefully
        result = ray.get(enhanced_sandbox.write_text.remote("failure_test.py", "print('test')"))
        assert isinstance(result, dict)
        assert "lsp_diagnostics" in result  # Should be empty list on failure
        
        # Cleanup
        try:
            ray.get(enhanced_sandbox.shutdown.remote())
        except:
            pass
    
    def test_shutdown_with_pending_operations(self, ray_cluster, test_workspace):
        """Test shutdown behavior with pending operations"""
        enhanced_sandbox = LSPSandboxFactory.create_enhanced_sandbox(
            image="python-dev:latest",
            workspace=str(test_workspace)
        )
        
        # Start some operations
        future1 = enhanced_sandbox.lsp_workspace_symbols.remote("test")
        future2 = enhanced_sandbox.lsp_diagnostics.remote()
        
        # Shutdown before operations complete
        ray.get(enhanced_sandbox.shutdown.remote())
        
        # Operations should handle shutdown gracefully
        try:
            ray.get([future1, future2])
        except:
            pass  # Expected to fail or return partial results


class TestPerformanceAndConcurrency:
    """Test performance and concurrency aspects"""
    
    def test_concurrent_lsp_operations(self, ray_cluster, test_workspace):
        """Test concurrent LSP operations"""
        enhanced_sandbox = LSPSandboxFactory.create_enhanced_sandbox(
            image="python-dev:latest",
            workspace=str(test_workspace)
        )
        
        # Create test files
        for i in range(5):
            ray.get(enhanced_sandbox.write_text.remote(f"concurrent_{i}.py", f'''
def function_{i}():
    """Function number {i}."""
    return {i}
'''))
        
        # Run concurrent operations
        futures = []
        for i in range(5):
            futures.extend([
                enhanced_sandbox.lsp_hover.remote(f"concurrent_{i}.py", 1, 4),
                enhanced_sandbox.lsp_document_symbols.remote(f"concurrent_{i}.py"),
                enhanced_sandbox.lsp_diagnostics.remote(f"concurrent_{i}.py")
            ])
        
        # All should complete
        results = ray.get(futures)
        assert len(results) == 15
        
        for result in results:
            assert isinstance(result, (dict, list))
        
        # Cleanup
        try:
            ray.get(enhanced_sandbox.shutdown.remote())
        except:
            pass
    
    def test_large_file_handling(self, ray_cluster, test_workspace):
        """Test handling of large files"""
        enhanced_sandbox = LSPSandboxFactory.create_enhanced_sandbox(
            image="python-dev:latest",
            workspace=str(test_workspace)
        )
        
        # Create a large Python file
        large_content = ""
        for i in range(1000):  # 1000 functions
            large_content += f'''
def function_{i}():
    """Function number {i}."""
    value = {i}
    return value * 2
'''
        
        result = ray.get(enhanced_sandbox.write_text.remote("large_file.py", large_content))
        assert isinstance(result, dict)
        assert result.get("size", 0) > 10000  # Should be reasonably large
        
        # LSP operations should still work
        symbols = ray.get(enhanced_sandbox.lsp_document_symbols.remote("large_file.py"))
        assert isinstance(symbols, list)
        
        # Cleanup
        try:
            ray.get(enhanced_sandbox.shutdown.remote())
        except:
            pass


class TestMultiLanguageSupport:
    """Test multi-language support in sandbox integration"""
    
    def test_python_typescript_integration(self, ray_cluster, test_workspace):
        """Test Python and TypeScript file handling"""
        enhanced_sandbox = LSPSandboxFactory.create_enhanced_sandbox(
            image="python-dev:latest",
            workspace=str(test_workspace)
        )
        
        # Create Python file
        py_content = '''
def python_function():
    """Python function."""
    return "python"
'''
        ray.get(enhanced_sandbox.write_text.remote("multi_test.py", py_content))
        
        # Create TypeScript file
        ts_content = '''
function typescriptFunction(): string {
    return "typescript";
}

interface TestInterface {
    value: number;
}
'''
        ray.get(enhanced_sandbox.write_text.remote("multi_test.ts", ts_content))
        
        # Test operations on both files
        py_symbols = ray.get(enhanced_sandbox.lsp_document_symbols.remote("multi_test.py"))
        ts_symbols = ray.get(enhanced_sandbox.lsp_document_symbols.remote("multi_test.ts"))
        
        assert isinstance(py_symbols, list)
        assert isinstance(ts_symbols, list)
        
        # Test workspace-wide symbol search
        workspace_symbols = ray.get(enhanced_sandbox.lsp_workspace_symbols.remote("function"))
        assert isinstance(workspace_symbols, list)
        
        # Cleanup
        try:
            ray.get(enhanced_sandbox.shutdown.remote())
        except:
            pass
    
    def test_language_specific_diagnostics(self, ray_cluster, test_workspace):
        """Test language-specific diagnostic collection"""
        enhanced_sandbox = LSPSandboxFactory.create_enhanced_sandbox(
            image="python-dev:latest",
            workspace=str(test_workspace)
        )
        
        # Create files with language-specific errors
        python_error = '''
def broken_python(
    print("Missing closing paren")
'''
        
        typescript_error = '''
function brokenTypeScript() {
    const obj = {
        prop: "value"
    // Missing closing brace
'''
        
        ray.get(enhanced_sandbox.write_text.remote("py_error.py", python_error))
        ray.get(enhanced_sandbox.write_text.remote("ts_error.ts", typescript_error))
        
        # Get diagnostics for each
        py_diagnostics = ray.get(enhanced_sandbox.lsp_diagnostics.remote("py_error.py"))
        ts_diagnostics = ray.get(enhanced_sandbox.lsp_diagnostics.remote("ts_error.ts"))
        
        assert isinstance(py_diagnostics, dict)
        assert isinstance(ts_diagnostics, dict)
        
        # Cleanup
        try:
            ray.get(enhanced_sandbox.shutdown.remote())
        except:
            pass


# Test configuration
# pytestmark = pytest.mark.asyncio  # Removed: only for async test functions