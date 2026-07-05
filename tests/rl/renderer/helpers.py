from __future__ import annotations

from copy import deepcopy
from typing import Any


def base_rendered_turn_payload(*, fidelity_class: str = "F3") -> dict[str, Any]:
    provider_by_class = {
        "F0": {"token_ids_source": "unavailable", "logprobs_source": "unavailable"},
        "F1": {"token_ids_source": "posthoc_tokenizer", "logprobs_source": "unavailable"},
        "F2": {"token_ids_source": "provider_native", "logprobs_source": "unavailable"},
        "F3": {"token_ids_source": "provider_native", "logprobs_source": "provider_native"},
    }
    provider = provider_by_class[fidelity_class]
    payload: dict[str, Any] = {
        "rollout_id": "rollout-1",
        "trajectory_id": "traj-1",
        "task_id": "task-1",
        "split_id": "train_probe",
        "env_package_hash": "sha256:env-package",
        "turn_id": "turn-1",
        "renderer": {
            "renderer_id": "renderer-1",
            "renderer_version": "0.1.0",
            "renderer_config_hash": "sha256:renderer-config",
            "tokenizer_id": "tok-1",
            "tokenizer_hash": "sha256:tokenizer",
            "chat_template_id": "template-1",
            "chat_template_hash": "sha256:template",
            "stop_token_ids": [2],
        },
        "provider_fidelity": {
            "fidelity_class": fidelity_class,
            "provider": "local",
            "model_requested": "test-model",
            "model_served": "test-model",
            "sampling_config": {"temperature": 1.0},
            **provider,
        },
        "prompt_ids": [10, 11, 12],
        "completion_ids": [20, 21],
        "input_ids": [10, 11, 12, 20, 21],
        "attention_mask": [1, 1, 1, 1, 1],
        "loss_mask": [False, False, False, True, True],
        "assistant_mask": [False, False, False, True, True],
        "tool_action_mask": [False, False, False, False, False],
        "reward_mask": [False, False, False, False, True],
        "sampled_mask": [False, False, False, True, True],
        "message_indices": [0, 0, 0, 1, 1],
        "bridge_to_next_turn": {"attempted": True, "success": True},
        "tool_parse_status": "not_applicable",
        "parsed_completion": "42",
        "tool_calls": [],
        "finish_reason": "stop",
        "is_truncated": False,
        "overlong_prompt": False,
        "metadata": {"source": "unit_test"},
    }
    if fidelity_class == "F3":
        payload["completion_logprobs"] = [-0.1, -0.2]
    return payload


def cloned_payload(*, fidelity_class: str = "F3") -> dict[str, Any]:
    return deepcopy(base_rendered_turn_payload(fidelity_class=fidelity_class))
