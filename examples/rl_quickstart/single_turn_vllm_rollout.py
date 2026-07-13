from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))



DEFAULT_ENV_PACKAGE = Path(__file__).resolve().parents[1] / "rl_env_packages" / "math_console_toy" / "env_package.yaml"
DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _add_runtime_paths(verl_wrapper: Path, nemo_gym_dir: Path | None) -> dict[str, str]:
    verl_src = verl_wrapper / "src"
    verl_dir = verl_wrapper / "third_party" / "verl"
    nemo_dir = nemo_gym_dir or (verl_wrapper / "third_party" / "nemo-gym")
    missing = [str(path) for path in (verl_src, verl_dir, nemo_dir) if not path.exists()]
    if missing:
        raise FileNotFoundError(f"exact wrapper paths missing: {', '.join(missing)}")
    for path in reversed((verl_src, verl_dir, nemo_dir)):
        sys.path.insert(0, str(path))
    os.environ.setdefault("ZYPHRA_NEMO_GYM_DIR", str(nemo_dir))
    return {"wrapper_src": str(verl_src), "verl": str(verl_dir), "nemo_gym": str(nemo_dir)}


def _tool_use_row() -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "user",
                "content": "Use the available tool once. Call get_weather with city Paris.",
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Return weather for a city.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        "expected_action": {"type": "function_call", "name": "get_weather", "arguments": {"city": "Paris"}},
    }



async def run_single_turn_rollout(args: argparse.Namespace) -> dict[str, Any]:
    from breadboard.rl.env_package import load_env_package

    env_package = load_env_package(args.env_package)
    wrapper_paths = _add_runtime_paths(args.verl_wrapper, args.nemo_gym_dir)

    import ray
    import requests
    from omegaconf import OmegaConf
    from transformers import AutoTokenizer
    from verl.workers.rollout.llm_server import GlobalRequestLoadBalancer, LLMServerClient
    from verl.workers.rollout.replica import TokenOutput
    from zyphra_verl.nemo_gym_loop import NeMoGymToolUseLoop, ToolParser, _load_canonical_verifier

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    @ray.remote(num_cpus=0)
    class VLLMOpenAICompletionActor:
        def __init__(self, model: str, base_url: str):
            self.model = model
            self.base_url = base_url.rstrip("/")
            self.tokenizer = AutoTokenizer.from_pretrained(model, trust_remote_code=True)
            if self.tokenizer.pad_token_id is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
            self.generate_calls = 0
            self.last_text = ""

        def generate(self, request_id, prompt_ids, sampling_params, **kwargs):
            self.generate_calls += 1
            prompt = self.tokenizer.decode(prompt_ids, skip_special_tokens=False)
            response = requests.post(
                f"{self.base_url}/v1/completions",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "max_tokens": int(sampling_params.get("max_tokens", 128)),
                    "temperature": float(sampling_params.get("temperature", 0.0)),
                    "logprobs": 1,
                },
                timeout=float(os.environ.get("BB_RL_QUICKSTART_VLLM_TIMEOUT", "180")),
            )
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            text = choice.get("text") or ""
            self.last_text = text
            token_ids = self.tokenizer.encode(text, add_special_tokens=False)
            return TokenOutput(
                token_ids=token_ids,
                log_probs=[0.0] * len(token_ids),
                num_preempted=0,
                extra_fields={
                    "real_vllm_http_server": True,
                    "server_base_url": self.base_url,
                    "openai_completion_id": data.get("id", ""),
                    "request_id": request_id,
                    "raw_generation_text": text,
                },
            )

        def status(self):
            return {"generate_calls": self.generate_calls, "last_text": self.last_text}

    if not ray.is_initialized():
        ray.init(num_cpus=2, include_dashboard=False, ignore_reinit_error=True, logging_level="ERROR")

    actor = VLLMOpenAICompletionActor.remote(args.model, args.base_url)
    load_balancer = GlobalRequestLoadBalancer.remote(servers={args.base_url.rstrip("/"): actor})
    server_manager = LLMServerClient(config=OmegaConf.create({}), load_balancer_handle=load_balancer)

    verifier, fn_call_cls = _load_canonical_verifier()
    loop = NeMoGymToolUseLoop.__new__(NeMoGymToolUseLoop)
    loop.response_length = args.response_length
    loop.rollout_config = type(
        "RolloutConfig",
        (),
        {
            "response_length": args.response_length,
            "prompt_length": args.prompt_length,
            "multi_turn": type("MultiTurn", (), {"format": args.tool_format})(),
        },
    )()
    loop.tokenizer = tokenizer
    loop.processor = None
    loop.server_manager = server_manager
    loop.loop = asyncio.get_running_loop()
    loop.apply_chat_template_kwargs = {}
    loop.system_prompt = []
    loop.tool_parser = ToolParser.get_tool_parser(args.tool_format, tokenizer)
    loop._FnCall = fn_call_cls
    loop._ExpectedFunctionCall = verifier.ExpectedFunctionCall
    loop._comparator = verifier.ToolCallComparator(
        config=verifier.ToolCallComparatorConfig(word_count_similarity_threshold=args.word_similarity_threshold)
    )

    row = _tool_use_row()
    output = await loop.run(
        {"max_tokens": args.max_tokens, "temperature": args.temperature},
        raw_prompt=row["messages"],
        extra_info={"tools": row["tools"], "expected_action": row["expected_action"]},
    )
    actor_status = ray.get(actor.status.remote())
    return {
        "schema_version": "bb.rl_quickstart.single_turn_vllm_rollout.v1",
        "env_package_id": env_package.package_id,
        "env_package_hash": env_package.package_hash,
        "agent_loop": "zyphra_verl.nemo_gym_loop.NeMoGymToolUseLoop",
        "agent_loop_registry_name": "nemo_gym_tool_use",
        "server_manager_class": f"{server_manager.__class__.__module__}.{server_manager.__class__.__name__}",
        "base_url": args.base_url.rstrip("/"),
        "model": args.model,
        "wrapper_paths": wrapper_paths,
        "request_id": uuid4().hex,
        "reward_score": float(output.reward_score),
        "metrics": dict(output.metrics or {}),
        "response_token_count": len(output.response_ids),
        "real_vllm_http_server": bool(output.extra_fields.get("real_vllm_http_server")),
        "actor_status": actor_status,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one NeMo Gym function-calling row through Zyphra's veRL AgentLoop using a vLLM OpenAI server."
    )
    parser.add_argument("--env-package", type=Path, default=DEFAULT_ENV_PACKAGE, help="BreadBoard EnvPackage YAML to load.")
    parser.add_argument("--verl-wrapper", type=Path, required=True, help="Path to verl_wrapper containing src/zyphra_verl.")
    parser.add_argument("--nemo-gym-dir", type=Path, default=None, help="Path to the NeMo Gym checkout; also sets ZYPHRA_NEMO_GYM_DIR.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Existing vLLM OpenAI-compatible server URL.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name served by vLLM.")
    parser.add_argument("--tool-format", default="hermes", help="veRL ToolParser format.")
    parser.add_argument("--prompt-length", type=int, default=1024)
    parser.add_argument("--response-length", type=int, default=128)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--word-similarity-threshold", type=float, default=0.1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    print(_json_line(asyncio.run(run_single_turn_rollout(args))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
