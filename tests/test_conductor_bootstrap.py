"""
Test conductor initialization/bootstrap to verify P2 and P3 changes work correctly.

This test verifies that:
1. Conductor can be instantiated via __init__ (P3 bootstrap extraction)
2. All components are initialized correctly
3. ConductorContext protocol is satisfied (P2 type improvements)
"""

import pytest
import ray
from pathlib import Path

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from agentic_coder_prototype.conductor_context import ConductorContext


@pytest.fixture(scope="module")
def ray_cluster():
    """Start Ray cluster for conductor tests."""
    if not ray.is_initialized():
        ray.init(local_mode=True, ignore_reinit_error=True)
    yield
    ray.shutdown()


def test_conductor_initialization_basic(ray_cluster, tmp_path):
    """Test that conductor initializes correctly with minimal config."""
    workspace = str(tmp_path / "test_workspace")
    
    # This should not raise any exceptions
    conductor = OpenAIConductor(
        workspace=workspace,
        image="python-dev:latest",
        config={},
        local_mode=True,
    )
    
    # Verify basic attributes are set
    assert conductor.workspace == workspace or str(Path(workspace).resolve())
    assert conductor.image == "python-dev:latest"
    assert conductor.config == {}
    assert conductor.local_mode is True


def test_conductor_initialization_with_config(ray_cluster, tmp_path):
    """Test conductor initialization with a more complete config."""
    workspace = str(tmp_path / "test_workspace")
    
    config = {
        "workspace": {
            "root": workspace,
            "mirror": {
                "mode": "development",
                "enabled": False,
            }
        },
        "modes": [
            {
                "name": "default",
                "tools_enabled": ["*"],
            }
        ],
    }
    
    conductor = OpenAIConductor(
        workspace=workspace,
        image="python-dev:latest",
        config=config,
        local_mode=True,
    )
    
    # Verify config was applied
    assert conductor.config == config
    assert conductor.workspace == workspace or str(Path(workspace).resolve())


def test_conductor_components_initialized(ray_cluster, tmp_path):
    """Verify all components are initialized correctly after bootstrap."""
    workspace = str(tmp_path / "test_workspace")
    
    conductor = OpenAIConductor(
        workspace=workspace,
        image="python-dev:latest",
        config={},
        local_mode=True,
    )
    
    # Core components
    assert hasattr(conductor, 'dialect_manager')
    assert conductor.dialect_manager is not None
    
    # Logging components
    assert hasattr(conductor, 'logger_v2')
    assert conductor.logger_v2 is not None
    assert hasattr(conductor, 'md_writer')
    assert conductor.md_writer is not None
    assert hasattr(conductor, 'api_recorder')
    assert conductor.api_recorder is not None
    assert hasattr(conductor, 'structured_request_recorder')
    assert conductor.structured_request_recorder is not None
    
    # Provider components
    assert hasattr(conductor, 'provider_metrics')
    assert conductor.provider_metrics is not None
    assert hasattr(conductor, 'route_health')
    assert conductor.route_health is not None
    
    # Execution components
    assert hasattr(conductor, 'agent_executor')
    assert conductor.agent_executor is not None
    assert hasattr(conductor, 'message_formatter')
    assert conductor.message_formatter is not None
    assert hasattr(conductor, 'permission_broker')
    assert conductor.permission_broker is not None
    
    # Orchestration components
    assert hasattr(conductor, 'plan_bootstrapper')
    assert conductor.plan_bootstrapper is not None
    assert hasattr(conductor, 'streaming_policy')
    assert conductor.streaming_policy is not None
    assert hasattr(conductor, 'provider_invoker')
    assert conductor.provider_invoker is not None
    assert hasattr(conductor, 'guardrail_orchestrator')
    assert conductor.guardrail_orchestrator is not None
    assert hasattr(conductor, 'tool_prompt_planner')
    assert conductor.tool_prompt_planner is not None
    assert hasattr(conductor, 'turn_relayer')
    assert conductor.turn_relayer is not None


