"""
LSP Validation Rule for Enhanced Tool Calling System
Integrates with existing sequence_validator.py framework
"""

import asyncio
from typing import Dict, Any, List, Optional
import logging

from ..execution.sequence_validator import ValidationRule
from ..integration.lsp_manager import RemoteLSPManager, Diagnostic

logger = logging.getLogger(__name__)

class LSPValidationRule(ValidationRule):
    """
    LSP validation rule that integrates with the existing validation framework.
    Validates code changes using language servers and provides feedback.
    """
    
    def __init__(self, workspace_root: str, enabled: bool = True, max_errors_shown: int = 3):
        config = {"enabled": enabled, "workspace_root": workspace_root, "max_errors_shown": max_errors_shown}
        super().__init__(config)
        self.workspace_root = workspace_root
        self.max_errors_shown = max_errors_shown
        self.lsp_manager: Optional[RemoteLSPManager] = None
    
    @property
    def rule_id(self) -> str:
        return "lsp_validation"
    
    @property
    def description(self) -> str:
        return "Validates code using Language Server Protocol for syntax and semantic errors"
    
    def _ensure_lsp_manager(self):
        """Lazy initialization of remote LSP manager"""
        if self.lsp_manager is None:
            self.lsp_manager = RemoteLSPManager.remote(self.workspace_root)
    
    def validate(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
        """
        Validate tool call using LSP diagnostics.
        Integrates with existing validation framework patterns.
        
        Note: This is a synchronous interface, so we do basic validation only.
        Full LSP validation happens in post-execution phase.
        """
        if not self.enabled:
            return None
        
        try:
            # For the sync validation interface, we only do basic checks
            # Full LSP validation happens in the post-execution phase
            op_type = tool_call.get("function", "")
            file_path = tool_call.get("arguments", {}).get("path", "") or \
                       tool_call.get("arguments", {}).get("file_path", "") or \
                       tool_call.get("arguments", {}).get("file_name", "")
            
            if not file_path:
                return None
            
            # Only validate code operations
            if op_type not in ["write", "create_file", "create_file_from_block", "apply_unified_patch", "apply_search_replace"]:
                return None
            
            # Basic file extension check
            if not self._is_supported_file(file_path):
                return None  # Skip validation for unsupported file types
            
            # At this point, we've passed basic validation
            # Detailed LSP validation will happen in post-execution
            return None
            
        except Exception as e:
            logger.warning(f"LSP validation error: {e}")
            return None  # Non-blocking: don't fail the operation if LSP validation fails
    
    def _is_supported_file(self, file_path: str) -> bool:
        """Check if file type is supported by LSP"""
        supported_extensions = [
            ".py", ".pyi",                  # Python
            ".ts", ".tsx", ".js", ".jsx",   # TypeScript/JavaScript
            ".rs",                          # Rust
            ".go",                          # Go
            ".c", ".cpp", ".cc", ".h", ".hpp"  # C/C++
        ]
        return any(file_path.endswith(ext) for ext in supported_extensions)

class PostExecutionLSPValidator:
    """
    Post-execution LSP validator for enhanced tool executor.
    Provides the "Patch Completed with 0 linter errors" style feedback.
    """
    
    def __init__(self, workspace_root: str, enabled: bool = True):
        self.workspace_root = workspace_root
        self.enabled = enabled
        self.lsp_manager: Optional[RemoteLSPManager] = None
    
    async def _ensure_lsp_manager(self):
        """Lazy initialization of remote LSP manager"""
        if self.lsp_manager is None:
            self.lsp_manager = RemoteLSPManager.remote(self.workspace_root)
    
    async def validate_post_execution(self, file_path: str, operation_type: str) -> str:
        """
        Validate file after execution and return feedback message.
        Returns messages like "Patch Completed with 0 linter errors" or brief error summary.
        """
        if not self.enabled:
            return f"{operation_type.capitalize()} completed"
        
        try:
            await self._ensure_lsp_manager()
            
            # Validate the file
            import ray
            diagnostics_data = await ray.get(
                self.lsp_manager.validate_file.remote(file_path, None)  # Read from file
            )
            
            # Format for AI model feedback
            brief_summary = await ray.get(
                self.lsp_manager.format_diagnostics_brief.remote(diagnostics_data, 3)
            )
            
            if brief_summary == "0 linter errors":
                return f"{operation_type.capitalize()} completed with 0 linter errors"
            else:
                return f"{operation_type.capitalize()} completed with issues:\n{brief_summary}"
                
        except Exception as e:
            logger.warning(f"Post-execution LSP validation failed: {e}")
            return f"{operation_type.capitalize()} completed (LSP validation unavailable)"
    
    async def validate_multiple_files(self, file_paths: List[str], operation_type: str) -> str:
        """Validate multiple files and provide aggregate feedback"""
        if not self.enabled or not file_paths:
            return f"{operation_type.capitalize()} completed"
        
        try:
            await self._ensure_lsp_manager()
            
            all_diagnostics = []
            import ray
            
            # Validate all files
            for file_path in file_paths:
                diagnostics_data = await ray.get(
                    self.lsp_manager.validate_file.remote(file_path, None)
                )
                all_diagnostics.extend(diagnostics_data)
            
            # Format aggregate summary
            brief_summary = await ray.get(
                self.lsp_manager.format_diagnostics_brief.remote(all_diagnostics, 5)
            )
            
            file_count = len(file_paths)
            if brief_summary == "0 linter errors":
                return f"{operation_type.capitalize()} completed on {file_count} file(s) with 0 linter errors"
            else:
                return f"{operation_type.capitalize()} completed on {file_count} file(s) with issues:\n{brief_summary}"
                
        except Exception as e:
            logger.warning(f"Multi-file LSP validation failed: {e}")
            return f"{operation_type.capitalize()} completed on {len(file_paths)} file(s) (LSP validation unavailable)"
    
    async def shutdown(self):
        """Shutdown the LSP manager"""
        if self.lsp_manager:
            import ray
            await ray.get(self.lsp_manager.shutdown.remote())