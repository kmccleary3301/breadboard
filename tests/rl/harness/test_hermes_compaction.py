from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from breadboard.artifacts.cas import FilesystemCAS
from breadboard.product.harness.resolution import compile_e4_harness
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.policy_provider import (
    E4TargetPolicyProjection,
    EpisodeOpenAICompletionsPolicyClient,
    ProviderContractError,
    _responses_request_to_chat,
    thaw_json,
)
from breadboard.rl.harness.hermes_worker import HermesActor, HermesWorkerError, MAX_OUTPUT_TOKENS
from breadboard.rl.harness.native_stream_profiles import HERMES_RESPONSE_CONSUMER_ID, NATIVE_STREAM_PROFILES
from breadboard_engine.compilation.provider_response import OPENHANDS_RESPONSE_CONSUMER_ID, profile_identity_digest
from breadboard_engine.e4_targets import load_e4_target
from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile
from tests.compilation.test_server_compiler import _options as _compile_options
from tests.rl.harness.native_dependencies import require_hermes, require_hermes_worker_runtime
from tests.rl.harness.test_runner_policy_runtime import (
    _observation as _runtime_observation,
    _plan as _runtime_plan,
    _policy_capabilities,
)


@pytest.fixture
def context_compressor():
    source = require_hermes()
    sys.path.insert(0, str(source))
    try:
        from agent.context_compressor import ContextCompressor
        yield ContextCompressor
    finally:
        sys.path.remove(str(source))


def test_hermes_stream_profile_configuration():
    """Verify HERMES_RESPONSE_CONSUMER_ID profile has correct compaction configuration."""
    profile = NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID]
    assert profile.implements_compaction_phases is False
    assert profile.compaction_checkpoints == frozenset()
    assert profile.compaction_in_source is True
    assert profile.compaction_overflow_attempts == 3
    assert profile.compaction_summary_system_prompt is None
    assert profile.phase_mode == "checkpointed"




def test_hermes_compaction_threshold_formula_parity(context_compressor):
    """Verify stock Hermes threshold calculation:
    1. _effective_threshold_percent floors at 0.75 for windows < 512K:
       max(0.50, 0.75) = 0.75
    2. _compute_threshold_tokens with max_tokens=2048:
       effective_window = 131072 - 2048 = 129024
       pct_value = int(129024 * 0.75) = 96768
       floored = max(96768, MINIMUM_CONTEXT_LENGTH=64000) = 96768
    """
    max_tokens = 2048
    cc = context_compressor(
        model="test-model",
        threshold_percent=0.5,
        max_tokens=max_tokens,
    )
    cc.context_length = 131072

    assert cc.threshold_percent == 0.75
    assert cc.threshold_tokens == 96768

    # Verify should_compress behavior against threshold
    assert cc.should_compress(prompt_tokens=96767) is False
    assert cc.should_compress(prompt_tokens=96768) is True

def test_compaction_disabled_by_default():
    """Verify HermesActor initializes with compaction disabled and stays disabled."""
    actor = HermesActor(channel=None)
    assert actor._compaction_enabled is False
    assert actor._summary_requests == 0



@pytest.fixture
def summary_exchange(tmp_path, monkeypatch):
    class Channel:
        def __init__(self):
            self.requests = []

        def respond(self, result):
            self.requests.append(result)

        def receive(self):
            return {
                "operation": "provider_response",
                "payload": {
                    "remaining_seconds": 60,
                    "body_b64": base64.b64encode(b"{}").decode("ascii"),
                    "status_code": 200,
                    "headers": [["content-type", "application/json"]],
                },
            }

    channel = Channel()
    actor = HermesActor(channel)
    actor._phase = "sampling"
    actor._source_root = tmp_path
    actor._source_modules["httpx"] = httpx
    actor._tool_schemas = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    monkeypatch.syspath_prepend(str(tmp_path))
    return actor, channel