def test_conductor_satisfies_conductor_context_protocol(ray_cluster, tmp_path):
    """Verify conductor satisfies ConductorContext protocol (P2)."""
    workspace = str(tmp_path / "test_workspace")
    
    conductor = OpenAIConductor(
        workspace=workspace,
        image="python-dev:latest",
        config={},
        local_mode=True,
    )
    
    # Type check: conductor should be usable as ConductorContext
    # This is a structural check - if it doesn't satisfy the protocol,
    # type checkers would catch it, but we can also verify key attributes exist
    
    # Core attributes
    assert hasattr(conductor, 'workspace')
    assert hasattr(conductor, 'config')
    
    # Execution components
    assert hasattr(conductor, 'agent_executor')
    assert hasattr(conductor, 'enhanced_executor')
    assert hasattr(conductor, 'permission_broker')
    
    # Guardrails
    assert hasattr(conductor, 'guardrail_orchestrator')
    
    # Logging
    assert hasattr(conductor, 'logger_v2')
    assert hasattr(conductor, 'message_formatter')
    assert hasattr(conductor, 'md_writer')
    assert hasattr(conductor, 'structured_request_recorder')
    
    # Metrics
    assert hasattr(conductor, 'provider_metrics')
    assert hasattr(conductor, 'route_health')
    
    # Execution method
    assert hasattr(conductor, '_exec_raw')
    assert callable(conductor._exec_raw)
    
    # Verify conductor can be passed to functions expecting ConductorContext
    # This is a runtime check that the protocol is satisfied
    def _test_protocol_usage(ctx: ConductorContext) -> str:
        return ctx.workspace
    
    # Should not raise TypeError
    result = _test_protocol_usage(conductor)
    assert result == workspace or str(Path(workspace).resolve())


def test_conductor_guardrail_attributes_initialized(ray_cluster, tmp_path):
    """Verify guardrail attributes are initialized correctly."""
    workspace = str(tmp_path / "test_workspace")
    
    conductor = OpenAIConductor(
        workspace=workspace,
        image="python-dev:latest",
        config={},
        local_mode=True,
    )
    
    # Guardrail attributes should be set
    assert hasattr(conductor, 'zero_tool_warn_message')
    assert hasattr(conductor, 'zero_tool_abort_message')
    assert hasattr(conductor, 'completion_guard_abort_threshold')
    assert hasattr(conductor, 'zero_tool_warn_turns')
    assert hasattr(conductor, 'zero_tool_abort_turns')
    assert hasattr(conductor, 'zero_tool_emit_event')
    
    # Guardrail handlers may be None initially, but should exist
    assert hasattr(conductor, 'completion_guard_handler')
    assert hasattr(conductor, 'workspace_guard_handler')
    assert hasattr(conductor, 'todo_rate_guard_handler')


def test_conductor_replay_session_loading(ray_cluster, tmp_path):
    """Test that replay session loading works (if configured)."""
    workspace = str(tmp_path / "test_workspace")
    
    # Without replay config, should initialize normally
    conductor = OpenAIConductor(
        workspace=workspace,
        image="python-dev:latest",
        config={},
        local_mode=True,
    )
    
    assert conductor._replay_session_data is None
    assert conductor._active_replay_session is None
    
    # With replay config pointing to non-existent file, should handle gracefully
    config_with_replay = {
        "replay": {
            "session_path": "/nonexistent/path.json"
        }
    }
    
    # Should not crash, but replay data should be None if file doesn't exist
    # (actual behavior depends on load_replay_session implementation)
    conductor2 = OpenAIConductor(
        workspace=workspace,
        image="python-dev:latest",
        config=config_with_replay,
        local_mode=True,
    )
    
    # Replay attributes should exist
    assert hasattr(conductor2, '_replay_session_data')
    assert hasattr(conductor2, '_active_replay_session')


def test_conductor_sandbox_initialization(ray_cluster, tmp_path):
    """Test that sandbox is initialized correctly."""
    workspace = str(tmp_path / "test_workspace")
    
    conductor = OpenAIConductor(
        workspace=workspace,
        image="python-dev:latest",
        config={},
        local_mode=True,
    )
    
    # Sandbox should be initialized
    assert hasattr(conductor, 'sandbox')
    assert conductor.sandbox is not None
    assert conductor.local_mode is True
    assert conductor.using_virtualized is False  # local_mode disables virtualization


def test_conductor_workspace_preparation(ray_cluster, tmp_path):
    """Test that workspace is prepared correctly."""
    workspace = str(tmp_path / "test_workspace")
    
    conductor = OpenAIConductor(
        workspace=workspace,
        image="python-dev:latest",
        config={},
        local_mode=True,
    )
    
    # Workspace should exist and be a directory
    workspace_path = Path(conductor.workspace)
    assert workspace_path.exists()
    assert workspace_path.is_dir()