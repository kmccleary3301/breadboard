from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

MODEL = os.environ.get("PHASE4_NATIVE_INFERENCE_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
PORT = int(os.environ.get("PHASE4_NATIVE_INFERENCE_PORT", "18008"))
BASE_URL = f"http://127.0.0.1:{PORT}"
SERVER_TIMEOUT_SECONDS = int(os.environ.get("PHASE4_NATIVE_INFERENCE_SERVER_TIMEOUT", "900"))
CLAIM_BOUNDARY = "phase4_native_breadboard_sub_inference_lane_slurm_smoke_scope"
INFERENCE_OWNER = "breadboard_native_sub_inference_lane"


def sha_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha_path(path: Path) -> str:
    return sha_bytes(path.read_bytes()) if path.exists() else ""


def json_hash(payload: Any) -> str:
    return sha_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def component_report(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "phase4_native_breadboard_inference_lane_attempt",
        "component": "native_breadboard_inference_lane",
        "claim_boundary": payload["claim_boundary"],
        "target_run_id": payload["target_run_id"],
        "points": 0,
        "passed": payload["passed"],
        "blocked_reason": payload["blocked_reason"],
        "attempt_report": payload,
        "input_hashes": {"attempt_report_inline": json_hash(payload)},
        "artifact_paths": {},
        "required_artifact_keys": [],
        "scorecard_update_allowed": False,
        "promotional": False,
    }


def wait_for_server(proc: subprocess.Popen, timeout_seconds: int) -> tuple[bool, str]:
    import urllib.request

    deadline = time.time() + timeout_seconds
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            return False, f"server_exited:{proc.returncode}:{last_error}"
        try:
            with urllib.request.urlopen(BASE_URL + "/v1/models", timeout=5) as response:
                if response.status == 200:
                    return True, ""
                last_error = f"status:{response.status}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{exc.__class__.__name__}:{exc}"
        time.sleep(5)
    return False, f"server_timeout:{last_error}"


class CountingComparator:
    def __init__(self, inner):
        self.inner = inner
        self.compare_tool_call_calls = 0

    def compare_tool_call(self, *args, **kwargs):
        self.compare_tool_call_calls += 1
        return self.inner.compare_tool_call(*args, **kwargs)


class RealRolloutTokenizerProxy:
    def __init__(self, tokenizer):
        self._tokenizer = tokenizer
        self.decode_calls = 0
        self.chat_template_calls = 0
        self.eos_token_id = tokenizer.eos_token_id
        self.pad_token_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id

    def apply_chat_template(self, *args, **kwargs):
        self.chat_template_calls += 1
        return self._tokenizer.apply_chat_template(*args, **kwargs)

    def decode(self, *args, **kwargs):
        self.decode_calls += 1
        return self._tokenizer.decode(*args, **kwargs)

    def batch_decode(self, *args, **kwargs):
        self.decode_calls += len(args[0]) if args else 1
        return self._tokenizer.batch_decode(*args, **kwargs)

    def encode(self, *args, **kwargs):
        return self._tokenizer.encode(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._tokenizer, name)


async def main() -> int:
    wrapper = Path(sys.argv[1]).resolve()
    target_run_id = os.environ.get("PHASE3_TARGET_RUN_ID", "")
    slurm_job_id = os.environ.get("SLURM_JOB_ID", "") or os.environ.get("PHASE3_SLURM_JOB_ID", "")
    node = os.environ.get("SLURMD_NODENAME", "") or socket.gethostname()
    if slurm_job_id and target_run_id.endswith("-slurm-pending"):
        target_run_id = f"{target_run_id[:-len('-slurm-pending')]}-slurm-{slurm_job_id}"
    report: dict[str, Any] = {
        "schema_version": "bb.rl.phase4.native_breadboard_inference_lane_attempt.v1",
        "report_id": "phase4_native_breadboard_inference_lane_attempt",
        "component": "native_breadboard_inference_lane",
        "target_run_id": target_run_id,
        "slurm_job_id": slurm_job_id,
        "node": node,
        "claim_boundary": CLAIM_BOUNDARY,
        "scorecard_update_allowed": False,
        "promotional": False,
        "breadboard_native_lane_used": False,
        "inference_owner": "",
        "engine_owner": "vllm_openai_server",
        "compatibility_bridge": "verl.workers.rollout.llm_server.LLMServerClient",
        "runtime": {},
        "wrapper": {},
        "checks": {},
        "errors": [],
        "model": MODEL,
        "server_base_url": BASE_URL,
    }
    source_path = wrapper / "src/zyphra_verl/nemo_gym_loop.py"
    source = source_path.read_text() if source_path.exists() else ""
    report["wrapper"] = {
        "path": str(wrapper),
        "nemo_gym_loop_sha256": sha_bytes(source.encode()) if source else "",
        "deps_yaml_sha256": sha_path(wrapper / "deps.yaml"),
        "source_terms_present": {term: (term in source) for term in ["register(\"nemo_gym_tool_use\"", "ToolParser", "ToolCallComparator", "reward_score"]},
    }
    sys.path.insert(0, str(wrapper / "src"))
    nemo_dir = os.environ.get("ZYPHRA_NEMO_GYM_DIR", "")
    if nemo_dir:
        sys.path.insert(0, nemo_dir)
    proc = None
    server_stdout_file = None
    server_stderr_file = None
    log_dir = Path(os.environ.get("PHASE4_NATIVE_INFERENCE_LOG_DIR", "/workspace"))
    server_stdout_path = log_dir / "phase4_native_vllm_stdout.log"
    server_stderr_path = log_dir / "phase4_native_vllm_stderr.log"
    native_request_log_path = log_dir / "phase4_native_breadboard_requests.jsonl"
    try:
        for name in ("torch", "verl", "vllm", "nemo_gym", "zyphra_verl", "breadboard.rl.phase4.native_inference"):
            try:
                mod = importlib.import_module(name)
                report["runtime"][name] = {"present": True, "version": str(getattr(mod, "__version__", "")), "file": str(getattr(mod, "__file__", ""))}
            except Exception as exc:  # noqa: BLE001
                report["runtime"][name] = {"present": False, "error": type(exc).__name__, "message": str(exc), "file": ""}
        from breadboard.rl.phase4.wrapper_identity import runtime_module_provenance

        provenance = runtime_module_provenance(
            wrapper,
            {
                "zyphra_verl": str(report["runtime"].get("zyphra_verl", {}).get("file", "")),
                "verl": str(report["runtime"].get("verl", {}).get("file", "")),
                "nemo_gym": str(report["runtime"].get("nemo_gym", {}).get("file", "")),
            },
        )
        identity_manifest_path = wrapper / "wrapper_identity.json"
        identity_manifest = {}
        if identity_manifest_path.exists():
            identity_manifest = json.loads(identity_manifest_path.read_text())
        report["wrapper"]["identity_manifest_sha256"] = sha_path(identity_manifest_path)
        report["wrapper"]["identity_manifest_passed"] = identity_manifest.get("passed") is True
        report["checks"]["runtime_module_provenance"] = provenance
        report["checks"]["runtime_module_provenance_passed"] = provenance["passed"]
        try:
            import torch

            report["runtime"]["torch_cuda"] = {
                "available": bool(torch.cuda.is_available()),
                "device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
                "device_names": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())] if torch.cuda.is_available() else [],
            }
        except Exception as exc:  # noqa: BLE001
            report["runtime"]["torch_cuda"] = {"error": type(exc).__name__, "message": str(exc)}

        server_cmd = [
            sys.executable,
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            MODEL,
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--tensor-parallel-size",
            "1",
            "--gpu-memory-utilization",
            os.environ.get("PHASE4_NATIVE_INFERENCE_GPU_UTIL", "0.35"),
            "--max-model-len",
            os.environ.get("PHASE4_NATIVE_INFERENCE_MAX_MODEL_LEN", "2048"),
            "--no-enable-log-requests",
            "--trust-remote-code",
        ]
        report["checks"]["server_command"] = server_cmd
        server_stdout_path.parent.mkdir(parents=True, exist_ok=True)
        server_stdout_file = server_stdout_path.open("w", encoding="utf-8")
        server_stderr_file = server_stderr_path.open("w", encoding="utf-8")
        report["checks"]["vllm_server_stdout_path"] = str(server_stdout_path)
        report["checks"]["vllm_server_stderr_path"] = str(server_stderr_path)
        report["checks"]["native_request_log_path"] = str(native_request_log_path)
        proc = subprocess.Popen(server_cmd, stdout=server_stdout_file, stderr=server_stderr_file, text=True)
        ready, ready_reason = wait_for_server(proc, SERVER_TIMEOUT_SECONDS)
        report["checks"]["vllm_openai_server_ready"] = ready
        if not ready:
            report["errors"].append({"stage": "start_vllm_server", "type": "ServerNotReady", "message": ready_reason})
            raise RuntimeError(ready_reason)

        from breadboard.rl.phase4.native_inference import BREADBOARD_NATIVE_INFERENCE_OWNER, NativeInferenceLane, sha256_file
        from omegaconf import OmegaConf
        import ray
        from transformers import AutoTokenizer
        from verl.workers.rollout.llm_server import GlobalRequestLoadBalancer, LLMServerClient
        from verl.workers.rollout.replica import TokenOutput

        base_tokenizer = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
        if base_tokenizer.pad_token_id is None:
            base_tokenizer.pad_token = base_tokenizer.eos_token
        tokenizer = RealRolloutTokenizerProxy(base_tokenizer)

        @ray.remote(num_cpus=0)
        class BreadboardNativeInferenceActor:
            def __init__(self, model: str, base_url: str, request_log_path: str, target_run_id: str):
                actor_tokenizer = AutoTokenizer.from_pretrained(model, trust_remote_code=True)
                if actor_tokenizer.pad_token_id is None:
                    actor_tokenizer.pad_token = actor_tokenizer.eos_token
                self.lane = NativeInferenceLane(
                    model_ref=model,
                    base_url=base_url,
                    tokenizer=actor_tokenizer,
                    request_log_path=Path(request_log_path),
                    target_run_id=target_run_id,
                )

            def generate(self, request_id, prompt_ids, sampling_params, **kwargs):
                del kwargs
                native_response = self.lane.generate_completion(
                    upstream_request_id=request_id,
                    prompt_ids=list(prompt_ids),
                    sampling_params=dict(sampling_params),
                )
                return TokenOutput(
                    token_ids=native_response.posthoc_token_ids,
                    log_probs=[0.0] * len(native_response.posthoc_token_ids),
                    num_preempted=0,
                    extra_fields={
                        "breadboard_native_lane_used": True,
                        "inference_owner": BREADBOARD_NATIVE_INFERENCE_OWNER,
                        "breadboard_request_id": native_response.request_id,
                        "breadboard_response_id": native_response.response_id,
                        "model_ref": native_response.model_ref,
                        "raw_generation_text": native_response.output_text[:500],
                        "native_output_text_sha256": native_response.output_text_sha256,
                        "backend_token_texts_sha256": native_response.backend_token_texts_sha256,
                        "backend_token_text_count": len(native_response.backend_token_texts),
                        "backend_token_logprobs_sha256": native_response.backend_token_logprobs_sha256,
                        "backend_token_logprob_count": len(native_response.backend_token_logprobs),
                        "backend_token_ids_sha256": native_response.backend_token_ids_sha256,
                        "backend_token_id_count": len(native_response.backend_token_ids),
                        "posthoc_transport_token_ids_sha256": native_response.posthoc_token_ids_sha256,
                        "posthoc_transport_token_count": len(native_response.posthoc_token_ids),
                        "native_http_status": native_response.http_status,
                        "native_latency_ms": native_response.latency_ms,
                        "backend_completion_id": native_response.backend_completion_id,
                    },
                )

            def status(self):
                return self.lane.status()

        if not ray.is_initialized():
            ray.init(num_cpus=2, include_dashboard=False, ignore_reinit_error=True, logging_level="ERROR")
        actor = BreadboardNativeInferenceActor.remote(MODEL, BASE_URL, str(native_request_log_path), target_run_id)
        load_balancer = GlobalRequestLoadBalancer.remote(servers={BASE_URL: actor})
        server_manager = LLMServerClient(config=OmegaConf.create({}), load_balancer_handle=load_balancer)
        report["checks"]["server_manager_class"] = f"{server_manager.__class__.__module__}.{server_manager.__class__.__name__}"
        report["checks"]["server_manager_role"] = "compatibility_transport_for_breadboard_native_lane"

        loop_mod = importlib.import_module("zyphra_verl.nemo_gym_loop")
        verifier, fn_call_cls = loop_mod._load_canonical_verifier()
        report["checks"]["canonical_verifier_loaded"] = True
        report["checks"]["canonical_verifier_module"] = str(getattr(verifier, "__file__", ""))
        tool_parser_cls = getattr(loop_mod, "ToolParser")
        tool_parser_obj = tool_parser_cls.get_tool_parser("hermes", tokenizer)
        loop_cls = getattr(loop_mod, "NeMoGymToolUseLoop")
        loop = loop_cls.__new__(loop_cls)
        loop.response_length = 128
        loop.rollout_config = type("RolloutConfig", (), {"response_length": 128, "prompt_length": 1024, "multi_turn": type("MultiTurn", (), {"format": "hermes"})()})()
        loop.tokenizer = tokenizer
        loop.processor = None
        loop.server_manager = server_manager
        loop.loop = asyncio.get_running_loop()
        loop.apply_chat_template_kwargs = {}
        loop.system_prompt = []
        loop.tool_parser = tool_parser_obj
        loop._FnCall = fn_call_cls
        loop._ExpectedFunctionCall = verifier.ExpectedFunctionCall
        comparator = CountingComparator(verifier.ToolCallComparator(config=verifier.ToolCallComparatorConfig(word_count_similarity_threshold=0.1)))
        loop._comparator = comparator

        messages = [{"role": "user", "content": "Use the available tool to answer: what is the weather in Paris? Call get_weather with city Paris."}]
        tools = [{"type": "function", "function": {"name": "get_weather", "description": "Return weather for a city.", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
        expected = {"type": "function_call", "name": "get_weather", "arguments": {"city": "Paris"}}
        output = await loop.run(
            {"max_tokens": 128, "temperature": 0.0},
            raw_prompt=messages,
            extra_info={"tools": tools, "expected_action": expected},
        )
        actor_status = ray.get(actor.status.remote())
        extra_fields = dict(getattr(output, "extra_fields", {}) or {})
        report["breadboard_native_lane_used"] = bool(extra_fields.get("breadboard_native_lane_used"))
        report["inference_owner"] = str(extra_fields.get("inference_owner") or "")
        report["checks"].update(
            {
                "agentloop_class": f"{loop_cls.__module__}.{loop_cls.__name__}",
                "agentloop_run_invoked": True,
                "native_lane_actor_status": actor_status,
                "native_lane_generate_calls": actor_status.get("generate_calls", 0),
                "native_request_id": extra_fields.get("breadboard_request_id", ""),
                "native_response_id": extra_fields.get("breadboard_response_id", ""),
                "native_model_ref": extra_fields.get("model_ref", ""),
                "native_generation_text_observed": bool(extra_fields.get("raw_generation_text")),
                "native_backend_token_texts_observed": int(extra_fields.get("backend_token_text_count") or 0) > 0,
                "native_backend_token_ids_observed": int(extra_fields.get("backend_token_id_count") or 0) > 0,
                "native_backend_token_logprobs_observed": int(extra_fields.get("backend_token_logprob_count") or 0) > 0,
                "native_output_text_sha256": extra_fields.get("native_output_text_sha256", ""),
                "native_backend_token_texts_sha256": extra_fields.get("backend_token_texts_sha256", ""),
                "native_backend_token_ids_sha256": extra_fields.get("backend_token_ids_sha256", ""),
                "native_backend_token_logprobs_sha256": extra_fields.get("backend_token_logprobs_sha256", ""),
                "posthoc_transport_token_ids_sha256": extra_fields.get("posthoc_transport_token_ids_sha256", ""),
                "native_request_log_sha256": actor_status.get("request_log_sha256", ""),
                "tokenizer_chat_template_calls": tokenizer.chat_template_calls,
                "tokenizer_decode_calls": tokenizer.decode_calls,
                "raw_generation_text": extra_fields.get("raw_generation_text", ""),
                "reward_score": float(getattr(output, "reward_score")) if getattr(output, "reward_score", None) is not None else None,
                "reward_score_observed": getattr(output, "reward_score", None) is not None,
                "comparator_compare_tool_call_calls": comparator.compare_tool_call_calls,
                "canonical_comparator_reward_observed": comparator.compare_tool_call_calls > 0,
                "canonical_tool_parser_used_observed": tokenizer.decode_calls > 0,
                "canonical_agentloop_run_method_invoked": True,
                "metrics": dict(getattr(output, "metrics", {}) or {}),
            }
        )
        if native_request_log_path.exists():
            report["checks"]["native_request_log_tail"] = native_request_log_path.read_text(errors="replace")[-4000:]
            report["checks"]["native_request_log_sha256"] = sha256_file(native_request_log_path)
    except Exception as exc:  # noqa: BLE001
        report["errors"].append({"stage": "native_breadboard_inference_lane", "type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc(limit=14)})
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        if proc is not None:
            for handle in (server_stdout_file, server_stderr_file):
                if handle is not None and not handle.closed:
                    handle.close()
            stdout = server_stdout_path.read_text(errors="replace") if server_stdout_path.exists() else ""
            stderr = server_stderr_path.read_text(errors="replace") if server_stderr_path.exists() else ""
            report["checks"]["vllm_server_stdout_sha256"] = sha_bytes(stdout.encode())
            report["checks"]["vllm_server_stderr_sha256"] = sha_bytes(stderr.encode())
            report["checks"]["vllm_server_stdout_tail"] = stdout[-4000:]
            report["checks"]["vllm_server_stderr_tail"] = stderr[-4000:]
            report["checks"]["vllm_server_returncode"] = proc.returncode

    checks = report["checks"]
    passed = bool(
        checks.get("vllm_openai_server_ready") is True
        and checks.get("server_manager_class") == "verl.workers.rollout.llm_server.LLMServerClient"
        and checks.get("server_manager_role") == "compatibility_transport_for_breadboard_native_lane"
        and checks.get("agentloop_run_invoked") is True
        and report.get("breadboard_native_lane_used") is True
        and report.get("inference_owner") == INFERENCE_OWNER
        and checks.get("native_lane_generate_calls", 0) >= 1
        and bool(checks.get("native_request_id"))
        and bool(checks.get("native_response_id"))
        and checks.get("native_model_ref") == MODEL
        and checks.get("native_generation_text_observed") is True
        and (checks.get("native_backend_token_texts_observed") is True or checks.get("native_backend_token_ids_observed") is True)
        and bool(checks.get("native_request_log_sha256"))
        and checks.get("canonical_tool_parser_used_observed") is True
        and checks.get("canonical_comparator_reward_observed") is True
        and checks.get("reward_score_observed") is True
        and checks.get("runtime_module_provenance_passed") is True
        and report.get("wrapper", {}).get("identity_manifest_passed") is True
    )
    blockers = []
    if not checks.get("vllm_openai_server_ready"):
        blockers.append("vllm_openai_server_not_ready")
    if checks.get("server_manager_class") != "verl.workers.rollout.llm_server.LLMServerClient":
        blockers.append("verl_llm_server_client_compatibility_bridge_not_used")
    if checks.get("server_manager_role") != "compatibility_transport_for_breadboard_native_lane":
        blockers.append("native_lane_compatibility_bridge_not_marked")
    if not checks.get("agentloop_run_invoked"):
        blockers.append("canonical_agentloop_run_not_invoked")
    if report.get("breadboard_native_lane_used") is not True:
        blockers.append("breadboard_native_lane_not_used")
    if report.get("inference_owner") != INFERENCE_OWNER:
        blockers.append("breadboard_native_inference_owner_not_observed")
    if checks.get("native_lane_generate_calls", 0) < 1:
        blockers.append("native_lane_generate_not_observed")
    if not checks.get("native_request_id"):
        blockers.append("native_request_id_missing")
    if not checks.get("native_response_id"):
        blockers.append("native_response_id_missing")
    if checks.get("native_model_ref") != MODEL:
        blockers.append("native_model_ref_missing")
    if checks.get("native_generation_text_observed") is not True:
        blockers.append("native_generation_text_not_observed")
    if checks.get("native_backend_token_texts_observed") is not True and checks.get("native_backend_token_ids_observed") is not True:
        blockers.append("native_backend_token_output_not_observed")
    if not checks.get("native_request_log_sha256"):
        blockers.append("native_request_log_hash_missing")
    if not checks.get("canonical_tool_parser_used_observed"):
        blockers.append("canonical_tool_parser_use_not_observed")
    if not checks.get("canonical_comparator_reward_observed"):
        blockers.append("canonical_comparator_reward_not_observed")
    if not checks.get("reward_score_observed"):
        blockers.append("reward_score_not_observed")
    if checks.get("runtime_module_provenance_passed") is not True:
        provenance = checks.get("runtime_module_provenance") or {}
        blockers.extend(provenance.get("blockers") or ["runtime_module_provenance_missing"])
    if report.get("wrapper", {}).get("identity_manifest_passed") is not True:
        blockers.append("wrapper_identity_manifest_missing_or_failed")
    report["passed"] = passed
    report["blocked_reason"] = "" if passed else ";".join(blockers or ["native_breadboard_inference_lane_failed"])
    print("PHASE3_COMPONENT_REPORT_JSON=" + json.dumps(component_report(report), sort_keys=True, separators=(",", ":")))
    print(json.dumps({"passed": passed, "blocked_reason": report["blocked_reason"], "checks": checks}, sort_keys=True))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