@pytest.mark.parametrize(
    "stock_body",
    [
        b'{ "stream" : false, "model": "m", "messages": [{"role":"user","content":"\\u0061"}], "value":1e+03 }\r\n',
        b'{"model":"m","messages":[{"role":"user","content":"escaped \\" quote"}]}\n',
        b'{ \n }\t',
    ],
)
def test_summary_exchange_preserves_stock_raw_bytes(summary_exchange, stock_body):
    actor, channel = summary_exchange
    request = httpx.Request(
        "POST", "https://provider.test/v1/chat/completions",
        headers={"x-stock-header": "retained"}, content=stock_body,
    )

    response = actor._handle_http(request, is_summary=True)

    assert response.status_code == 200
    result = channel.requests[0]
    wire = result["http_request"]
    rewritten = base64.b64decode(wire["body_b64"])
    closing = len(stock_body.rstrip(b" \t\r\n")) - 1
    assert rewritten[:closing] == stock_body[:closing]
    assert rewritten.endswith(stock_body[closing:])
    stock = json.loads(stock_body)
    appended = rewritten[closing:len(rewritten) - len(stock_body[closing:])]
    overlay = json.loads(b"{" + (appended[1:] if stock else appended) + b"}")
    assert overlay == {"tools": actor._tool_schemas, "max_tokens": MAX_OUTPUT_TOKENS}
    assert json.loads(rewritten) == {**stock, **overlay}
    assert result["stock_body_sha256"] == hashlib.sha256(stock_body).hexdigest()
    assert result["purpose"] == "compaction_summary"
    assert actor._requests == 0
    assert actor._summary_requests == 1
    headers = {key.lower(): value for key, value in wire["headers"]}
    assert headers["content-length"] == str(len(rewritten))
    assert headers["x-stock-header"] == "retained"


@pytest.mark.parametrize(
    "stock_body",
    [b"[]", b'{"tools":[]}', b'{"max_tokens":8192}'],
)
def test_summary_exchange_rejects_stock_field_overrides(summary_exchange, stock_body):
    actor, channel = summary_exchange
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions", content=stock_body)

    with pytest.raises(HermesWorkerError, match="stock summary request"):
        actor._handle_http(request, is_summary=True)

    assert channel.requests == []


def test_main_exchange_keeps_original_raw_body(summary_exchange):
    actor, channel = summary_exchange
    stock_body = b'{ "model" : "m", "messages": [], "max_tokens":4096 }\n'
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions", content=stock_body)

    actor._handle_http(request)

    result = channel.requests[0]
    assert base64.b64decode(result["http_request"]["body_b64"]) == stock_body
    assert "stock_body_sha256" not in result
    assert actor._requests == 1
    assert actor._summary_requests == 0


def _compiled_target(tmp_path: Path, target_id: str, consumer_id: str, model: str):
    profile = OpenAICompletionsProviderProfile(
        model=model,
        scoped_credential="episode-secret",
        base_url="http://127.0.0.1:8000/v1",
        context_window=131_072,
        max_output_tokens=2_048,
        capabilities={"supports_max_tokens": True, "supports_non_streaming": True, "supports_streaming": False, "supports_tools": True},
        request_policy={"mode": "non_streaming", "include_usage": False, "max_token_field": "max_tokens", "strict_tools": None, "enable_thinking": None},
    )
    cas = FilesystemCAS(tmp_path / f"cas-{model}")
    try:
        manifest = compile_e4_harness(
            load_e4_target(target_id),
            {},
            {
                "version": 2,
                "profile": {"name": f"{model}-test"},
                "workspace": {"root": "workspace"},
                "provider_tools": {"use_native": True, "api_variant": "chat"},
                "providers": {
                    "default_model": model,
                    "models": [{
                        "id": model,
                        "adapter": "openai",
                        "context_length": profile.context_window,
                        "route_handle_id": "route-a",
                        "credential_handle_id": "credential-a",
                        "params": {},
                        "response_policy": {
                            "schema_version": "bb.provider_native_response_policy.v1",
                            "consumer_id": consumer_id,
                            "provider_profile_digest": profile_identity_digest(profile),
                            "max_response_bytes": 4_194_304,
                            "max_stream_fragments": 1,
                        },
                    }],
                },
            },
            cas=cas,
            options=_compile_options(),
            request_schema_version="bb.rl.headless-run-request.v3",
        ).manifest
    finally:
        cas.close()
    return profile, manifest, E4TargetPolicyProjection.from_compiled(manifest)


