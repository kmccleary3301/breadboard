from __future__ import annotations

from breadboard.rl.security import run_probe_suite
from breadboard.rl.security.reward_hack_suites import (
    SWE_REWARD_HACK_PROBES,
    build_swe_reward_hack_probe_suite,
)
from tests.rl.security.helpers import load_swe_hardening_policy


def test_swe_reward_hack_probe_suite_executes_and_quarantines_adversarial_fixtures(tmp_path) -> None:
    results = run_probe_suite(
        workspace=tmp_path,
        policy=load_swe_hardening_policy(),
        probes=build_swe_reward_hack_probe_suite(),
    )

    assert [item.probe_id for item in results] == SWE_REWARD_HACK_PROBES
    assert all(item.status == "quarantined" for item in results)
    assert all(item.findings for item in results)
