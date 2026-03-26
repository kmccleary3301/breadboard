# DARWIN Stage-5 Repo_SWE Family Probe Review

Date: 2026-03-25
Status: reviewed
References:
- `docs/darwin_stage5_repo_swe_family_probe_status_2026-03-25.md`
- `artifacts/darwin/stage5/policy_stability/policy_stability_v0.json`

## Review questions

### 1. Did the family probe improve the Repo_SWE read?

Yes.

The prior Repo_SWE topology probe was dominated by flat cases. The tool-scope probe produces fewer flat rows and more directional compounding outcomes.

### 2. Did the family probe make Repo_SWE positive?

No.

Repo_SWE remains `mixed_negative` under the current Stage-5 protocol.

### 3. Was the slice still bounded?

Yes.

It only changed the selected Repo_SWE family under the existing Stage-5 protocol and did not widen into transfer, composition, or runtime changes.

## Review conclusion

The Repo_SWE family probe succeeded as a bounded diagnostic slice.

It improved interpretability, but it did not produce a stable-positive Repo_SWE compounding surface. The next bounded Repo_SWE move should be a comparison-protocol adjustment or a side-by-side family A/B surface, not more blind density.
