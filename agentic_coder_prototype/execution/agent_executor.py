"""
Agent-level tool execution coordination and policy enforcement
"""

import concurrent.futures
import threading
from collections import defaultdict
from typing import Any, Dict, List, Optional, Callable, Set

from .concurrency_validator import (
    ConcurrencyConfigError,
    validate_concurrency_config,
)


class AgentToolExecutor:
    """Coordinates tool execution with policy enforcement and concurrency management"""
    
    def __init__(self, config: Dict[str, Any], workspace: str):
        self.config = config or {}
        self.workspace = workspace
        self.enhanced_executor = None
        self.config_validator = None
        self.alias_map: Dict[str, str] = {}
        self.nonblocking_names: Set[str] = set()
        self.at_most_one_of: Set[str] = set()
        self.barrier_tools: Set[str] = set()
        self.tool_to_group: Dict[str, Dict[str, Any]] = {}
        self.concurrency_groups: List[Dict[str, Any]] = []
        self.allow_multiple_bash: bool = False

        raw_concurrency = (self.config.get("concurrency") if isinstance(self.config, dict) else None)
        try:
            self._validated_concurrency = validate_concurrency_config(raw_concurrency)
        except ConcurrencyConfigError as exc:
            raise ValueError(f"Invalid concurrency configuration: {exc}") from exc

        self._load_concurrency_config()
    
    def set_enhanced_executor(self, enhanced_executor):
        """Set the enhanced executor for LSP integration"""
        self.enhanced_executor = enhanced_executor
    
    def set_config_validator(self, config_validator):
        """Set the configuration validator for design decision constraints"""
        self.config_validator = config_validator

    def _canonical_tool_name(self, name: str) -> str:
        if not name:
            return ""
        current = str(name)
        visited: Set[str] = set()
        while current in self.alias_map and current not in visited:
            visited.add(current)
            current = self.alias_map[current]
        return current

    def canonical_tool_name(self, name: str) -> str:
        """Expose canonical name resolution for callers that need alias awareness"""
        return self._canonical_tool_name(name)

    def _load_concurrency_config(self) -> None:
        tools_cfg = (self.config.get("tools", {}) or {})
        raw_aliases = {str(k): str(v) for k, v in (tools_cfg.get("aliases") or {}).items()}
        self.alias_map = raw_aliases

        conc_cfg = dict(self._validated_concurrency)

        nb_override = conc_cfg.get("nonblocking_tools", []) or []
        default_nb = [
            'apply_unified_patch', 'apply_search_replace', 'create_file_from_block',
            'read', 'read_file', 'glob', 'grep', 'list', 'list_dir', 'patch'
        ]
        source_nb = nb_override if nb_override else default_nb
        self.nonblocking_names = {self._canonical_tool_name(str(n)) for n in source_nb if n}

        self.at_most_one_of = {self._canonical_tool_name(str(n)) for n in conc_cfg.get("at_most_one_of", []) or [] if n}

        self.barrier_tools = set()
        self.tool_to_group = {}
        self.concurrency_groups = []

        for raw_group in conc_cfg.get("groups", []) or []:
            match_tools = {self._canonical_tool_name(str(t)) for t in raw_group.get("match_tools", []) or [] if t}
            max_parallel = int(raw_group.get("max_parallel", 1) or 1)
            if max_parallel < 1:
                max_parallel = 1
            barrier_after = raw_group.get("barrier_after")
            barrier_canonical = None
            if barrier_after:
                barrier_canonical = self._canonical_tool_name(str(barrier_after))
                self.barrier_tools.add(barrier_canonical)
            group = {
                "name": raw_group.get("name") or "",
                "match_tools": match_tools,
                "max_parallel": max_parallel,
                "barrier_after": barrier_canonical,
                "_id": raw_group.get("name") or "|".join(sorted(match_tools)) or f"group_{len(self.concurrency_groups)}",
            }
            self.concurrency_groups.append(group)
            for tool_name in match_tools:
                self.tool_to_group[tool_name] = group

    def is_tool_failure(self, tool_name: str, result: Dict[str, Any]) -> bool:
        """Check if a tool call failed based on its result"""
        # Check for explicit error
        if result.get("error"):
            return True
        
        # Check for explicit failure flag
        if result.get("ok") is False:
            return True
        
        # Check for bash command failure (non-zero exit code)
        if tool_name == "run_shell" and result.get("exit", 0) != 0:
            return True
        
        return False
    
    def validate_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Validate tool calls against design decision constraints"""
        if not self.config_validator:
            return None
        
        validation_result = self.config_validator.validate_tool_calls(tool_calls)
        
        # Check for validation errors (critical constraints)
        if not validation_result["valid"]:
            errors = validation_result.get("errors") or []
            error_message = "Tool execution validation failed:\n" + "\n".join(errors)
            return {
                "error": error_message,
                "errors": errors,
                "validation_failed": True,
            }
        
        return None
    
    def check_completion_isolation(self, parsed_calls: List[Any]) -> Optional[str]:
        """Check for mark_task_complete isolation constraint"""
        completion_calls = [p for p in parsed_calls if p.function == "mark_task_complete"]
        if completion_calls and len(parsed_calls) > 1:
            # Mark_task_complete must be isolated - reject other operations
            return f"mark_task_complete() cannot be combined with other operations. Found {len(parsed_calls)} total calls including: {[p.function for p in parsed_calls]}"
        return None

    def check_at_most_one_of(self, parsed_calls: List[Any]) -> Optional[str]:
        """Enforce at_most_one_of concurrency constraint"""
        if not self.at_most_one_of:
            return None

        present: List[str] = []
        for p in parsed_calls:
            tool_name = self._canonical_tool_name(getattr(p, "function", ""))
            if tool_name in self.at_most_one_of:
                present.append(tool_name)

        if len(present) <= 1:
            return None

        counts: Dict[str, int] = {}
        for name in present:
            counts[name] = counts.get(name, 0) + 1

        details = ", ".join([f"{name} x{count}" for name, count in counts.items()])
        return f"Only one of {sorted(self.at_most_one_of)} may be called per turn; saw {len(present)} calls ({details})."

    def reorder_operations(self, parsed_calls: List[Any]) -> List[Any]:
        """Reorder operations to prioritize file operations before bash commands"""
        file_ops = [p for p in parsed_calls if p.function in ['create_file', 'create_file_from_block', 'apply_unified_patch', 'apply_search_replace']]
        other_ops = [p for p in parsed_calls if p.function not in ['create_file', 'create_file_from_block', 'apply_unified_patch', 'apply_search_replace']]
        return file_ops + other_ops

    def determine_execution_strategy(self, parsed_calls: List[Any]) -> Dict[str, Any]:
        """Determine execution strategy based on tool types and configuration"""
        barrier_triggered = False
        nonblocking_calls: List[Any] = []

        group_counts_all: Dict[str, int] = defaultdict(int)
        group_limits_all: Dict[str, int] = {}

        for call in parsed_calls:
            tool_name = self._canonical_tool_name(getattr(call, "function", ""))
            group = self.tool_to_group.get(tool_name)
            if group:
                gid = group.get("_id") or str(id(group))
                group_counts_all[gid] += 1
                limit = int(group.get("max_parallel", 1))
                if limit < 1:
                    limit = 1
                group_limits_all[gid] = limit

            if tool_name in self.barrier_tools or (group and group.get("barrier_after") == tool_name):
                barrier_triggered = True

            if group and int(group.get("max_parallel", 1)) <= 1:
                return {
                    "calls_to_execute": parsed_calls,
                    "strategy": "sequential",
                    "can_run_concurrent": False,
                    "group_counts": dict(group_counts_all),
                    "group_limits": dict(group_limits_all),
                }

            is_nonblocking = tool_name in self.nonblocking_names or (group and int(group.get("max_parallel", 1)) > 1)
            if is_nonblocking:
                nonblocking_calls.append(call)
            else:
                return {
                    "calls_to_execute": parsed_calls,
                    "strategy": "sequential",
                    "can_run_concurrent": False,
                    "max_workers": 1,
                }

        if barrier_triggered or len(nonblocking_calls) <= 1:
            return {
                "calls_to_execute": parsed_calls,
                "strategy": "sequential",
                "can_run_concurrent": False,
                "max_workers": 1,
                "group_counts": dict(group_counts_all),
                "group_limits": dict(group_limits_all),
            }

        # Determine effective max workers respecting group limits
        per_group_counts: Dict[str, int] = defaultdict(int)
        per_group_limits: Dict[str, int] = {}
        neutral_calls = 0

        for call in nonblocking_calls:
            tool_name = self._canonical_tool_name(getattr(call, "function", ""))
            group = self.tool_to_group.get(tool_name)
            if group:
                group_id = group.get("_id") or str(id(group))
                per_group_counts[group_id] += 1
                limit = int(group.get("max_parallel", 1))
                if limit < 1:
                    limit = 1
                per_group_limits[group_id] = limit
            else:
                neutral_calls += 1

        concurrency_budget = neutral_calls
        for group_id, count in per_group_counts.items():
            limit = per_group_limits.get(group_id, 1)
            concurrency_budget += min(count, max(1, limit))

        if concurrency_budget <= 1:
            return {
                "calls_to_execute": parsed_calls,
                "strategy": "sequential",
                "can_run_concurrent": False,
                "max_workers": 1,
                "group_counts": dict(group_counts_all),
                "group_limits": dict(group_limits_all),
            }

        max_workers = min(len(nonblocking_calls), concurrency_budget, 8)
        max_workers = min(len(nonblocking_calls), concurrency_budget, 8)

        return {
            "calls_to_execute": nonblocking_calls,
            "strategy": "nonblocking_concurrent",
            "can_run_concurrent": True,
            "max_workers": max_workers,
            "group_limits": per_group_limits,
            "group_counts": dict(per_group_counts) or dict(group_counts_all),
        }

    def execute_tool_call(self, tool_call: Dict[str, Any], exec_func: Callable) -> Dict[str, Any]:
        """Execute a single tool call with enhanced executor support"""
        if self.enhanced_executor:
            try:
                import asyncio
                
                # Create async wrapper for exec function
                async def async_exec(tc):
                    result = exec_func(tc)
                    if asyncio.iscoroutine(result):
                        return await result
                    return result
                
                # Try to get existing event loop, create new one if needed
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_closed():
                        raise RuntimeError("Loop is closed")
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                result = loop.run_until_complete(
                    self.enhanced_executor.execute_tool_call(tool_call, async_exec)
                )
                return result
            except Exception:
                # Fallback to raw execution on enhanced executor failure
                pass
        
        # Raw execution (original implementation)
        return exec_func(tool_call)
    
    def execute_calls_concurrent(
        self, 
        calls_to_execute: List[Any], 
        exec_func: Callable,
        transcript_callback: Callable = None,
        max_workers: Optional[int] = None
    ) -> List[tuple]:
        """Execute calls concurrently for nonblocking operations"""
        group_controls: Dict[str, threading.Semaphore] = {}
        group_controls_lock = threading.Lock()

        def _acquire_group_control(group_name: Optional[str], limit: int):
            if not group_name:
                return None
            with group_controls_lock:
                semaphore = group_controls.get(group_name)
                if semaphore is None:
                    semaphore = threading.Semaphore(limit)
                    group_controls[group_name] = semaphore
                return semaphore

        def _run_single_call(p):
            try:
                tool_call = {
                    "function": p.function,
                    "arguments": p.arguments,
                    "provider_name": getattr(p, "provider_name", None),
                    "expected_output": getattr(p, "expected_output", None),
                }
                tool_name = self._canonical_tool_name(p.function)
                group = self.tool_to_group.get(tool_name)
                semaphore = None
                if group:
                    limit = int(group.get("max_parallel", 1))
                    if limit < 1:
                        limit = 1
                    group_key = group.get("_id") or str(id(group))
                    semaphore = _acquire_group_control(group_key, limit)
                if semaphore:
                    semaphore.acquire()
                try:
                    out = self.execute_tool_call(tool_call, exec_func)
                finally:
                    if semaphore:
                        semaphore.release()
            except Exception as e:
                out = {"error": str(e), "function": p.function}
            return (p, out)

        executed_results = []
        failed_at_index = -1
        
        try:
            max_workers = max_workers or min(8, len(calls_to_execute))
            max_workers = max(1, min(max_workers, len(calls_to_execute)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [pool.submit(_run_single_call, p) for p in calls_to_execute]
                for fut in futures:
                    p, out = fut.result()
                    if transcript_callback:
                        transcript_callback({"tool": p.function, "result": out})
                    executed_results.append((p, out))
                    if self.is_tool_failure(p.function, out) and failed_at_index < 0:
                        failed_at_index = 0
        except Exception:
            # Fallback to sequential if thread pool execution fails
            executed_results, failed_at_index = self.execute_calls_sequential(
                calls_to_execute, exec_func, transcript_callback
            )
        
        return executed_results, failed_at_index
    
    def execute_calls_sequential(
        self, 
        calls_to_execute: List[Any], 
        exec_func: Callable,
        transcript_callback: Callable = None
    ) -> tuple:
        """Execute calls sequentially with bash constraint enforcement"""
        executed_results = []
        failed_at_index = -1
        bash_executed = False  # Track bash constraint (critical research finding)
        
        enforce_bash_limit = not getattr(self, "allow_multiple_bash", False)
        for i, p in enumerate(calls_to_execute):
            if enforce_bash_limit and p.function == "run_shell":
                if bash_executed:
                    out = {"error": "Only one bash command allowed per turn (policy)", "skipped": True}
                    executed_results.append((p, out))
                    continue
                bash_executed = True
            
            try:
                tool_call = {
                    "function": p.function,
                    "arguments": p.arguments,
                    "provider_name": getattr(p, "provider_name", None),
                    "expected_output": getattr(p, "expected_output", None),
                }
                out = self.execute_tool_call(tool_call, exec_func)
            except Exception as e:
                out = {"error": str(e), "function": p.function}
            
            if transcript_callback:
                transcript_callback({"tool": p.function, "result": out})
            executed_results.append((p, out))
            
            if self.is_tool_failure(p.function, out):
                failed_at_index = i
                break
        
        return executed_results, failed_at_index
    
    def execute_parsed_calls(
        self, 
        parsed_calls: List[Any], 
        exec_func: Callable,
        transcript_callback: Callable = None,
        *,
        policy_bypass: bool = False,
    ) -> tuple:
        """Execute parsed tool calls with full policy enforcement"""
        # Normalize tool names via alias map for downstream enforcement/execution
        normalized_calls: List[Any] = []
        for p in parsed_calls:
            tool_name = getattr(p, "function", "")
            canonical = self._canonical_tool_name(tool_name)
            if canonical and canonical != tool_name:
                try:
                    setattr(p, "function", canonical)
                except Exception:
                    pass
            normalized_calls.append(p)

        # Validate tool calls
        tool_calls_for_validation = [{"name": p.function, "args": p.arguments} for p in normalized_calls]
        if not policy_bypass:
            validation_error = self.validate_tool_calls(tool_calls_for_validation)
            if validation_error:
                meta = {
                    "group_counts": {},
                    "group_limits": {},
                    "max_workers": 1,
                    "total_calls": len(normalized_calls),
                    "executed_calls": 0,
                }
                return [], -1, validation_error, meta

        # Check completion isolation
        if not policy_bypass:
            isolation_error = self.check_completion_isolation(normalized_calls)
            if isolation_error:
                meta = {
                    "strategy": "constraint_violation",
                    "can_run_concurrent": False,
                    "group_counts": {},
                    "group_limits": {},
                    "max_workers": 1,
                    "total_calls": len(normalized_calls),
                    "executed_calls": 0,
                }
                return [], -1, {"error": isolation_error, "constraint_violation": True}, meta

        if not policy_bypass:
            amo_error = self.check_at_most_one_of(normalized_calls)
            if amo_error:
                meta = {
                    "strategy": "constraint_violation",
                    "can_run_concurrent": False,
                    "group_counts": {},
                    "group_limits": {},
                    "max_workers": 1,
                    "total_calls": len(normalized_calls),
                    "executed_calls": 0,
                }
                return [], -1, {"error": amo_error, "constraint_violation": True}, meta

        # Reorder operations for optimal execution
        reordered_calls = self.reorder_operations(normalized_calls)

        # Determine execution strategy
        strategy = self.determine_execution_strategy(reordered_calls)
        calls_to_execute = strategy["calls_to_execute"]
        plan_metadata = {
            "strategy": strategy.get("strategy", "sequential"),
            "can_run_concurrent": bool(strategy.get("can_run_concurrent")),
            "max_workers": strategy.get("max_workers", 1),
            "group_counts": strategy.get("group_counts", {}),
            "group_limits": strategy.get("group_limits", {}),
            "total_calls": len(normalized_calls),
            "executed_calls": len(calls_to_execute),
        }

        # Execute calls based on strategy
        if strategy["can_run_concurrent"]:
            executed_results, failed_at_index = self.execute_calls_concurrent(
                calls_to_execute, exec_func, transcript_callback, strategy.get("max_workers")
            )
        else:
            executed_results, failed_at_index = self.execute_calls_sequential(
                calls_to_execute, exec_func, transcript_callback
            )

        return executed_results, failed_at_index, None, plan_metadata
