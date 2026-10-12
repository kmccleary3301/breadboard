# ACR-20261012-cli-bridge-contracts-pydantic-2-14

- `acr_id`: `ACR-20261012-cli-bridge-contracts-pydantic-2-14`
- `title`: Make the exported module-authority schemas independent of the pydantic version
- `author`: BreadBoard RL implementation
- `date`: 2026-10-12
- `status`: implemented

## 1) Problem Statement

The module-authority types in `breadboard/modules/authority.py` (ProjectAuthority, NetworkAuthority, ChildAuthority, CredentialDisclosure, AuthorityDeclaration) are stdlib dataclasses. Pydantic 2.14 propagates the enclosing model's `extra="forbid"` (public `SessionStartRequest`) into them and renders `"additionalProperties": false` for all five. Pydantic 2.13.5, which `pyproject.toml` and `requirements.txt` still admit (`pydantic>=2.13.5,<3`), does not. As a result the export-plus-`git diff` CI step can pass on only one of the two supported versions. CI installs 2.14, so it fails against the committed exports, as PR #161's Python and Product spine jobs did with no CLI-bridge change.

## 2) Scope and Surfaces

- Kernel danger-zone: yes. This change touches `breadboard/modules/authority.py` and the generated exports `docs/contracts/cli_bridge/openapi.json`, `docs/contracts/cli_bridge/schemas/session_create_request.schema.json`, `sdk/ts/src/generated/openapi.v1.json` and the schema hash header of `sdk/ts/src/generated/routes.ts`.
- Each of the five dataclasses declares `__pydantic_config__ = {"extra": "forbid"}`. This is a class attribute, not a dataclass field.
- The declared contract matches validation: every `from_dict` rejects unknown fields (`_object`, "contains unknown fields"), and the API validates these values through `AuthorityDeclaration.from_dict` (`breadboard_engine/api/module_models.py` BeforeValidator).

## 3) Coupling and Generalization Impact

- Validation behavior is unchanged. Both pydantic versions build the values through `from_dict` before pydantic sees them.
- With the declaration, the exported schemas are byte-identical under pydantic 2.13.5 and 2.14.1. The runtime `session_create_request` schema now also states the closed objects that `from_dict` already enforced.

## 4) Change Classification

- Classification: `internal`.

## 5) Evidence and Validation Plan

- Exported with `scripts/release/export_cli_bridge_contracts.py` under pydantic 2.13.5 and 2.14.1 (fastapi 0.143.0): identical output. `scripts/dev/generate_ts_sdk.py --check` reports both files current under both versions.
- Under both versions: `tests/test_cli_bridge_contract_exports.py` and `tests/test_ts_sdk_codegen.py` pass (6 tests each).
- The module-authority suites `tests/product/runtime/test_checkpoint_adoption_worker.py`, `tests/test_cli_bridge_author_children.py`, `tests/test_cli_bridge_checkpoint_commands.py` and `tests/test_cli_bridge_lifecycle_authority_v2.py` pass before and after the change (39 tests).
- CI: the contract-export diff check, the TS SDK codegen check and the Python suite.

## 6) Rollout Plan

- Merge to `main`, then update open PRs (e.g. #161) so their contract-export checks run against the regenerated files.

## 7) Rollback Plan

- Revert the commit. The exports then depend on the pydantic version again, and CI must pin a single version.

## 8) Decision and Approvals

- Decision: declare the closed schema on the types instead of raising the pydantic floor. Repository owner authority for `main` writes, under the dev-branch and PR gate.
