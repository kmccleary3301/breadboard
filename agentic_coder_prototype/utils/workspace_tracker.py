"""
Workspace State Tracker - Tracks file operations to prevent redundancy
Maintains session state of file creations and modifications
"""
from __future__ import annotations

import logging
import time
import json
import re
from typing import Dict, Set, List, Any, Optional
from pathlib import Path
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

@dataclass
class Operation:
    """Record of a tool operation"""
    function: str
    timestamp: float
    files_affected: List[str]
    success: bool
    arguments: Dict[str, Any]
    result_summary: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class WorkspaceStateTracker:
    """Tracks file operations to prevent redundancy and provide context"""
    
    def __init__(self, workspace_root: str):
        """
        Initialize workspace tracker
        
        Args:
            workspace_root: Root directory of the workspace
        """
        self.workspace_root = Path(workspace_root).resolve()
        self.created_files: Set[str] = set()
        self.modified_files: Set[str] = set()
        self.read_files: Set[str] = set()
        self.read_timestamps: Dict[str, float] = {}
        self.operation_history: List[Operation] = []
        self.session_start = time.time()
        
        logger.info(f"Initialized WorkspaceStateTracker for {self.workspace_root}")
    
    def track_operation(self, tool_call: Dict[str, Any], result: Dict[str, Any]) -> None:
        """
        Track completed tool operations
        
        Args:
            tool_call: The tool call that was executed
            result: Result of the tool execution
        """
        function = tool_call.get("function", "unknown")
        arguments = tool_call.get("arguments", {})
        try:
            success = (isinstance(result, dict) and ("error" not in result))
        except Exception:
            success = True
        files_affected = self._extract_files_from_call(tool_call)
        
        # Create operation record
        operation = Operation(
            function=function,
            timestamp=time.time(),
            files_affected=files_affected,
            success=success,
            arguments=arguments,
            result_summary=self._summarize_result(result if isinstance(result, dict) else {})
        )
        
        # Update file tracking if operation succeeded
        if success:
            now_ts = time.time()
            for file_path in files_affected:
                normalized_path = self._normalize_path(file_path)

                if function in ["create_file", "create_file_from_block", "write"]:
                    self.created_files.add(normalized_path)
                    self.read_timestamps[normalized_path] = now_ts
                    logger.debug(f"Tracked file creation: {normalized_path}")

                if function in ["apply_unified_patch", "apply_search_replace", "write_file", "write"]:
                    self.modified_files.add(normalized_path)
                    self.read_timestamps[normalized_path] = now_ts
                    logger.debug(f"Tracked file modification: {normalized_path}")

                elif function in ["read_file"]:
                    self.read_files.add(normalized_path)
                    self.read_timestamps[normalized_path] = now_ts
                    logger.debug(f"Tracked file read: {normalized_path}")
        
        self.operation_history.append(operation)
        
        # Keep history bounded
        if len(self.operation_history) > 100:
            self.operation_history = self.operation_history[-50:]
            
        logger.debug(f"Tracked operation: {function} on {files_affected} (success={success})")
    
    def validate_operation(self, tool_call: Dict[str, Any]) -> Optional[str]:
        """
        Validate operation against workspace state
        
        Args:
            tool_call: Tool call to validate
            
        Returns:
            Error message if invalid, None if valid
        """
        function = tool_call.get("function", "")
        files_affected = self._extract_files_from_call(tool_call)
        
        for file_path in files_affected:
            normalized_path = self._normalize_path(file_path)
            
            # Check for redundant file creation
            if function in ["create_file", "create_file_from_block", "write"]:
                if function != "write" and normalized_path in self.created_files:
                    return f"File {file_path} already created this session. Use edit tools instead."

                if function != "write":
                    full_path = self.workspace_root / file_path
                    if full_path.exists():
                        return f"File {file_path} already exists. Use edit tools instead."
            
            # Validate edit operations have target files
            elif function in ["apply_unified_patch", "apply_search_replace"]:
                if normalized_path not in self.created_files and normalized_path not in self.modified_files:
                    full_path = self.workspace_root / file_path
                    if not full_path.exists():
                        return f"Cannot edit {file_path} - file does not exist. Create it first."
        
        return None  # Valid operation
    
    def get_context_for_prompt(self) -> Dict[str, Any]:
        """
        Generate context for tool prompts
        
        Returns:
            Dict with workspace state information for prompt generation
        """
        recent_ops = self.operation_history[-5:] if self.operation_history else []
        
        context = {
            "workspace_root": str(self.workspace_root),
            "session_duration_minutes": round((time.time() - self.session_start) / 60, 1),
            "files_created_this_session": sorted(list(self.created_files)),
            "files_modified_this_session": sorted(list(self.modified_files)),
            "files_read_this_session": sorted(list(self.read_files)),
            "total_operations": len(self.operation_history),
            "recent_operations": [op.to_dict() for op in recent_ops],
            "file_operation_summary": self._get_file_operation_summary(),
            "files_last_read_at": self._serialize_read_timestamps()
        }
        
        return context
    
    def _extract_files_from_call(self, tool_call: Dict[str, Any]) -> List[str]:
        """Extract file paths from a tool call"""
        function = tool_call.get("function", "")
        arguments = tool_call.get("arguments", {})
        files = []
        
        # Direct file path arguments
        if "path" in arguments:
            files.append(arguments["path"])
        if "file_path" in arguments:
            files.append(arguments["file_path"])
        if "filePath" in arguments:
            files.append(arguments["filePath"])
        if "file_name" in arguments:
            files.append(arguments["file_name"])
        if "target_file" in arguments:
            files.append(arguments["target_file"])
        
        # Extract from patches
        if function == "apply_unified_patch" and "patch" in arguments:
            files.extend(self._extract_files_from_patch(arguments["patch"]))
        
        # Extract from search-replace operations
        if function == "apply_search_replace" and "file_name" in arguments:
            files.append(arguments["file_name"])
        
        return files
    
    def _extract_files_from_patch(self, patch_content: str) -> List[str]:
        """Extract file paths from unified diff patch"""
        files = []
        
        # Standard unified diff format: --- a/file +++ b/file
        for line in patch_content.split('\n'):
            if line.startswith('--- ') or line.startswith('+++ '):
                # Extract filename, handle various formats
                parts = line.split('\t')[0].split(' ', 1)
                if len(parts) > 1:
                    file_path = parts[1]
                    # Remove a/ or b/ prefix if present
                    if file_path.startswith(('a/', 'b/')):
                        file_path = file_path[2:]
                    if file_path != '/dev/null':
                        files.append(file_path)
        
        return list(set(files))  # Remove duplicates
    
    def _normalize_path(self, file_path: str) -> str:
        """Normalize file path relative to workspace root"""
        try:
            path = Path(file_path)
            if path.is_absolute():
                # Make relative to workspace root
                return str(path.relative_to(self.workspace_root))
            else:
                return str(path)
        except (ValueError, OSError):
            # If path manipulation fails, return as-is
            return file_path
    
    def _summarize_result(self, result: Dict[str, Any]) -> str:
        """Create a brief summary of operation result"""
        if "error" in result:
            return f"Error: {str(result['error'])[:100]}"
        elif "status" in result:
            return f"Status: {result['status']}"
        elif "stdout" in result:
            stdout = str(result['stdout'])
            return f"Output: {stdout[:100]}{'...' if len(stdout) > 100 else ''}"
        else:
            return "Success"
    
    def _get_file_operation_summary(self) -> Dict[str, int]:
        """Get summary statistics of file operations"""
        return {
            "files_created": len(self.created_files),
            "files_modified": len(self.modified_files),
            "files_read": len(self.read_files),
            "total_file_operations": sum(
                1 for op in self.operation_history 
                if op.function in [
                    "create_file", "create_file_from_block", "write_file",
                    "apply_unified_patch", "apply_search_replace", "read_file"
                ]
            )
        }
    
    def get_operation_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent operation history"""
        recent = self.operation_history[-limit:] if self.operation_history else []
        return [op.to_dict() for op in recent]
    
    def clear_session_state(self) -> None:
        """Clear all tracked state (for new sessions)"""
        self.created_files.clear()
        self.modified_files.clear()
        self.read_files.clear()
        self.read_timestamps.clear()
        self.operation_history.clear()
        self.session_start = time.time()
        logger.info("Cleared workspace tracker state")
    
    def export_state(self) -> Dict[str, Any]:
        """Export current state for persistence"""
        return {
            "workspace_root": str(self.workspace_root),
            "created_files": list(self.created_files),
            "modified_files": list(self.modified_files),
            "read_files": list(self.read_files),
            "read_timestamps": dict(self.read_timestamps),
            "operation_history": [op.to_dict() for op in self.operation_history],
            "session_start": self.session_start
        }
    
    def import_state(self, state: Dict[str, Any]) -> None:
        """Import previously exported state"""
        self.created_files = set(state.get("created_files", []))
        self.modified_files = set(state.get("modified_files", []))
        self.read_files = set(state.get("read_files", []))
        self.read_timestamps = {self._normalize_path(k): float(v) for k, v in state.get("read_timestamps", {}).items()}
        self.session_start = state.get("session_start", time.time())
        
        # Reconstruct operation history
        self.operation_history = []
        for op_dict in state.get("operation_history", []):
            op = Operation(**op_dict)
            self.operation_history.append(op)
        
        logger.info(f"Imported workspace state with {len(self.operation_history)} operations")

    def require_recent_read(self, file_path: str, *, max_age_seconds: Optional[float] = None) -> Optional[str]:
        """Ensure a file was read (or written) recently enough before editing."""
        normalized = self._normalize_path(file_path)
        last_read = self.read_timestamps.get(normalized)
        if last_read is None:
            return f"File {file_path} must be read with read_file before editing."

        if max_age_seconds is not None:
            age = time.time() - last_read
            if age > max_age_seconds:
                return f"File {file_path} was last read {int(age)}s ago. Re-read it before editing to ensure you have the latest content."

        try:
            full_path = self.workspace_root / normalized
            if full_path.exists():
                mtime = full_path.stat().st_mtime
                if mtime > last_read:
                    return f"File {file_path} has changed since it was last read. Read it again before editing."
            else:
                return f"File {file_path} no longer exists. Read the latest state before editing."
        except Exception:
            # Conservative default: if we cannot compute mtime, allow edit but warn via validator.
            return None

        return None

    def _serialize_read_timestamps(self) -> Dict[str, float]:
        """Return read timestamps in export-friendly form."""
        return {path: float(ts) for path, ts in self.read_timestamps.items()}
