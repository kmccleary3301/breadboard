"""opencode episode responsibility owner."""
from __future__ import annotations
import asyncio
import hashlib
import json
import base64
from pathlib import Path
import sys
import tempfile
import urllib.request
import urllib.parse
import time
from contextlib import nullcontext
from typing import Any
from .opencode_mcp import mcp_configuration, reset_mcp_relay
from .opencode_scenarios import TEST_WINDOW, production_settings, scenario_turns
from .opencode_stock import NativePruneFence, native_session_evidence, wait_stock_idle_checkpoint

def materialize_episode_profile(settings: dict[str, Any], overrides: dict[str, Any], path: Path) -> dict[str, Any]:
    """Freeze the production-loaded profile with the episode's declared overrides."""
    from breadboard_engine.auth.enforcer import apply_dotted_overrides
    from breadboard_engine.compilation.v2_loader import load_agent_config
    config = apply_dotted_overrides(load_agent_config(settings["profile"]), overrides)
    payload = json.dumps(config, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    path.write_bytes(payload)
    return {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest(),
            "source_profile": settings["profile"], "source_profile_sha256": settings["profile_sha256"],
            "override_keys": list(overrides)}

async def run_bb_queued_session(settings: dict[str, Any], workspace: Path, base_url: str,
                                session_id: str, effective_profile: dict[str, Any],
                                scenario: str) -> dict[str, Any]:
    """Drive autonomous source inputs through the actual production session lifecycle."""
    from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionInputRequest
    from breadboard_engine.api.cli_bridge.service import SessionService
    service = SessionService(state_root=Path(tempfile.mkdtemp(prefix="bb-opencode-queue-bridge-")))
    checkpoints = []
    prune_fences = []
    failure = None
    record = None
    evidence = None
    async def completed_turns(expected: int) -> str | None:
        deadline = time.monotonic() + 180
        while True:
            turns = list(record.turns_by_id.values())
            if len(turns) > expected:
                return f"Production session accepted {len(turns)} turns, expected {expected}"
            if any(turn.terminal_outcome in {"failed", "cancelled"} for turn in turns):
                return "Production queued turn did not complete successfully"
            if len(turns) == expected and all(turn.terminal_outcome == "completed" for turn in turns):
                return None
            if time.monotonic() >= deadline:
                return f"Production session did not complete {expected} queued turns"
            await asyncio.sleep(0.05)
    def queued_result() -> dict[str, Any]:
        product = record.product_session
        return {"session_id": session_id, "driver": "SessionService/SessionRunner production input queue",
                "effective_profile": effective_profile,
                "turns": [{"input_id": turn.input_id, "turn_id": turn.turn_id,
                           "client_message_id": turn.client_message_id,
                           "content": turn.content, "state": turn.state,
                           "terminal_outcome": turn.terminal_outcome}
                          for turn in record.turns_by_id.values()],
                "events": [event.asdict() for event in record.event_log],
                "idle_checkpoints": checkpoints,
                "native_prune_fences": prune_fences,
                "retained_effective_messages": json.loads(product.effective_context)
                    if product is not None and product.effective_context is not None else None,
                **({"native_evidence": evidence} if evidence is not None else {}),
                **({"error": failure} if failure is not None else {})}
    try:
        await service.create_session(
            SessionCreateRequest(task="", config_path=effective_profile["path"],
                                 workspace=str(workspace), max_steps=40, stream=True),
            session_id=session_id)
        record = await service.registry.get(session_id)
        turns = scenario_turns(scenario, settings)
        await service.send_input(session_id, SessionInputRequest(
            content="\n" + turns[0]["prompt"],
            client_message_id="delegate-idle-initial" if scenario == "delegate_idle" else "overflow-initial"))
        failure = await completed_turns(1)
        if failure is None:
            native_sessions = list(record.runner._agent.agent._opencode_tool_sessions.values())
            if len(native_sessions) != 1 or native_sessions[0].session_id != session_id:
                failure = "Production queued runner lost its genuine source session identity"
            else:
                native = native_sessions[0]
                if scenario == "overflow":
                    for index, turn in enumerate(turns[1:], 1):
                        fence_context = (
                            NativePruneFence(native.api, native.url, workspace, session_id,
                                             turn["native_prune_call_ids"], native.password)
                            if "native_prune_call_ids" in turn else nullcontext(None)
                        )
                        with fence_context as fence:
                            await service.send_input(session_id, SessionInputRequest(
                                content="\n" + turn["prompt"],
                                client_message_id=f"overflow-human-{index}"))
                            if index < len(turns) - 1:
                                failure = await completed_turns(index + 1)
                            else:
                                # The final human owns the overflow. Any genuine
                                # native recovery users enter the production queue.
                                checkpoint = await asyncio.to_thread(
                                    wait_stock_idle_checkpoint, native.api, session_id, base_url,
                                    0, turn["responses"][-1]["text"])
                                checkpoints.append(checkpoint)
                                failure = checkpoint.get("error")
                            if failure is None and fence is not None:
                                prune_fences.append(await asyncio.to_thread(fence.wait))
                        if failure is not None:
                            break
                    deadline = time.monotonic() + 180
                    while failure is None and any(
                        turn.terminal_outcome is None for turn in record.turns_by_id.values()
                    ):
                        if time.monotonic() >= deadline:
                            failure = "Production recovery queue did not settle at native completion"
                            break
                        await asyncio.sleep(0.05)
                    if failure is None and any(
                        turn.terminal_outcome != "completed" for turn in record.turns_by_id.values()
                    ):
                        failure = "Production recovery queued turn did not complete successfully"
                    evidence = native_session_evidence(native.api, session_id)
                    return queued_result()
                following = len(turns[1]["responses"])
                checkpoint = await asyncio.to_thread(
                    wait_stock_idle_checkpoint, native.api, session_id, base_url,
                    following + len(turns[0]["autonomous_responses"]), turns[0]["responses"][-1]["text"])
                checkpoints.append(checkpoint)
                failure = checkpoint.get("error")
                if failure is None:
                    with urllib.request.urlopen(base_url + "/lane/release-child", timeout=10) as response:
                        response.read()
                    # No lane-origin input invokes this turn. The source notification
                    # enters SessionService's trusted production stock-origin path.
                    checkpoint = await asyncio.to_thread(
                        wait_stock_idle_checkpoint, native.api, session_id, base_url,
                        following, turns[0]["autonomous_responses"][-1]["text"])
                    checkpoints.append(checkpoint)
                    failure = checkpoint.get("error") or await completed_turns(2)
                if failure is None:
                    await service.send_input(session_id, SessionInputRequest(
                        content="\n" + turns[1]["prompt"], client_message_id="delegate-idle-followup"))
                    failure = await completed_turns(3)
                if failure is None:
                    checkpoint = await asyncio.to_thread(
                        wait_stock_idle_checkpoint, native.api, session_id, base_url,
                        0, turns[1]["responses"][-1]["text"])
                    checkpoints.append(checkpoint)
                    failure = checkpoint.get("error")
                evidence = native_session_evidence(native.api, session_id)
        return queued_result()
    finally:
        active_error = sys.exc_info()[1]
        try:
            observation = {
                "session_id": session_id,
                "events": [event.asdict() for event in record.event_log] if record is not None else [],
                "turns": [{"input_id": turn.input_id, "turn_id": turn.turn_id,
                           "client_message_id": turn.client_message_id,
                           "content": turn.content, "state": turn.state,
                           "terminal_outcome": turn.terminal_outcome}
                          for turn in record.turns_by_id.values()] if record is not None else [],
                "idle_checkpoints": checkpoints,
                "native_prune_fences": prune_fences,
            }
            (Path(effective_profile["path"]).parent / "bb_queued_observation.json").write_text(
                json.dumps(observation, indent=2) + "\n")
        except Exception as observation_error:
            if active_error is None:
                raise
            active_error.add_note(
                f"Queued observation failed: {type(observation_error).__name__}: {observation_error}")
        finally:
            await service.shutdown_runtime_owners()

def run_bb(harness: str, scenario: str, workspace: Path, base_url: str, stock_session_id: str,
           episode_profile_path: Path, session_imports: list[dict[str, Any]] | None = None,
           mcp_relay: str | None = None) -> dict[str, Any]:
    from breadboard_engine.agent import AgenticCoder
    from breadboard_engine.provider.routing import provider_router
    settings = production_settings(harness)
    # Recording-provider transport binding only. Compaction/provider runtimes
    # and their serializers are not replaced.
    route = provider_router.providers["openai"]
    route.base_url = base_url
    route.auth_owner = "none"
    route.credential_required = False
    if not stock_session_id:
        raise ValueError("The stock run must supply its real sessionID before BB starts")
    reset_mcp_relay(mcp_relay)
    overrides = {
        "compaction.contextWindow": TEST_WINDOW,
        "providers.models[0].max_input_tokens": TEST_WINDOW,
        "enhanced_tools.lsp_integration.enabled": False,
        "tools.stock_config": {**mcp_configuration(mcp_relay), "permission": "allow", "lsp": False,
                               "provider": {"openai": {"models": {settings["model"]: {
                                   "limit": {"context": TEST_WINDOW, "input": TEST_WINDOW,
                                             "output": settings["max_output_tokens"]}}}}}},
        "tools.stock_environment": settings["overrides"]["stock_environment"],
        **({"tools.stock_session_imports": session_imports} if session_imports is not None else {}),
    }
    if scenario == "delegate_idle" or (
        scenario == "overflow" and harness == "oh-my-opencode"
    ):
        return asyncio.run(run_bb_queued_session(
            settings, workspace, base_url, stock_session_id,
            materialize_episode_profile(settings, overrides, episode_profile_path), scenario))
    agent = AgenticCoder(settings["profile"], str(workspace), overrides=overrides, force_local_mode=True)
    retained: list[dict[str, Any]] = []
    facts: list[str] = []
    events: list[dict[str, Any]] = []
    turns = []
    failure = None
    try:
        for index, turn in enumerate(scenario_turns(scenario, settings)):
            def receive(kind: str, payload: dict[str, Any], turn: int | None = None) -> None:
                events.append({"type": kind, "payload": payload, "turn": turn})
            # Stock emitted this ID before this engine session starts. Feed the
            # real session seam, never rewrite prompt_cache_key after recording.
            context = {"session_id": stock_session_id, "input_id": f"input-{index}", "turn_id": f"turn-{index}", "retained_effective_messages": retained, "retained_raw_fact_ids": facts, "_product_compaction_owner": True}
            result = agent.run_task("\n" + turn["prompt"], max_iterations=40, stream=True, event_emitter=receive, context=context)
            turns.append(result)
            if result.get("completion_reason") == "run_loop_exception":
                failure = f"Production turn {index} failed: {result.get('completion_summary')}"
                break
            snapshots = [event["payload"] for event in events if event["type"] == "conversation.compaction.end"]
            if snapshots:
                snapshot = snapshots[-1]
                retained = json.loads(base64.b64decode(snapshot["effective_context"]))
                facts = snapshot["raw_fact_ids"]
            else:
                failure = "Production engine did not emit the retained product context"
                break
        native_sessions = list(agent.agent._opencode_tool_sessions.values())
        if len(native_sessions) != 1 or native_sessions[0].session_id != stock_session_id:
            raise RuntimeError("The production runner lost its genuine source session identity")
        evidence = native_session_evidence(native_sessions[0].api, stock_session_id)
        return {"turns": turns, "events": events, "retained_effective_messages": retained,
                "session_id": stock_session_id, "native_evidence": evidence,
                **({"error": failure} if failure is not None else {})}
    finally:
        agent.close()
