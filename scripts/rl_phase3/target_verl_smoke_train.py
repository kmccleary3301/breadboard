from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd


def sha_path(path: Path) -> str:
    h = hashlib.sha256()
    if path.is_file():
        h.update(path.read_bytes())
    elif path.exists():
        for child in sorted(p for p in path.rglob("*") if p.is_file()):
            h.update(str(child.relative_to(path)).encode())
            h.update(child.read_bytes())
    return "sha256:" + h.hexdigest()


def write_dataset(path: Path, rows: int, *, grpo: bool) -> None:
    data = []
    for index in range(rows):
        group = index // 2 if grpo else index
        data.append({
            "data_source": "phase3_smoke_math",
            "prompt": [{"role": "user", "content": f"Return only the integer {group + 1}."}],
            "reward_model": {"style": "exact", "ground_truth": str(group + 1)},
            "extra_info": {"index": index, "split": "train", "group_id": str(group)},
        })
    df = pd.DataFrame(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


def write_reward(path: Path) -> None:
    path.write_text(
        "def compute_score(data_source, solution_str, ground_truth, extra_info=None):\n"
        "    text = str(solution_str).strip()\n"
        "    truth = str(ground_truth).strip()\n"
        "    return {'score': 1.0 if truth in text else 0.0, 'exact_match': truth in text}\n"
    )


def main() -> int:
    backend = os.environ.get("PHASE3_TRAINER_BACKEND", "verl_ppo")
    target_run_id = os.environ.get("PHASE3_TARGET_RUN_ID", "")
    model_ref = os.environ.get("PHASE3_TRAINER_MODEL_PATH", "Qwen/Qwen2.5-0.5B-Instruct")
    root = Path("/shared/bb-p3-root/phase3_trainer_runs") / f"{target_run_id or 'no-target'}-{int(time.time())}" / backend
    root.mkdir(parents=True, exist_ok=True)
    train = root / "train.parquet"
    val = root / "val.parquet"
    reward = root / "reward.py"
    grpo = backend == "verl_grpo"
    write_dataset(train, 8 if not grpo else 8, grpo=grpo)
    write_dataset(val, 2 if not grpo else 2, grpo=grpo)
    write_reward(reward)
    ckpt_dir = root / "checkpoints"
    before_sha = sha_path(ckpt_dir)
    adv = "grpo" if grpo else "gae"
    rollout_n = os.environ.get("PHASE3_ROLLOUT_N", "2" if grpo else "1")
    rollout_name = os.environ.get("PHASE3_ROLLOUT_NAME", "vllm")
    n_gpus_per_node = os.environ.get("PHASE3_N_GPUS_PER_NODE", "8")
    entrypoint = os.environ.get("PHASE3_TRAINER_ENTRYPOINT", "verl.trainer.main_ppo" if grpo else "verl.trainer.main_ppo_sync")
    command = [
        sys.executable, "-m", entrypoint,
        f"data.train_files={train}",
        f"data.val_files={val}",
        "data.train_batch_size=8",
        "data.val_batch_size=2",
        "data.max_prompt_length=64",
        "data.max_response_length=16",
        "data.dataloader_num_workers=0",
        "data.filter_overlong_prompts=False",
        "data.truncation=right",
        f"actor_rollout_ref.model.path={model_ref}",
        "+actor_rollout_ref.model.override_config.attn_implementation=eager",
        "actor_rollout_ref.model.trust_remote_code=True",
        f"actor_rollout_ref.rollout.name={rollout_name}",
        f"actor_rollout_ref.rollout.n={rollout_n}",
        "actor_rollout_ref.rollout.tensor_model_parallel_size=1",
        "actor_rollout_ref.rollout.mode=async",
        "actor_rollout_ref.rollout.log_prob_micro_batch_size_per_gpu=1",
        "actor_rollout_ref.ref.log_prob_micro_batch_size_per_gpu=1",
        "actor_rollout_ref.actor.ppo_mini_batch_size=8",
        "actor_rollout_ref.actor.ppo_micro_batch_size_per_gpu=1",
        "actor_rollout_ref.actor.fsdp_config.use_torch_compile=False",
    ]
    if not grpo:
        command.extend([
            f"critic.model.path={model_ref}",
            "+critic.model.override_config.attn_implementation=eager",
            "critic.model.trust_remote_code=True",
            "critic.ppo_micro_batch_size_per_gpu=1",
            "critic.ppo_mini_batch_size=8",
            "critic.optim.lr=1e-6",
        ])
    command.extend([
        "reward.num_workers=1",
        f"reward.custom_reward_function.path={reward}",
        "reward.custom_reward_function.name=compute_score",
        f"algorithm.adv_estimator={adv}",
        "trainer.project_name=bb_phase3",
        f"trainer.experiment_name={backend}",
        "trainer.nnodes=1",
        f"trainer.n_gpus_per_node={n_gpus_per_node}",
        "trainer.total_epochs=1",
        "trainer.total_training_steps=1",
        "trainer.save_freq=1",
        "trainer.test_freq=-1",
        "trainer.val_before_train=False",
        "trainer.logger=console",
        f"trainer.default_local_dir={ckpt_dir}",
    ])
    env = dict(os.environ)
    env["HF_HOME"] = "/shared/bb-p3-root/hf_home"
    visible = env.get("ROCR_VISIBLE_DEVICES", env.get("HIP_VISIBLE_DEVICES", env.get("CUDA_VISIBLE_DEVICES", "")))
    if visible:
        env["HIP_VISIBLE_DEVICES"] = visible
    env.pop("ROCR_VISIBLE_DEVICES", None)
    env.pop("CUDA_VISIBLE_DEVICES", None)
    env.pop("RAY_EXPERIMENTAL_NOSET_HIP_VISIBLE_DEVICES", None)
    started = time.time()
    stdout_path = root / "trainer_stdout.log"
    stderr_path = root / "trainer_stderr.log"
    timed_out = False
    with stdout_path.open("w") as stdout_handle, stderr_path.open("w") as stderr_handle:
        try:
            result = subprocess.run(command, text=True, stdout=stdout_handle, stderr=stderr_handle, env=env, timeout=2700, check=False)
            returncode = result.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = 124
            if exc.stdout:
                stdout_handle.write(exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode(errors="replace"))
            if exc.stderr:
                stderr_handle.write(exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode(errors="replace"))
            stderr_handle.write(f"\nPHASE3_TRAINER_TIMEOUT: {exc}\n")
    after_sha = sha_path(ckpt_dir)
    changed = before_sha != after_sha and ckpt_dir.exists()
    metrics = {
        "optimizer_step_count": 1 if returncode == 0 and changed else 0,
        "device_count": 8,
        "weight_update_performed": returncode == 0 and changed,
        "duration_seconds": time.time() - started,
        "returncode": returncode,
        "timed_out": timed_out,
    }
    (root / "metrics.json").write_text(json.dumps(metrics, sort_keys=True, indent=2) + "\n")
    report = {
        "schema_version": "bb.rl.phase3.verl_trainer_update.v1",
        "report_id": f"phase3_{backend}_trainer_update",
        "component": f"phase3_{backend}_trainer_update",
        "claim_boundary": "phase3_verl_ppo_grpo_weight_update_named_target_scope",
        "target_run_id": target_run_id,
        "trainer_backend": backend,
        "model_ref": model_ref,
        "entrypoint": entrypoint,
        "algorithm_adv_estimator": adv,
        "rollout_name": rollout_name,
        "n_gpus_per_node": int(n_gpus_per_node),
        "optimizer_step_count": metrics["optimizer_step_count"],
        "checkpoint_before_sha256": before_sha,
        "checkpoint_after_sha256": after_sha,
        "checkpoint_changed": changed,
        "weight_update_performed": metrics["weight_update_performed"],
        "device_count": int(n_gpus_per_node),
        "artifact_paths": {"run_dir": str(root), "metrics": str(root / "metrics.json"), "stdout": str(root / "trainer_stdout.log"), "stderr": str(root / "trainer_stderr.log")},
        "input_hashes": {"train": sha_path(train), "val": sha_path(val), "reward": sha_path(reward)},
        "blocked_reason": "" if returncode == 0 and changed else ("verl_trainer_update_timeout" if timed_out else "verl_trainer_update_failed"),
        "scorecard_update_allowed": False,
        "passed": returncode == 0 and changed,
    }
    print("PHASE3_COMPONENT_REPORT_JSON=" + json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
