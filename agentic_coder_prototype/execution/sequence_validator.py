"""
Sequence Validator - Framework for validating tool call sequences
Provides pluggable validation rules to prevent problematic tool call patterns
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Set
from pathlib import Path
import re
import time

logger = logging.getLogger(__name__)

class ValidationRule(ABC):
    """Base class for validation rules"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        Initialize validation rule
        
        Args:
            config: Optional configuration for the rule
        """
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
    
    @abstractmethod
    def validate(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
        """
        Validate a tool call
        
        Args:
            tool_call: The tool call to validate
            context: Workspace and session context
            
        Returns:
            Error message if invalid, None if valid
        """
        pass
    
    @property
    @abstractmethod
    def rule_id(self) -> str:
        """Unique identifier for this rule"""
        pass
    
    @property
    def description(self) -> str:
        """Human-readable description of what this rule checks"""
        return f"Validation rule: {self.rule_id}"

class NoRedundantCreationRule(ValidationRule):
    """Prevent creating files that already exist"""
    
    @property
    def rule_id(self) -> str:
        return "no_redundant_creation"
    
    @property
    def description(self) -> str:
        return "Prevents creating files that were already created in this session"
    
    def validate(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
        if not self.enabled:
            return None
            
        function = tool_call.get("function", "")
        if function not in ["create_file", "create_file_from_block", "write_file"]:
            return None
        
        # Extract target file path
        args = tool_call.get("arguments", {})
        target_file = args.get("path") or args.get("file_path") or args.get("file_name", "")
        
        if not target_file:
            return None
        
        # Check against session state
        created_files = context.get("files_created_this_session", [])
        if target_file in created_files:
            return f"Redundant file creation: {target_file} already created this session. Use edit tools instead."
        
        return None

class ReadBeforeEditRule(ValidationRule):
    """Ensure files are read before being edited"""
    
    @property
    def rule_id(self) -> str:
        return "read_before_edit"
    
    @property
    def description(self) -> str:
        return "Ensures files are read before editing to understand their content"
    
    def validate(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
        if not self.enabled:
            return None

        function = tool_call.get("function", "")
        if function not in ["apply_unified_patch", "apply_search_replace"]:
            return None

        target_files = self._extract_target_files(tool_call)
        if not target_files:
            return None

        read_files = set(context.get("files_read_this_session", []))
        created_files = set(context.get("files_created_this_session", []))
        last_read_map = context.get("files_last_read_at", {}) or {}
        workspace_root = context.get("workspace_root")
        strictness = str(self.config.get("mode", self.config.get("strictness", "warn"))).lower()
        require_fresh = bool(self.config.get("require_fresh_read", False))
        max_age_raw = self.config.get("max_age_seconds")
        max_age = float(max_age_raw) if isinstance(max_age_raw, (int, float)) else None

        for file_path in target_files:
            if file_path in created_files:
                continue

            timestamp = self._lookup_last_read(file_path, last_read_map)
            if timestamp is None and file_path not in read_files:
                if strictness == "error":
                    return f"File {file_path} must be read with read_file before editing."
                logger.warning(f"File {file_path} is being edited without being read first")
                continue

            if timestamp is not None:
                if max_age is not None:
                    age = time.time() - timestamp
                    if age > max_age:
                        if strictness == "error":
                            return f"File {file_path} was last read {int(age)}s ago. Read it again before editing."
                        logger.warning(f"File {file_path} read timestamp is stale ({int(age)}s); consider re-reading before editing.")
                        continue
                if require_fresh:
                    try:
                        candidate_path = Path(workspace_root) / file_path if workspace_root else Path(file_path)
                        if candidate_path.exists():
                            if candidate_path.stat().st_mtime > timestamp:
                                if strictness == "error":
                                    return f"File {file_path} has changed since it was last read. Read it again before editing."
                                logger.warning(f"File {file_path} mtime exceeds last read timestamp.")
                        else:
                            if strictness == "error":
                                return f"File {file_path} is missing; read the latest state before editing."
                            logger.warning(f"File {file_path} missing when enforcing read-before-edit guard.")
                    except Exception:
                        # Best-effort mtime check; swallow errors to avoid crashing validation.
                        pass

        return None
    
    def _extract_target_files(self, tool_call: Dict[str, Any]) -> List[str]:
        """Extract target files from edit operations"""
        function = tool_call.get("function", "")
        args = tool_call.get("arguments", {})
        
        if function == "apply_search_replace":
            file_name = args.get("file_name", "")
            return [file_name] if file_name else []
        
        elif function == "apply_unified_patch":
            patch_content = args.get("patch", "")
            return self._extract_files_from_patch(patch_content)
        
        return []
    
    def _extract_files_from_patch(self, patch_content: str) -> List[str]:
        """Extract file paths from patch content"""
        files = []
        for line in patch_content.split('\n'):
            if line.startswith('--- ') or line.startswith('+++ '):
                parts = line.split('\t')[0].split(' ', 1)
                if len(parts) > 1:
                    file_path = parts[1]
                    if file_path.startswith(('a/', 'b/')):
                        file_path = file_path[2:]
                    if file_path != '/dev/null':
                        files.append(file_path)
        return list(set(files))

    def _lookup_last_read(self, file_path: str, last_read_map: Dict[str, Any]) -> Optional[float]:
        candidates = [file_path]
        if file_path.startswith("./"):
            candidates.append(file_path[2:])
        else:
            candidates.append(f"./{file_path}")
        if file_path.startswith(("a/", "b/")):
            candidates.append(file_path[2:])
        for candidate in candidates:
            if candidate in last_read_map:
                try:
                    return float(last_read_map[candidate])
                except Exception:
                    continue
        return None

class NoMixedDialectRule(ValidationRule):
    """Prevent mixing conflicting dialects in rapid succession"""
    
    @property
    def rule_id(self) -> str:
        return "no_mixed_dialect"
    
    @property
    def description(self) -> str:
        return "Prevents mixing conflicting tool dialects that can cause confusion"
    
    def validate(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
        if not self.enabled:
            return None
        
        # This rule would analyze recent operations to detect dialect mixing
        # For now, we'll implement basic detection
        recent_ops = context.get("recent_operations", [])
        if not recent_ops:
            return None
        
        current_function = tool_call.get("function", "")
        
        # Check for file creation conflicts
        if current_function in ["create_file", "create_file_from_block"]:
            # Look for recent patch operations on same files
            target_files = self._get_target_files(tool_call)
            for op in recent_ops[-3:]:  # Check last 3 operations
                if op.get("function") == "apply_unified_patch":
                    # This could indicate mixed dialect usage
                    op_files = op.get("files_affected", [])
                    if any(f in op_files for f in target_files):
                        logger.warning(f"Potential mixed dialect usage detected: "
                                     f"patch operation followed by file creation")
        
        return None
    
    def _get_target_files(self, tool_call: Dict[str, Any]) -> List[str]:
        """Get target files from tool call"""
        args = tool_call.get("arguments", {})
        return [args.get("path", "") or args.get("file_path", "") or args.get("file_name", "")]

class FileExistsRule(ValidationRule):
    """Check file existence for operations that require existing files"""
    
    def __init__(self, config: Dict[str, Any] = None, workspace_root: str = "."):
        super().__init__(config)
        self.workspace_root = Path(workspace_root).resolve()
    
    @property
    def rule_id(self) -> str:
        return "file_exists"
    
    @property
    def description(self) -> str:
        return "Validates that files exist when required for operations"
    
    def validate(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
        if not self.enabled:
            return None
            
        function = tool_call.get("function", "")
        
        # Operations that require existing files
        if function in ["read_file", "apply_search_replace", "apply_unified_patch"]:
            target_files = self._get_operation_files(tool_call)
            
            for file_path in target_files:
                if not file_path:
                    continue
                    
                # Check session state first
                created_files = context.get("files_created_this_session", [])
                if file_path in created_files:
                    continue  # File exists in session
                
                # Check filesystem
                full_path = self.workspace_root / file_path
                if not full_path.exists():
                    return f"File {file_path} does not exist and is required for {function}"
        
        return None
    
    def _get_operation_files(self, tool_call: Dict[str, Any]) -> List[str]:
        """Extract files from various operation types"""
        args = tool_call.get("arguments", {})
        function = tool_call.get("function", "")
        
        files = []
        
        # Direct file path arguments
        for key in ["path", "file_path", "file_name", "target_file"]:
            if key in args and args[key]:
                files.append(args[key])
        
        # Extract from patches
        if function == "apply_unified_patch" and "patch" in args:
            patch_files = self._extract_patch_files(args["patch"])
            files.extend(patch_files)
        
        return files
    
    def _extract_patch_files(self, patch_content: str) -> List[str]:
        """Extract existing files from patch (exclude /dev/null)"""
        files = []
        for line in patch_content.split('\n'):
            if line.startswith('--- '):
                parts = line.split('\t')[0].split(' ', 1)
                if len(parts) > 1:
                    file_path = parts[1]
                    if file_path.startswith('a/'):
                        file_path = file_path[2:]
                    if file_path != '/dev/null':
                        files.append(file_path)
        return files

class OneBashPerTurnRule(ValidationRule):
    """Limit to one bash command per turn (best-effort, based on recent ops).

    Note: Without an explicit turn boundary marker, we approximate a "turn"
    using a short time window and immediate history. This protects against
    repeated bash calls in rapid succession within the same assistant reply.
    """

    @property
    def rule_id(self) -> str:
        return "one_bash_per_turn"

    @property
    def description(self) -> str:
        return "Enforces at most one run_shell per assistant turn (approximate)"

    def validate(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
        if not self.enabled:
            return None

        if tool_call.get("function", "") != "run_shell":
            return None

        recent_ops = context.get("recent_operations", []) or []
        # Configurable time window (seconds) to approximate a turn
        window_s = float(self.config.get("turn_window_seconds", 8.0))
        # Allow explicit opt-out for certain scenarios
        if self.config.get("disabled", False):
            return None

        # If the immediately previous operation in recent history is a bash run,
        # we reject as likely the same turn.
        if recent_ops:
            last = recent_ops[-1]
            if isinstance(last, dict) and last.get("function") == "run_shell":
                return "Only one bash command allowed per turn"

        # Additionally, scan the short recent history for very close-in-time bash
        try:
            import time
            now = time.time()
            for op in reversed(recent_ops[-3:]):
                if not isinstance(op, dict):
                    continue
                if op.get("function") != "run_shell":
                    continue
                ts = float(op.get("timestamp", now))
                if (now - ts) <= window_s:
                    return "Only one bash command allowed per turn"
        except Exception:
            # Best-effort only; if context lacks timestamps, fall back to single-previous-op check only
            pass

        return None

class ReadsBeforeBashRule(ValidationRule):
    """Encourage reading files before running bash when both are in recent plan."""

    @property
    def rule_id(self) -> str:
        return "reads_before_bash"

    @property
    def description(self) -> str:
        return "Encourages read_file operations before run_shell in a turn"

    def validate(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
        if not self.enabled:
            return None
        if tool_call.get("function") != "run_shell":
            return None
        # If we have modified or created files but never read them, and bash is executed,
        # suggest to read first. This is a soft constraint (warn -> error configurable).
        strictness = self.config.get("strictness", "warn")
        read_files = set(context.get("files_read_this_session", []))
        modified_files = set(context.get("files_modified_this_session", []))
        created_files = set(context.get("files_created_this_session", []))
        touched = modified_files.union(created_files)
        if touched and not read_files:
            if strictness == "error":
                return "Prefer reading relevant files before running bash"
        return None

class SequenceValidator:
    """Validates tool call sequences using pluggable rules"""
    
    def __init__(self, enabled_rules: List[str], config: Dict[str, Any] = None, workspace_root: str = "."):
        """
        Initialize sequence validator
        
        Args:
            enabled_rules: List of rule IDs to enable
            config: Optional configuration for rules
            workspace_root: Workspace root directory for file operations
        """
        self.config = config or {}
        self.workspace_root = workspace_root
        
        # Initialize all available rules
        self.available_rules = {
            "no_redundant_creation": NoRedundantCreationRule(
                self.config.get("no_redundant_creation", {})
            ),
            "read_before_edit": ReadBeforeEditRule(
                self.config.get("read_before_edit", {})
            ),
            "no_mixed_dialect": NoMixedDialectRule(
                self.config.get("no_mixed_dialect", {})
            ),
            "file_exists": FileExistsRule(
                self.config.get("file_exists", {}), 
                workspace_root
            ),
            "one_bash_per_turn": OneBashPerTurnRule(
                self.config.get("one_bash_per_turn", {})
            ),
            "reads_before_bash": ReadsBeforeBashRule(
                self.config.get("reads_before_bash", {})
            ),
            "lsp_validation": self._create_lsp_validation_rule(
                self.config.get("lsp_validation", {})
            )
        }
        
        # Enable only requested rules
        self.active_rules = []
        for rule_id in enabled_rules:
            if rule_id in self.available_rules:
                rule = self.available_rules[rule_id]
                rule.enabled = True
                self.active_rules.append(rule)
                logger.debug(f"Enabled validation rule: {rule_id}")
            else:
                logger.warning(f"Unknown validation rule: {rule_id}")
        
        logger.info(f"Initialized SequenceValidator with {len(self.active_rules)} rules")
    
    def validate_call(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
        """
        Validate single tool call against all active rules
        
        Args:
            tool_call: Tool call to validate
            context: Workspace and session context
            
        Returns:
            Error message if any rule fails, None if all rules pass
        """
        for rule in self.active_rules:
            try:
                error = rule.validate(tool_call, context)
                if error:
                    logger.debug(f"Rule {rule.rule_id} failed: {error}")
                    return error
            except Exception as e:
                logger.error(f"Error in validation rule {rule.rule_id}: {e}")
                # Continue with other rules rather than failing completely
        
        return None  # All rules passed
    
    def get_active_rules(self) -> List[str]:
        """Get list of active rule IDs"""
        return [rule.rule_id for rule in self.active_rules]
    
    def get_rule_descriptions(self) -> Dict[str, str]:
        """Get descriptions of all active rules"""
        return {rule.rule_id: rule.description for rule in self.active_rules}
    
    def disable_rule(self, rule_id: str) -> bool:
        """Disable a specific rule"""
        for rule in self.active_rules:
            if rule.rule_id == rule_id:
                rule.enabled = False
                logger.info(f"Disabled validation rule: {rule_id}")
                return True
        return False
    
    def enable_rule(self, rule_id: str) -> bool:
        """Enable a specific rule"""
        for rule in self.active_rules:
            if rule.rule_id == rule_id:
                rule.enabled = True
                logger.info(f"Enabled validation rule: {rule_id}")
                return True
        return False
    
    def _create_lsp_validation_rule(self, config: Dict[str, Any]):
        """Create LSP validation rule with lazy import to avoid circular dependencies"""
        try:
            from ..integration.lsp_validation import LSPValidationRule
            return LSPValidationRule(
                workspace_root=config.get("workspace_root", self.workspace_root),
                enabled=config.get("enabled", True),
                max_errors_shown=config.get("max_errors_shown", 3)
            )
        except ImportError as e:
            logger.warning(f"LSP validation rule not available: {e}")
            # Return a no-op rule if LSP is not available
            return NoOpValidationRule(config)

class NoOpValidationRule(ValidationRule):
    """No-op validation rule for when LSP is not available"""
    
    @property
    def rule_id(self) -> str:
        return "lsp_validation"
    
    @property 
    def description(self) -> str:
        return "LSP validation (not available)"
    
    def validate(self, tool_call: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
        return None  # Always passes
