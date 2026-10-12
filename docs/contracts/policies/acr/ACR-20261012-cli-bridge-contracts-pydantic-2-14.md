# ACR-20261012-cli-bridge-contracts-pydantic-2-14

- `acr_id`: `ACR-20261012-cli-bridge-contracts-pydantic-2-14`
- `title`: Regenerate the exported CLI-bridge contracts for pydantic 2.14
- `author`: BreadBoard RL implementation
- `date`: 2026-10-12
- `status`: implemented

## 1) Problem Statement

CI installs the current pydantic, 2.14. Pydantic 2.14 renders `"additionalProperties": false` in the JSON schema of every model configured with `extra="forbid"`. The committed exports predate it, so the CI step `python scripts/release/export_cli_bridge_contracts.py && git diff --exit-code docs/contracts/cli_bridge` fails on every fresh install. PR #161's Python and Product spine jobs failed on exactly this diff, and the PR touches no CLI-bridge code.

## 2) Scope and Surfaces

- Kernel danger-zone: yes. The change touches the generated exports `docs/contracts/cli_bridge/openapi.json` and `sdk/ts/src/generated/openapi.v1.json`, plus the schema hash header of `sdk/ts/src/generated/routes.ts`.
- Five schemas gain `"additionalProperties": false`: AuthorityDeclaration, ChildAuthority, CredentialDisclosure, NetworkAuthority and ProjectAuthority.
- No model, route or handler changes. The server already rejected extra fields on these models; the exported schema now states it.

## 3) Coupling and Generalization Impact

- None at runtime. A client that validates against the exported schema now rejects the same extra fields the server already rejected.
- The TS SDK route table is unchanged apart from its schema hash header (`e4585569…`).

## 4) Change Classification

- Classification: `internal`.

## 5) Evidence and Validation Plan

- Regenerated with the repository generators `scripts/release/export_cli_bridge_contracts.py` and `scripts/dev/generate_ts_sdk.py` (pydantic 2.14.1, fastapi 0.143.0).
- `python scripts/dev/generate_ts_sdk.py --check` reports both files current.
- The regenerated `openapi.json` is byte-identical to the regeneration on `feat/compaction-subsystem` (commit `02e37c0e`).
- CI: the contract-export diff check, `tests/test_cli_bridge_contract_exports.py` and `tests/test_ts_sdk_codegen.py`.

## 6) Rollout Plan

- Merge to `main`, then update open PRs (e.g. #161) so their contract-export checks run against the regenerated files.

## 7) Rollback Plan

- Revert the commit. The exports then match pydantic below 2.14, and CI must pin pydantic accordingly.

## 8) Decision and Approvals

- Decision: regenerate the exports and leave the dependency floor unchanged. Repository owner authority for `main` writes, under the dev-branch and PR gate.
