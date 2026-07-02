from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))



DEFAULT_ENV_PACKAGE = Path(__file__).resolve().parents[1] / "rl_env_packages" / "math_console_toy" / "env_package.yaml"


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


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))



def run_reward_check(args: argparse.Namespace) -> dict[str, Any]:
    from breadboard.rl.env_package import load_env_package

    env_package = load_env_package(args.env_package)
    wrapper_paths = _add_runtime_paths(args.verl_wrapper, args.nemo_gym_dir)

    from zyphra_verl.nemo_gym_loop import NeMoGymToolUseLoop, _load_canonical_verifier

    verifier, fn_call_cls = _load_canonical_verifier()
    loop = NeMoGymToolUseLoop.__new__(NeMoGymToolUseLoop)
    loop._FnCall = fn_call_cls
    loop._ExpectedFunctionCall = verifier.ExpectedFunctionCall
    loop._comparator = verifier.ToolCallComparator(
        config=verifier.ToolCallComparatorConfig(word_count_similarity_threshold=args.word_similarity_threshold)
    )

    expected = {"type": "function_call", "name": "get_weather", "arguments": {"city": "Paris"}}
    gold_tool_call = SimpleNamespace(name="get_weather", arguments=json.dumps({"city": "Paris"}, sort_keys=True))
    negative_tool_call = SimpleNamespace(name="get_weather", arguments=json.dumps({"city": "Lyon"}, sort_keys=True))

    gold_reward = float(loop._score(expected, [gold_tool_call]))
    negative_reward = float(loop._score(expected, [negative_tool_call]))

    assert gold_reward == 1.0, f"gold reward should be 1.0, got {gold_reward}"
    assert negative_reward == 0.0, f"negative reward should be 0.0, got {negative_reward}"

    return {
        "schema_version": "bb.rl_quickstart.nemo_gym_reward_check.v1",
        "env_package_id": env_package.package_id,
        "env_package_hash": env_package.package_hash,
        "agent_loop": "zyphra_verl.nemo_gym_loop.NeMoGymToolUseLoop",
        "verifier_module": str(getattr(verifier, "__file__", "")),
        "wrapper_paths": wrapper_paths,
        "checks": {
            "gold_reward": gold_reward,
            "negative_reward": negative_reward,
            "gold_assertion": "reward == 1.0",
            "negative_assertion": "reward == 0.0",
        },
        "passed": True,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Zyphra's NeMo Gym AgentLoop reward path on one gold and one negative tool call."
    )
    parser.add_argument("--env-package", type=Path, default=DEFAULT_ENV_PACKAGE, help="BreadBoard EnvPackage YAML to load.")
    parser.add_argument("--verl-wrapper", type=Path, required=True, help="Path to verl_wrapper containing src/zyphra_verl.")
    parser.add_argument("--nemo-gym-dir", type=Path, default=None, help="Path to the NeMo Gym checkout; also sets ZYPHRA_NEMO_GYM_DIR.")
    parser.add_argument("--word-similarity-threshold", type=float, default=0.1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    print(_json_line(run_reward_check(args)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
