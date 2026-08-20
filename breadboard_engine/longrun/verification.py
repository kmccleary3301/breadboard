from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Sequence


VerificationExecutor = Callable[[str, float, Mapping[str, Any]], Mapping[str, Any]]


@dataclass
class VerificationTier:
    name: str
    commands: List[str]
    timeout_seconds: float
    hard_fail: bool = True


class VerificationPolicy:
    """Tiered verification orchestration with hard/soft fail semantics."""

    def __init__(self, tiers: Sequence[VerificationTier]) -> None:
        self._tiers = list(tiers)

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "VerificationPolicy":
        cfg = config if isinstance(config, Mapping) else {}
        long_running = cfg.get("long_running")
        long_running = long_running if isinstance(long_running, Mapping) else {}
        verification = long_running.get("verification")
        verification = verification if isinstance(verification, Mapping) else {}
        raw_tiers = verification.get("tiers")
        raw_tiers = raw_tiers if isinstance(raw_tiers, list) else []

        tiers: List[VerificationTier] = []
        for idx, raw in enumerate(raw_tiers):
            if not isinstance(raw, Mapping):
                continue
            commands = raw.get("commands")
            commands = [str(cmd) for cmd in commands] if isinstance(commands, list) else []
            if not commands:
                continue
            name = str(raw.get("name") or f"tier_{idx}")
            timeout_seconds = float(raw.get("timeout_seconds") or 300.0)
            hard_fail = bool(raw.get("hard_fail", True))
            tiers.append(
                VerificationTier(
                    name=name,
                    commands=commands,
                    timeout_seconds=timeout_seconds,
                    hard_fail=hard_fail,
                )
            )
        return cls(tiers)

    def run_sequence(
        self,
        executor: VerificationExecutor,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        ctx = context if isinstance(context, Mapping) else {}
        tier_results: List[Dict[str, Any]] = []
        overall = "pass"

        for tier in self._tiers:
            command_results: List[Dict[str, Any]] = []
            tier_status = "pass"
            total_duration_ms = 0
            for command in tier.commands:
                result = executor(command, float(tier.timeout_seconds), ctx)
                status = str(result.get("status", "error"))
                duration_ms = int(result.get("duration_ms") or 0)
                total_duration_ms += duration_ms
                command_results.append(
                    {
                        "command": command,
                        "status": status,
                        "duration_ms": duration_ms,
                        "signal": str(result.get("signal") or ("hard" if tier.hard_fail else "soft")),
                        "details": dict(result.get("details") or {}),
                    }
                )
                if status != "pass":
                    tier_status = status
                    break

            tier_payload = {
                "tier": tier.name,
                "hard_fail": bool(tier.hard_fail),
                "status": tier_status,
                "duration_ms": total_duration_ms,
                "commands": command_results,
            }
            tier_results.append(tier_payload)

            if tier_status != "pass":
                if tier.hard_fail:
                    overall = "fail"
                    break
                if overall == "pass":
                    overall = "soft_fail"

        return {"overall_status": overall, "tiers": tier_results}
