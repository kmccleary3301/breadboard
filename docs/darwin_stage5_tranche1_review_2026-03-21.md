# DARWIN Stage-5 Tranche-1 Review

Date: 2026-03-21
Status: tranche-1 reviewed after denser Repo_SWE repetitions
References:
- `docs/darwin_stage5_tranche1_slice_2026-03-20.md`
- `docs/darwin_stage5_tranche1_status_2026-03-21.md`
- `artifacts/darwin/stage5/tranche1/compounding_pilot_v0.json`
- `artifacts/darwin/stage5/tranche1/compounding_cases_v1.json`

## Review questions

### 1. Is provider economics truth strong enough for Stage-5 tranche-1 claims?

Yes.

Claim-bearing rows now carry provider origin, requested route, fallback reason, and cost-source truth. The current workspace still mixes OpenRouter and direct OpenAI because of `openrouter_http_401`, but that behavior is explicit instead of silent.

### 2. Is the Stage-5 compounding protocol real or merely schematic?

Real.

Repo_SWE now emits valid live `cold_start`, `warm_start`, and `family_lockout` runs, matched comparisons, and typed `CompoundingCaseV1` records without widening runtime truth.

### 3. Does tranche-1 already prove scalable compounding?

No.

The current result is mixed: `2` `reuse_lift`, `2` `no_lift`. That is enough to prove the protocol can distinguish positive from non-positive family reuse. It is not enough to claim stable compounding rate.
The current result is mixed: `1` `reuse_lift`, `3` `no_lift`. That is enough to prove the protocol can distinguish positive from non-positive family reuse. It is not enough to claim stable compounding rate.

### 4. Should Systems remain canary-only?

No, but Systems should still enter narrowly.

Repo_SWE now has enough protocol density that Systems can move from canary-only to bounded secondary-lane participation in the next slice.

## Review conclusion

Stage-5 tranche-1 succeeded.

The tranche established economics truth, a typed compounding protocol, and a family-aware Repo_SWE search skeleton. The right next move is not broader scale. The right next move is a bounded Stage-5 secondary-lane expansion plus more dense family-aware search on Repo_SWE.
