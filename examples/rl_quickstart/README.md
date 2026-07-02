# RL quickstart: BreadBoard EnvPackage + NeMo Gym AgentLoop + veRL/vLLM

These scripts show the current Zyphra stack at the function-calling seam:

- BreadBoard loads an `EnvPackage` from `examples/rl_env_packages/`.
- `zyphra_verl.nemo_gym_loop.NeMoGymToolUseLoop` renders the row with the model chat template, calls veRL's rollout server client, parses the result with veRL `ToolParser`, and scores with NeMo Gym's `ToolCallComparator`.
- vLLM serves the model through its OpenAI-compatible endpoint. The quickstart actor adapts that endpoint to veRL's `LLMServerClient`/`GlobalRequestLoadBalancer` path.

The example row is a single function-calling task: call `get_weather` with `city="Paris"`.

## Files

- `single_turn_vllm_rollout.py` runs one row through a live vLLM OpenAI server and the Zyphra NeMo Gym AgentLoop.
- `reward_check.py` runs the no-GPU gold/negative verifier preflight against the same AgentLoop `_score` path: gold returns `1.0`, negative returns `0.0`.

## Prerequisites

Run from `breadboard_repo_integration_main_20260326` unless you adjust paths.

You need:

- a GPU node with ROCm-visible devices for the vLLM rollout script;
- the `vllm/vllm-openai-rocm:nightly` image;
- the Zyphra `verl_wrapper` directory from the Phase 4 payload;
- the veRL and NeMo Gym checkouts pinned by that wrapper (`verl_wrapper/third_party/verl` and `verl_wrapper/third_party/nemo-gym`);
- Python deps installed inside the container: BreadBoard import path, `zyphra_verl`, wrapper-pinned veRL, wrapper-pinned NeMo Gym, `ray`, `omegaconf`, `transformers`, and `requests`;
- a model served by vLLM. The small smoke-test default is `Qwen/Qwen2.5-0.5B-Instruct`.

Example paths used below:

```bash
BB_REPO=/workspace/breadboard_repo_integration_main_20260326
PAYLOAD=/workspace/real_rollout_agentloop_attempt_20260706T213000Z
VERL_WRAPPER=$PAYLOAD/verl_wrapper
NEMO_GYM_DIR=$VERL_WRAPPER/third_party/nemo-gym
VERL_DIR=$VERL_WRAPPER/third_party/verl
MODEL=Qwen/Qwen2.5-0.5B-Instruct
PORT=8000
```

## Gold/negative reward check

This check does not start vLLM and does not need a GPU, but it still imports the real wrapper and NeMo Gym verifier.

```bash
docker run --rm --ipc=host \
  -v "$PWD":/workspace/breadboard_repo_integration_main_20260326 \
  -v "$PAYLOAD":/workspace/real_rollout_agentloop_attempt_20260706T213000Z \
  --entrypoint bash \
  vllm/vllm-openai-rocm:nightly \
  -lc 'set -euo pipefail
       export PYTHONPATH=/workspace/breadboard_repo_integration_main_20260326:/workspace/real_rollout_agentloop_attempt_20260706T213000Z/verl_wrapper/src:/workspace/real_rollout_agentloop_attempt_20260706T213000Z/verl_wrapper/third_party/verl:/workspace/real_rollout_agentloop_attempt_20260706T213000Z/verl_wrapper/third_party/nemo-gym:${PYTHONPATH:-}
       cd /workspace/breadboard_repo_integration_main_20260326
       python examples/rl_quickstart/reward_check.py \
         --verl-wrapper /workspace/real_rollout_agentloop_attempt_20260706T213000Z/verl_wrapper \
         --nemo-gym-dir /workspace/real_rollout_agentloop_attempt_20260706T213000Z/verl_wrapper/third_party/nemo-gym'
```

Expected output is one JSON line with:

```json
{"checks":{"gold_reward":1.0,"negative_reward":0.0}}
```

The actual line includes package IDs, hashes, and the verifier module path.

## Single-turn vLLM rollout

Start vLLM inside the ROCm container, then run the script against that server in the same container. This command assumes the Phase 4 `verl_wrapper` is mounted beside the BreadBoard repo.

```bash
docker run --rm --ipc=host \
  --cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
  --device=/dev/kfd --device=/dev/dri --group-add video \
  -e HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}" \
  -e HF_HOME=/workspace/hf_home \
  -v "$PWD":/workspace/breadboard_repo_integration_main_20260326 \
  -v "$PAYLOAD":/workspace/real_rollout_agentloop_attempt_20260706T213000Z \
  --entrypoint bash \
  vllm/vllm-openai-rocm:nightly \
  -lc 'set -euo pipefail
       export BB_REPO=/workspace/breadboard_repo_integration_main_20260326
       export VERL_WRAPPER=/workspace/real_rollout_agentloop_attempt_20260706T213000Z/verl_wrapper
       export NEMO_GYM_DIR=$VERL_WRAPPER/third_party/nemo-gym
       export VERL_DIR=$VERL_WRAPPER/third_party/verl
       export MODEL=Qwen/Qwen2.5-0.5B-Instruct
       export PORT=8000
       export PYTHONPATH=$BB_REPO:$VERL_WRAPPER/src:$VERL_DIR:$NEMO_GYM_DIR:${PYTHONPATH:-}
       python -m vllm.entrypoints.openai.api_server \
         --model "$MODEL" \
         --host 127.0.0.1 \
         --port "$PORT" \
         --tensor-parallel-size 1 \
         --gpu-memory-utilization 0.35 \
         --max-model-len 2048 \
         --no-enable-log-requests \
         --trust-remote-code &
       server_pid=$!
       trap "kill $server_pid 2>/dev/null || true" EXIT
       python - <<PY
import time, urllib.request
for _ in range(180):
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).read()
        break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit("vLLM server did not become healthy")
PY
       cd "$BB_REPO"
       python examples/rl_quickstart/single_turn_vllm_rollout.py \
         --verl-wrapper "$VERL_WRAPPER" \
         --nemo-gym-dir "$NEMO_GYM_DIR" \
         --base-url "http://127.0.0.1:$PORT" \
         --model "$MODEL"'
```

The script prints one JSON line with `agent_loop`, `server_manager_class`, `reward_score`, `metrics`, and `real_vllm_http_server`. A low-quality model response can produce `reward_score: 0.0`; that still proves the path ran. The reward preflight above is the deterministic gold/negative check.

## Caveats

- The rollout command is a GPU job. Do not use it as a local CPU smoke test.
- The example depends on the Phase 4 `verl_wrapper` layout and its pinned submodules. `third_party/verl` and `third_party/nemo-gym` must be present; ambient/pip-installed veRL is diagnostic-only, not exact-wrapper evidence.
- `Qwen/Qwen2.5-0.5B-Instruct` is a small model reference for smoke runs. Use the team's current policy/model ref for real experiments.
- `reward_check.py` verifies the comparator path with constructed tool-call objects. It does not start vLLM and does not claim an on-policy rollout.
- `single_turn_vllm_rollout.py` uses the OpenAI-compatible vLLM endpoint as the rollout backend and returns the generated reward. It does not run GRPO/PPO training.
