"""
Integration layer between sandbox_v2.py and lsp_manager_v2.py
Provides enhanced LSP capabilities without modifying existing code.
"""

import os
import ray
from typing import Any, Dict, List, Optional

from lsp_manager_v2 import LSPManagerV2


@ray.remote
class LSPEnhancedSandbox:
    """
    Enhanced sandbox that wraps DevSandboxV2 with advanced LSP capabilities.
    This is a composition pattern that adds LSP features without modifying sandbox_v2.py
    """
    
    def __init__(self, base_sandbox: ray.ObjectRef, workspace: str):
        self.base_sandbox = base_sandbox
        self.workspace = workspace
        self.lsp_manager = LSPManagerV2.remote()
        ray.get(self.lsp_manager.register_root.remote(workspace))
        
    # Forward all base sandbox methods
    def put(self, path: str, content: bytes) -> None:
        return ray.get(self.base_sandbox.put.remote(path, content))
    
    def get(self, path: str) -> bytes:
        return ray.get(self.base_sandbox.get.remote(path))
    
    def ls(self, path: str = ".", depth: int = 1):
        """Forward ls and preserve base return shape; coerce to list when depth==1 for compatibility."""
        out = ray.get(self.base_sandbox.ls.remote(path, depth))
        if isinstance(out, dict) and not out.get("tree_format") and "items" in out:
            return out["items"]
        return out
    
    def exists(self, path: str) -> bool:
        return ray.get(self.base_sandbox.exists.remote(path))
    
    def stat(self, path: str) -> dict[str, Any]:
        return ray.get(self.base_sandbox.stat.remote(path))
    
    def read_text(self, path: str, offset: Optional[int] = None, limit: Optional[int] = None, encoding: str = "utf-8") -> dict:
        return ray.get(self.base_sandbox.read_text.remote(path, offset, limit, encoding))
    
    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> dict:
        """Enhanced write_text with automatic LSP diagnostics"""
        result = ray.get(self.base_sandbox.write_text.remote(path, content, encoding))
        
        # Trigger LSP diagnostics collection
        full_path = os.path.join(self.workspace, path) if not os.path.isabs(path) else path
        ray.get(self.lsp_manager.touch_file.remote(full_path))
        
        # Add diagnostics to result
        try:
            diagnostics = ray.get(self.lsp_manager.diagnostics.remote())
            result["lsp_diagnostics"] = diagnostics.get(full_path, [])
        except Exception:
            result["lsp_diagnostics"] = []
            
        return result
    
    def edit_replace(self, path: str, old: str, new: str, count: int = 1, encoding: str = "utf-8") -> dict:
        """Enhanced edit_replace with automatic LSP diagnostics"""
        result = ray.get(self.base_sandbox.edit_replace.remote(path, old, new, count, encoding))
        
        # Trigger LSP diagnostics collection
        full_path = os.path.join(self.workspace, path) if not os.path.isabs(path) else path
        ray.get(self.lsp_manager.touch_file.remote(full_path))
        
        # Add diagnostics to result
        try:
            diagnostics = ray.get(self.lsp_manager.diagnostics.remote())
            result["lsp_diagnostics"] = diagnostics.get(full_path, [])
        except Exception:
            result["lsp_diagnostics"] = []
            
        return result
    
    def multiedit(self, edits: list[dict], encoding: str = "utf-8") -> dict:
        """Enhanced multiedit with automatic LSP diagnostics"""
        result = ray.get(self.base_sandbox.multiedit.remote(edits, encoding))
        
        # Trigger LSP diagnostics for all edited files
        all_diagnostics = {}
        for edit in edits:
            path = edit.get("path", "")
            full_path = os.path.join(self.workspace, path) if not os.path.isabs(path) else path
            ray.get(self.lsp_manager.touch_file.remote(full_path))
        
        # Collect diagnostics for all files
        try:
            diagnostics = ray.get(self.lsp_manager.diagnostics.remote())
            all_diagnostics = diagnostics
        except Exception:
            pass
            
        result["lsp_diagnostics"] = all_diagnostics
        return result
    
    def run(self, cmd: str, timeout: Optional[int] = None, stdin_data: Optional[str] = None, 
            env: Optional[dict[str, str]] = None, stream: bool = True, shell: bool = True):
        """Forward run command to base sandbox"""
        return ray.get(self.base_sandbox.run.remote(cmd, timeout, stdin_data, env, stream, shell))
    
    def get_session_id(self) -> str:
        return ray.get(self.base_sandbox.get_session_id.remote())
    
    def get_workspace(self) -> str:
        return ray.get(self.base_sandbox.get_workspace.remote())
    
    def glob(self, pattern: str, root: str = ".", limit: Optional[int] = None) -> list[str]:
        return ray.get(self.base_sandbox.glob.remote(pattern, root, limit))
    
    def list_files(self, root: str = ".", include_hidden: bool = False, recursive: bool = True, limit: int = 1000) -> list[str]:
        return ray.get(self.base_sandbox.list_files.remote(root, include_hidden, recursive, limit))
    
    def grep(self, pattern: str, path: str = ".", include: Optional[str] = None, limit: int = 100) -> dict:
        return ray.get(self.base_sandbox.grep.remote(pattern, path, include, limit))
    
    def vcs(self, request: dict[str, Any]) -> dict[str, Any]:
        return ray.get(self.base_sandbox.vcs.remote(request))
    
    # New LSP-enhanced methods
    def lsp_diagnostics(self, file_path: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        """Get LSP diagnostics for specific file or all touched files"""
        if file_path:
            full_path = os.path.join(self.workspace, file_path) if not os.path.isabs(file_path) else file_path
            ray.get(self.lsp_manager.touch_file.remote(full_path))
        
        return ray.get(self.lsp_manager.diagnostics.remote())
    
    def lsp_hover(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Get hover information at specific position"""
        full_path = os.path.join(self.workspace, file_path) if not os.path.isabs(file_path) else file_path
        return ray.get(self.lsp_manager.hover.remote(full_path, line, character))
    
    def lsp_go_to_definition(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Go to definition from specific position"""
        full_path = os.path.join(self.workspace, file_path) if not os.path.isabs(file_path) else file_path
        return ray.get(self.lsp_manager.go_to_definition.remote(full_path, line, character))
    
    def lsp_find_references(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Find references from specific position"""
        full_path = os.path.join(self.workspace, file_path) if not os.path.isabs(file_path) else file_path
        return ray.get(self.lsp_manager.find_references.remote(full_path, line, character))
    
    def lsp_workspace_symbols(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search workspace symbols"""
        return ray.get(self.lsp_manager.workspace_symbol.remote(query, limit))
    
    def lsp_document_symbols(self, file_path: str) -> List[Dict[str, Any]]:
        """Get symbols in specific document"""
        full_path = os.path.join(self.workspace, file_path) if not os.path.isabs(file_path) else file_path
        return ray.get(self.lsp_manager.document_symbol.remote(full_path))
    
    def lsp_format_document(self, file_path: str) -> Dict[str, Any]:
        """Format document via LSP"""
        full_path = os.path.join(self.workspace, file_path) if not os.path.isabs(file_path) else file_path
        return ray.get(self.lsp_manager.format_document.remote(full_path))
    
    def lsp_code_actions(self, file_path: str, start_line: int, start_char: int, 
                        end_line: int, end_char: int, diagnostics: List[Dict] = None) -> Dict[str, Any]:
        """Get available code actions for range"""
        full_path = os.path.join(self.workspace, file_path) if not os.path.isabs(file_path) else file_path
        return ray.get(self.lsp_manager.code_actions.remote(
            full_path, start_line, start_char, end_line, end_char, diagnostics
        ))
    
    def lsp_completion(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Get code completion suggestions"""
        full_path = os.path.join(self.workspace, file_path) if not os.path.isabs(file_path) else file_path
        return ray.get(self.lsp_manager.completion.remote(full_path, line, character))
    
    def shutdown(self):
        """Shutdown both sandbox and LSP manager"""
        try:
            ray.get(self.lsp_manager.shutdown.remote())
        except Exception:
            pass


class LSPSandboxFactory:
    """Factory for creating LSP-enhanced sandboxes"""
    
    @staticmethod
    def create_enhanced_sandbox(image: str, workspace: str, session_id: Optional[str] = None, 
                               lsp_actor: Optional[ray.actor.ActorHandle] = None) -> ray.ObjectRef:
        """
        Create an LSP-enhanced sandbox that's compatible with existing agent_session.py
        """
        from sandbox_v2 import DevSandboxV2
        
        # Create base sandbox
        base_sandbox = DevSandboxV2.remote(image, session_id, workspace, lsp_actor)
        
        # Wrap with LSP enhancement
        enhanced_sandbox = LSPEnhancedSandbox.remote(base_sandbox, workspace)
        
        return enhanced_sandbox


# Tool definitions for agent integration
LSP_TOOL_DEFINITIONS = [
    {
        "name": "lsp_hover",
        "description": "Get hover information (documentation, type info) at a specific position in a file",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"},
                "line": {"type": "integer", "description": "Line number (0-based)"},
                "character": {"type": "integer", "description": "Character position (0-based)"}
            },
            "required": ["file_path", "line", "character"]
        }
    },
    {
        "name": "lsp_go_to_definition",
        "description": "Find the definition of a symbol at a specific position",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"},
                "line": {"type": "integer", "description": "Line number (0-based)"},
                "character": {"type": "integer", "description": "Character position (0-based)"}
            },
            "required": ["file_path", "line", "character"]
        }
    },
    {
        "name": "lsp_find_references",
        "description": "Find all references to a symbol at a specific position",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"},
                "line": {"type": "integer", "description": "Line number (0-based)"},
                "character": {"type": "integer", "description": "Character position (0-based)"}
            },
            "required": ["file_path", "line", "character"]
        }
    },
    {
        "name": "lsp_workspace_symbols",
        "description": "Search for symbols across the entire workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Symbol search query"},
                "limit": {"type": "integer", "description": "Maximum number of results", "default": 50}
            },
            "required": ["query"]
        }
    },
    {
        "name": "lsp_document_symbols",
        "description": "Get all symbols (functions, classes, variables) in a specific document",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "lsp_format_document",
        "description": "Format a document using the appropriate language formatter",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to format"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "lsp_code_actions",
        "description": "Get available code actions (quick fixes, refactoring) for a code range",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"},
                "start_line": {"type": "integer", "description": "Start line number (0-based)"},
                "start_char": {"type": "integer", "description": "Start character position (0-based)"},
                "end_line": {"type": "integer", "description": "End line number (0-based)"},
                "end_char": {"type": "integer", "description": "End character position (0-based)"}
            },
            "required": ["file_path", "start_line", "start_char", "end_line", "end_char"]
        }
    },
    {
        "name": "lsp_completion",
        "description": "Get code completion suggestions at a specific position",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file"},
                "line": {"type": "integer", "description": "Line number (0-based)"},
                "character": {"type": "integer", "description": "Character position (0-based)"}
            },
            "required": ["file_path", "line", "character"]
        }
    }
]


def integrate_lsp_with_agent_session(cls):
    """
    Decorator to integrate LSP tools with existing agent session without modifying the original class.
    Usage: @integrate_lsp_with_agent_session
    """
    # Store original _execute_tool method
    original_execute_tool = cls._execute_tool
        
    def enhanced_execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Enhanced tool execution with LSP support"""
        
        # Handle LSP tools
        if name == "lsp_hover":
            result = self.sandbox.lsp_hover.remote(args["file_path"], args["line"], args["character"])
            return {"output": ray.get(result), "metadata": {"type": "lsp_hover"}}
            
        elif name == "lsp_go_to_definition":
            result = self.sandbox.lsp_go_to_definition.remote(args["file_path"], args["line"], args["character"])
            return {"output": ray.get(result), "metadata": {"type": "lsp_definition"}}
            
        elif name == "lsp_find_references":
            result = self.sandbox.lsp_find_references.remote(args["file_path"], args["line"], args["character"])
            return {"output": ray.get(result), "metadata": {"type": "lsp_references"}}
            
        elif name == "lsp_workspace_symbols":
            limit = args.get("limit", 50)
            result = self.sandbox.lsp_workspace_symbols.remote(args["query"], limit)
            return {"output": ray.get(result), "metadata": {"type": "lsp_symbols"}}
            
        elif name == "lsp_document_symbols":
            result = self.sandbox.lsp_document_symbols.remote(args["file_path"])
            return {"output": ray.get(result), "metadata": {"type": "lsp_document_symbols"}}
            
        elif name == "lsp_format_document":
            result = self.sandbox.lsp_format_document.remote(args["file_path"])
            return {"output": ray.get(result), "metadata": {"type": "lsp_format"}}
            
        elif name == "lsp_code_actions":
            result = self.sandbox.lsp_code_actions.remote(
                args["file_path"], args["start_line"], args["start_char"], 
                args["end_line"], args["end_char"], args.get("diagnostics")
            )
            return {"output": ray.get(result), "metadata": {"type": "lsp_code_actions"}}
            
        elif name == "lsp_completion":
            result = self.sandbox.lsp_completion.remote(args["file_path"], args["line"], args["character"])
            return {"output": ray.get(result), "metadata": {"type": "lsp_completion"}}
        
        # Fall back to original tool execution
        else:
            return original_execute_tool(self, name, args)
    
    # Replace the method
    cls._execute_tool = enhanced_execute_tool
    return cls