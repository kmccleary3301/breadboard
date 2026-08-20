from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..ctrees.branch_receipt_contract import branch_receipt_executor_enabled, build_branch_receipt_contract
from ..ctrees.branch_receipt_hooks import Phase17BranchReceiptHookManager
from ..ctrees.executor_contract import build_executor_contract, executor_enabled
from ..ctrees.executor_hooks import Phase14ExecutorHookManager
from ..ctrees.finish_closure_contract import build_finish_closure_contract, finish_closure_executor_enabled
from ..ctrees.finish_closure_hooks import Phase18FinishClosureHookManager
from ..ctrees.invocation_first_contract import build_invocation_first_contract, invocation_first_executor_enabled
from ..ctrees.invocation_first_hooks import Phase16InvocationFirstHookManager
from ..ctrees.verifier_executor_contract import build_verifier_executor_contract, verifier_executor_enabled
from ..ctrees.verifier_executor_hooks import Phase15VerifierExecutorHookManager

def build_hook_manager(config: Dict[str, Any], workspace: str, *, plugin_manifests: Optional[List[Any]] = None) -> Any:
    del workspace
    del plugin_manifests
    if finish_closure_executor_enabled(config):
        return Phase18FinishClosureHookManager(build_finish_closure_contract(config))
    if branch_receipt_executor_enabled(config):
        return Phase17BranchReceiptHookManager(build_branch_receipt_contract(config))
    if invocation_first_executor_enabled(config):
        return Phase16InvocationFirstHookManager(build_invocation_first_contract(config))
    if verifier_executor_enabled(config):
        return Phase15VerifierExecutorHookManager(build_verifier_executor_contract(config))
    if executor_enabled(config):
        return Phase14ExecutorHookManager(build_executor_contract(config))
    return None
