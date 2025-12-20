"""
Validation handling for tool calls and constraints
"""

from typing import Any, Dict, List, Optional


class ValidationHandler:
    """Handles validation of tool calls and constraint enforcement"""
    
    def __init__(self, config_validator=None):
        self.config_validator = config_validator
    
    def validate_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Validate tool calls against design decision constraints"""
        if not self.config_validator:
            return None
        
        validation_result = self.config_validator.validate_tool_calls(tool_calls)
        
        # Check for validation errors (critical constraints)
        if not validation_result["valid"]:
            return {
                "valid": False,
                "errors": validation_result["errors"],
                "validation_failed": True
            }
        
        return {"valid": True, "errors": []}
    
    def check_completion_isolation(self, parsed_calls: List[Any]) -> Optional[str]:
        """Check for mark_task_complete isolation constraint"""
        completion_calls = [p for p in parsed_calls if p.function == "mark_task_complete"]
        if completion_calls and len(parsed_calls) > 1:
            # Mark_task_complete must be isolated - reject other operations
            return f"mark_task_complete() cannot be combined with other operations. Found {len(parsed_calls)} total calls including: {[p.function for p in parsed_calls]}"
        return None
    
    def check_bash_constraint(self, parsed_calls: List[Any], bash_executed: bool = False) -> tuple:
        """Check bash constraint (only one bash command per turn)"""
        bash_calls = [p for p in parsed_calls if p.function == "run_shell"]
        
        if len(bash_calls) > 1:
            return False, "Only one bash command allowed per turn (research constraint)"
        
        if bash_calls and bash_executed:
            return False, "Only one bash command allowed per turn (already executed)"
        
        return True, None
    
    def validate_turn_strategy(self, parsed_calls: List[Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate turn strategy constraints"""
        turn_cfg = config.get("turn_strategy", {})
        allow_multi = bool(turn_cfg.get("allow_multiple_per_turn", False))
        
        if len(parsed_calls) > 1 and not allow_multi:
            return {
                "valid": False,
                "reason": "Multiple tool calls per turn not allowed by configuration",
                "suggested_action": "Enable allow_multiple_per_turn or use single tool calls"
            }
        
        return {"valid": True}
    
    def get_nonblocking_tools(self, config: Dict[str, Any]) -> set:
        """Get set of nonblocking tool names from configuration"""
        conc_cfg = config.get("concurrency", {})
        return set(conc_cfg.get("nonblocking_tools", [])) or set([
            'apply_unified_patch', 'apply_search_replace', 'create_file_from_block',
            'read', 'read_file', 'glob', 'grep', 'list', 'list_dir', 'patch'
        ])
    
    def validate_concurrency_strategy(self, parsed_calls: List[Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate concurrency strategy for tool calls"""
        nonblocking_tools = self.get_nonblocking_tools(config)
        
        nb_calls = [p for p in parsed_calls if p.function in nonblocking_tools]
        other_calls = [p for p in parsed_calls if p.function not in nonblocking_tools]
        
        # Check if mixing blocking and nonblocking tools
        if nb_calls and other_calls:
            return {
                "valid": True,  # Allow but prefer separation
                "warning": "Mixing blocking and nonblocking tools may affect performance",
                "strategy": "sequential"
            }
        
        if len(nb_calls) > 1:
            return {
                "valid": True,
                "strategy": "concurrent",
                "tool_count": len(nb_calls)
            }
        
        return {
            "valid": True,
            "strategy": "sequential"
        }