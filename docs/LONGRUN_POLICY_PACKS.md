# LongRun Policy Packs

BreadBoard now supports an explicit longrun policy-profile toggle:

- `long_running.policy_profile: conservative`
- `long_running.policy_profile: balanced`
- `long_running.policy_profile: aggressive`

This profile is additive and non-breaking. It is recorded in longrun state/summary for observability and can be overridden at runtime with:

- `BREADBOARD_LONGRUN_POLICY_PROFILE=conservative|balanced|aggressive`

## Current profile wiring

Preset profiles:

1. `agent_configs/longrun_conservative_v1.yaml`
2. `agent_configs/longrun_balanced_v1.yaml`
3. `agent_configs/longrun_aggressive_v1.yaml`

Prompt-pack bindings:

1. `implementations/system_prompts/longrun/policy_conservative.md`
2. `implementations/system_prompts/longrun/policy_balanced.md`
3. `implementations/system_prompts/longrun/policy_aggressive.md`

## Notes

1. This tranche introduces policy-profile selection and prompt variants without changing parity defaults.
2. Longrun remains opt-in by config.
3. Profile values outside the allowed set are rejected at config-load time.