def test_hermes_compaction_summary_admission_and_non_compaction_profile_rejection(tmp_path: Path) -> None:
    _, _, hermes = _compiled_target(tmp_path, "hermes-agent@2026.9.11", HERMES_RESPONSE_CONSUMER_ID, "hermes-model")
    episode_tools = [thaw_json(tool) for tool in hermes.chat_tools]

    def admit(request, projection=hermes, model="hermes-model"):
        return _responses_request_to_chat(
            request,
            expected_model_id=model,
            target_projection=projection,
            native_system_prompt="native system prompt",
            compaction_summary=True,
        )

    summary = [{"role": "user", "content": "Hermes summary request prompt."}]
    assert admit({"model": "hermes-model", "messages": summary, "tools": episode_tools}) == (summary, episode_tools)

    schema_error = "source-native compaction summary request does not match its required schema"
    with pytest.raises(ProviderContractError, match=schema_error):
        admit({"model": "hermes-model", "messages": [{"role": "system", "content": "extra"}, *summary], "tools": episode_tools})
    with pytest.raises(ProviderContractError, match=schema_error):
        admit({"model": "hermes-model", "messages": summary, "tools": []})

    _, _, openhands = _compiled_target(tmp_path, "openhands-sdk@1.47.0", OPENHANDS_RESPONSE_CONSUMER_ID, "oh-model")
    with pytest.raises(ProviderContractError, match="compaction summary requests are admitted only for compaction-capable native streams"):
        admit(
            {"model": "oh-model", "messages": summary, "tools": [thaw_json(tool) for tool in openhands.chat_tools]},
            projection=openhands,
            model="oh-model",
        )


# The worker owns its process (cwd, HOME, TZ, sys.path), so initialization runs
# in a fresh interpreter.
_COMPRESSOR_INIT = """
import json, sys
from breadboard.rl.harness.hermes_worker import HermesActor

class Channel:
    digest = None
    def respond(self, result):
        self.digest = result["history_digest"]
    def receive(self):
        return {"operation": "history_ack", "payload": {"history_digest": self.digest}}

actor = HermesActor(Channel())
actor._initialize(json.loads(sys.argv[1]))
compressor = actor._state.context_compressor
threshold = compressor.threshold_tokens
print(json.dumps({
    "context_length": compressor.context_length,
    "threshold_tokens": threshold,
    "below": compressor.should_compress(prompt_tokens=threshold - 1),
    "at": compressor.should_compress(prompt_tokens=threshold),
}))
"""


def test_overridden_episode_window_reaches_stock_compressor_threshold(tmp_path: Path) -> None:
    require_hermes_worker_runtime()

    profile, manifest, target_projection = _compiled_target(
        tmp_path, "hermes-agent@2026.9.11", HERMES_RESPONSE_CONSUMER_ID, "hermes-model"
    )

    # Episode overrides max_context_tokens to 100_000 (different from target default 131_072)
    overridden_window = 100_000
    observation = _runtime_observation(
        provider_id="openai",
        model_id="hermes-model",
        capabilities=_policy_capabilities(
            max_context_tokens=overridden_window,
            max_output_tokens=2_048,
            request_features=["max_tokens", "non_streaming"],
        ),
    )
    plan = _runtime_plan(
        observation=observation,
        semantics=manifest.semantic.to_canonical_obj(),
        policy_slot_ids=("model:hermes-model",),
    )
    plan_payload = plan.model_dump(mode="python")
    plan_payload["base_compiled"] = c.CompiledArtifactIdentity.model_validate({
        **plan.base_compiled.model_dump(mode="python"),
        "manifest_digest": "sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
        "compiler_input_digest": manifest.inputs.compiler_input_digest,
    })
    plan = c.EffectiveExecutionPlan.model_validate(plan_payload)

    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-window-test",
        effective_plan_digest=plan.canonical_digest(),
        observation=observation,
        profile=profile,
        target_projection=target_projection,
        timeout_seconds=45,
    )
    public_config = client.bind_compiled_plan(plan)
    assert public_config["max_input_tokens"] == overridden_window

    (tmp_path / "workspace").mkdir()
    target_json = Path(__file__).resolve().parents[3] / "config/e4_targets/hermes_agent/2026.9.11-r3/native-config.json"
    payload = {
        "task": "Perform task",
        "compaction": True,
        "remaining_seconds": 100,
        "model_config": public_config,
        "schema_overlay": json.loads(target_json.read_bytes())["schema_overlay"],
        "workspace": str(tmp_path / "workspace"),
        "scratch": str(tmp_path / "scratch"),
    }

    completed = subprocess.run(
        [sys.executable, "-c", _COMPRESSOR_INIT, json.dumps(payload)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    observed = json.loads(completed.stdout.strip().splitlines()[-1])
    # Stock threshold: (window - MAX_OUTPUT_TOKENS) * 0.75; the 131_072 target default would give 96_768.
    assert observed == {"context_length": overridden_window, "threshold_tokens": 73_464, "below": False, "at": True}
