"""
Bootstrap functions for conductor initialization.

This module extracts workspace setup, sandbox initialization, replay session loading,
and component initialization orchestration from the conductor's __init__ method.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ray

from breadboard.sandbox_v2 import DevSandboxV2, new_dev_sandbox_v2
from breadboard.sandbox_virtualized import SandboxFactory, DeploymentMode
from .execution.dialect_manager import DialectManager
from .execution.agent_executor import AgentToolExecutor
from .messaging.message_formatter import MessageFormatter
from .monitoring.reward_metrics import RewardMetricsSQLiteWriter, TodoMetricsSQLiteWriter
from .logging_v2 import LoggerV2Manager
from .logging_v2.api_recorder import APIRequestRecorder
from .logging_v2.prompt_logger import PromptArtifactLogger
from .logging_v2.markdown_transcript import MarkdownTranscriptWriter
from .logging_v2.provider_native_logger import ProviderNativeLogger
from .logging_v2.request_recorder import StructuredRequestRecorder
from .utils.local_ray import LocalActorProxy, identity_get
from .provider_health import RouteHealthManager
from .provider_metrics import ProviderMetricsCollector
from .provider_capability_probe import ProviderCapabilityProbeRunner
from .provider_routing import provider_router
from .provider_runtime import provider_registry
from .guardrail_coordinator import GuardrailCoordinator
from .guardrail_orchestrator import GuardrailOrchestrator
from .plan_bootstrapper import PlanBootstrapper
from .permission_broker import PermissionBroker
from .loop_detection import LoopDetectionService
from .context_window_guard import ContextWindowGuard
from .streaming_policy import StreamingPolicy
from .provider_invoker import ProviderInvoker
from .tool_prompt_planner import ToolPromptPlanner
from .turn_relayer import TurnRelayer
from .replay import ReplaySession, load_replay_session
from .conductor_components import (
    initialize_guardrail_config,
    initialize_yaml_tools,
    initialize_config_validator,
    initialize_enhanced_executor,
    write_env_fingerprint,
)


def prepare_workspace(workspace: str) -> Path:
    """Ensure the workspace directory exists and is empty before use."""
    import shutil
    
    try:
        path = Path(workspace)
    except Exception:
        path = Path(str(workspace))
    try:
        path = path if path.is_absolute() else (Path.cwd() / path)
        path = path.resolve()
    except Exception:
        # Fallback: use absolute() best-effort without strict resolution
        try:
            path = path.absolute()
        except Exception:
            pass
    preserve_seeded = os.environ.get("PRESERVE_SEEDED_WORKSPACE") in {"1", "true", "True"}
    try:
        if path.exists() and not preserve_seeded:
            shutil.rmtree(path)
    except Exception:
        # If cleanup fails we still attempt to proceed with a fresh directory
        pass
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_workspace(workspace: str, config: Optional[Dict[str, Any]]) -> str:
    """Resolve workspace path from config or use provided workspace."""
    cfg_ws = None
    if config:
        try:
            cfg_ws = (config.get("workspace", {}) or {}).get("root")
        except Exception:
            cfg_ws = None
    effective_ws = cfg_ws or workspace
    effective_ws = str(prepare_workspace(effective_ws))
    return effective_ws


def setup_sandbox(
    conductor: Any,
    workspace: str,
    image: str,
    config: Optional[Dict[str, Any]],
    local_mode: bool,
) -> None:
    """Initialize sandbox based on virtualization mode."""
    sandbox_cfg = ((config or {}).get("workspace", {}) or {}).get("sandbox", {}) or {}
    sandbox_driver = str(sandbox_cfg.get("driver") or "process").strip().lower()
    sandbox_options = dict(sandbox_cfg.get("options") or {}) if isinstance(sandbox_cfg, dict) else {}
    mirror_cfg = ((config or {}).get("workspace", {}) or {}).get("mirror", {})
    mirror_mode = str(mirror_cfg.get("mode", "development")).lower()
    use_virtualized = mirror_cfg.get("enabled", True)
    
    print("WORKSPACE", workspace)
    print("MIRROR CFG", mirror_cfg)
    
    conductor.local_mode = bool(local_mode)
    conductor._ray_get = ray.get if not conductor.local_mode else identity_get
    
    conductor.using_virtualized = bool(use_virtualized) and not conductor.local_mode
    if conductor.using_virtualized:
        try:
            mode = DeploymentMode(mirror_mode) if mirror_mode in (m.value for m in DeploymentMode) else DeploymentMode.DEVELOPMENT
        except Exception:
            mode = DeploymentMode.DEVELOPMENT
        factory = SandboxFactory()
        vsb, session_id = factory.create_sandbox(
            mode,
            {
                "runtime": {"image": image},
                "workspace": workspace,
                "sandbox": {"driver": sandbox_driver, "options": sandbox_options},
            },
        )
        conductor.sandbox = vsb
    elif conductor.local_mode:
        dev_cls = DevSandboxV2.__ray_metadata__.modified_class
        dev_impl = dev_cls(image=image, session_id=f"local-{uuid.uuid4()}", workspace=workspace, lsp_actor=None)
        conductor.sandbox = LocalActorProxy(dev_impl)
    else:
        conductor.sandbox = new_dev_sandbox_v2(
            image,
            workspace,
            name=f"oa-sb-{uuid.uuid4()}",
            driver=sandbox_driver,
            driver_options=sandbox_options,
        )


def load_replay_session_data(conductor: Any, config: Optional[Dict[str, Any]]) -> None:
    """Load replay session data if configured."""
    conductor._replay_session_data = None
    conductor._active_replay_session: Optional[ReplaySession] = None
    replay_cfg = dict((config or {}).get("replay") or {})
    session_path = replay_cfg.get("session_path")
    if session_path:
        session_path = str(session_path)
        session_file = Path(session_path)
        conductor._replay_session_data = load_replay_session(session_file)
        print(f"[replay] Loaded session: {session_file}")


def initialize_guardrail_attributes(
    conductor: Any,
    zero_tool_warn_message: str,
    zero_tool_abort_message: str,
    completion_guard_abort_threshold: int,
) -> None:
    """Initialize guardrail-related attributes."""
    conductor.guardrail_manager = None
    conductor.workspace_guard_handler = None
    conductor.todo_rate_guard_handler = None
    conductor.zero_tool_warn_message = zero_tool_warn_message
    conductor.zero_tool_abort_message = zero_tool_abort_message
    conductor.completion_guard_abort_threshold = completion_guard_abort_threshold
    conductor.completion_guard_handler = None
    conductor.shell_write_guard_handler = None
    initialize_guardrail_config(
        conductor,
        default_warn_turns=2,  # ZERO_TOOL_WARN_TURNS
        default_abort_turns=4,  # ZERO_TOOL_ABORT_TURNS
        default_warn_message=zero_tool_warn_message,
        default_abort_message=zero_tool_abort_message,
    )


def initialize_core_components(conductor: Any) -> None:
    """Initialize core components (dialect manager, tools, validators, executors)."""
    conductor.dialect_manager = DialectManager(conductor.config)
    initialize_yaml_tools(conductor)
    initialize_config_validator(conductor)
    initialize_enhanced_executor(conductor)


def initialize_logging_components(conductor: Any) -> None:
    """Initialize logging v2 components."""
    conductor.logger_v2 = LoggerV2Manager(conductor.config)
    try:
        # Use only workspace basename for logging session id to avoid path tokens
        run_dir = conductor.logger_v2.start_run(session_id=os.path.basename(os.path.normpath(conductor.workspace)))
    except Exception:
        run_dir = ""
    if run_dir:
        try:
            write_env_fingerprint(conductor)
        except Exception:
            pass
    
    conductor.api_recorder = APIRequestRecorder(conductor.logger_v2)
    conductor.structured_request_recorder = StructuredRequestRecorder(conductor.logger_v2)
    conductor.prompt_logger = PromptArtifactLogger(conductor.logger_v2)
    conductor.md_writer = MarkdownTranscriptWriter()
    conductor.provider_logger = ProviderNativeLogger(conductor.logger_v2)
    conductor.capability_probe_runner = ProviderCapabilityProbeRunner(
        provider_router,
        provider_registry,
        conductor.logger_v2,
        None,
    )
    conductor._capability_probes_ran = False


def initialize_provider_components(conductor: Any) -> None:
    """Initialize provider-related components."""
    conductor.route_health = RouteHealthManager()
    conductor._current_route_id: Optional[str] = None
    conductor.provider_metrics = ProviderMetricsCollector()
    conductor._reward_metrics_sqlite: Optional[RewardMetricsSQLiteWriter] = None
    conductor._todo_metrics_sqlite: Optional[TodoMetricsSQLiteWriter] = None


def initialize_execution_components(conductor: Any) -> None:
    """Initialize execution-related components."""
    conductor.permission_broker = PermissionBroker(conductor.config.get("permissions"))
    conductor.loop_detector = LoopDetectionService()
    conductor.context_guard = ContextWindowGuard()
    conductor.message_formatter = MessageFormatter(conductor.workspace)
    conductor.agent_executor = AgentToolExecutor(conductor.config, conductor.workspace)
    conductor.agent_executor.set_enhanced_executor(conductor.enhanced_executor)
    conductor.agent_executor.set_config_validator(conductor.config_validator)


def initialize_orchestration_components(conductor: Any) -> None:
    """Initialize orchestration components (plan bootstrapper, streaming, providers, guardrails)."""
    conductor._last_runtime_latency: Optional[float] = None
    conductor._last_html_detected: bool = False
    conductor._prompt_hashes: Dict[str, Any] = {"system": None, "per_turn": {}}
    conductor._turn_diagnostics: List[Any] = []
    conductor.plan_bootstrapper = PlanBootstrapper(conductor.config)
    conductor.streaming_policy = StreamingPolicy(conductor.provider_metrics)
    conductor.provider_invoker = ProviderInvoker(
        provider_metrics=conductor.provider_metrics,
        route_health=conductor.route_health,
        logger_v2=conductor.logger_v2,
        md_writer=conductor.md_writer,
        retry_with_fallback=conductor._retry_with_fallback,
        update_health_metadata=conductor._update_health_metadata,
        set_last_latency=lambda value: setattr(conductor, "_last_runtime_latency", value),
        set_html_detected=lambda flag: setattr(conductor, "_last_html_detected", flag),
    )
    conductor.guardrail_coordinator = GuardrailCoordinator(conductor.config)
    conductor.tool_prompt_planner = ToolPromptPlanner()
    conductor.turn_relayer = TurnRelayer(conductor.logger_v2, conductor.md_writer)
    # Phase 8 multi-agent orchestration (lazy init)
    conductor._multi_agent_orchestrator = None
    conductor._multi_agent_event_log_path = None
    conductor.guardrail_orchestrator = GuardrailOrchestrator(
        conductor.config,
        conductor.guardrail_coordinator,
        conductor.plan_bootstrapper,
        completion_guard_abort_threshold=conductor.completion_guard_abort_threshold,
        completion_guard_handler=conductor.completion_guard_handler,
        zero_tool_warn_turns=conductor.zero_tool_warn_turns,
        zero_tool_abort_turns=conductor.zero_tool_abort_turns,
        zero_tool_warn_message=conductor.zero_tool_warn_message,
        zero_tool_abort_message=conductor.zero_tool_abort_message,
        zero_tool_emit_event=conductor.zero_tool_emit_event,
    )


def bootstrap_conductor(
    conductor: Any,
    workspace: str,
    image: str,
    config: Optional[Dict[str, Any]],
    local_mode: bool,
    zero_tool_warn_message: str,
    zero_tool_abort_message: str,
    completion_guard_abort_threshold: int,
) -> None:
    """
    Bootstrap conductor initialization.
    
    This function orchestrates all conductor initialization steps:
    1. Workspace resolution and sandbox setup
    2. Replay session loading
    3. Guardrail configuration
    4. Core component initialization
    5. Logging component initialization
    6. Provider component initialization
    7. Execution component initialization
    8. Orchestration component initialization
    """
    # Resolve workspace
    effective_ws = resolve_workspace(workspace, config)
    
    # Setup sandbox
    setup_sandbox(conductor, effective_ws, image, config, local_mode)
    
    # Set basic attributes
    conductor.workspace = effective_ws
    conductor.image = image
    conductor.config = config or {}
    
    # Load replay session
    load_replay_session_data(conductor, config)
    
    # Initialize guardrail attributes
    initialize_guardrail_attributes(
        conductor,
        zero_tool_warn_message,
        zero_tool_abort_message,
        completion_guard_abort_threshold,
    )
    
    # Initialize core components
    initialize_core_components(conductor)
    
    # Initialize logging components
    initialize_logging_components(conductor)
    
    # Initialize provider components
    initialize_provider_components(conductor)
    
    # Initialize execution components
    initialize_execution_components(conductor)
    
    # Initialize orchestration components
    initialize_orchestration_components(conductor)
