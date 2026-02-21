from __future__ import annotations

from typing import Any, Dict, Mapping

from agentic_coder_prototype.longrun.verification import VerificationPolicy


def test_verification_policy_hard_fail_short_circuits() -> None:
    config = {
        "long_running": {
            "verification": {
                "tiers": [
                    {"name": "smoke", "commands": ["pytest -q smoke"], "timeout_seconds": 30, "hard_fail": True},
                    {"name": "full", "commands": ["pytest -q full"], "timeout_seconds": 120, "hard_fail": True},
                ]
            }
        }
    }
    policy = VerificationPolicy.from_config(config)

    def executor(command: str, timeout_seconds: float, context: Mapping[str, Any]) -> Dict[str, Any]:
        if "smoke" in command:
            return {"status": "fail", "duration_ms": 1200, "signal": "hard"}
        return {"status": "pass", "duration_ms": 5000, "signal": "hard"}

    summary = policy.run_sequence(executor, context={})
    assert summary["overall_status"] == "fail"
    assert len(summary["tiers"]) == 1
    assert summary["tiers"][0]["tier"] == "smoke"
    assert summary["tiers"][0]["status"] == "fail"


def test_verification_policy_soft_fail_continues() -> None:
    config = {
        "long_running": {
            "verification": {
                "tiers": [
                    {"name": "smoke", "commands": ["pytest -q smoke"], "timeout_seconds": 30, "hard_fail": False},
                    {"name": "full", "commands": ["pytest -q full"], "timeout_seconds": 120, "hard_fail": True},
                ]
            }
        }
    }
    policy = VerificationPolicy.from_config(config)

    def executor(command: str, timeout_seconds: float, context: Mapping[str, Any]) -> Dict[str, Any]:
        if "smoke" in command:
            return {"status": "timeout", "duration_ms": 30000, "signal": "soft"}
        return {"status": "pass", "duration_ms": 8000, "signal": "hard"}

    summary = policy.run_sequence(executor, context={})
    assert summary["overall_status"] == "soft_fail"
    assert len(summary["tiers"]) == 2
    assert summary["tiers"][0]["status"] == "timeout"
    assert summary["tiers"][1]["status"] == "pass"
