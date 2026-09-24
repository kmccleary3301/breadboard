# ACR-20260924-filesystem-identity-decimal-strings

- `acr_id`: `ACR-20260924-filesystem-identity-decimal-strings`
- `title`: Encode filesystem identity in canonical JSON as decimal strings
- `author`: BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

SquashFS can assign inode 9223372036854805903 to an installed native tool root. The canonical JSON encoder rejects that integer because it does not round-trip through binary64. The former numeric directory authority could therefore prevent composition of an otherwise valid installed tool. Widening the encoder would change unrelated artifact hashes and is not an option.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/rl/harness/composition.py` and its composition/runtime consumers, and `breadboard/rl/harness/mount_namespace_broker.py` (supervisor journal).
- Extension modules touched: phase-5 composition and F6 restart-replay authoring, the prebound-socket plan producer, runtime capability probe, rollback-store cleanup authorities, monotonic revocation publication, the phase-5 deployment-anchor store, and the G4 bind-replace attack harness.
- Contract surfaces touched: directory authority references, prebound socket plans and observations, OpenSSL authority, composition manifests and refs, composed manifests, F6 restart-replay input identities, runtime capability observations, rollback requests, signed cleanup preparing/committed/receipt records, monotonic revocation authority identities, phase-5 deployment anchors, G4 bind-replace manifests and results, and HMAC-signed mount-namespace supervisor journals and receipts. Each filesystem `device`/`inode` pair carried in these canonical or signed artifacts is an exact base-10 string.
- Is this a **kernel danger-zone** change? `yes`.

## 3) Coupling and Generalization Impact

- Danger-zone: yes. Native-tool closure, store, CAS, OpenSSL and socket authority checks continue comparing against the same `os.stat` identities after parsing the decimal strings back to integers.
- Core-to-extension dependency: none introduced.
- Cross-harness parity: the identity encoding is independent of adapter/profile; all installed native tools share the same validation rule.
- Coupling risk: moderate because serialized composition digests and media versions change. Cached pre-change artifacts must not be admitted as current versions.

## 4) Change Classification

- Classification: `breaking` (externally observable contract correction).
- Compatibility window: none; old numeric identity documents retain their old hashes but fail the new strict versioned models. Regenerate composition artifacts together.
- Required schema/version bumps: composition ref v1/v2 become v3/v4; composition manifest v1/v2 become v3/v4; composed manifest v1 becomes v2; prebound socket plan and lease v1 become v2; OpenSSL authority v1 becomes v2. F2 production input v2 becomes v3, and its C4 static authority input/fragment, dynamic authority input, and target dynamic observations v1 become v2. F6 restart-replay input v1 becomes v2, rollback request v1 becomes v2, runtime capability report v1 becomes v2. Signed rollback cleanup preparing v2 becomes v3, committed v3 becomes v4, receipt v4 becomes v5; previous signed versions fail closed. `bb.rl.monotonic-revocation-authority-identity` v1 becomes v2, `bb.rl.phase5.deployment-anchor` v2 becomes v3, `bb.rl.g4-bind-replace-manifest` and `bb.rl.g4-bind-replace-result` v1 become v2, and `bb.rl.mount-namespace-supervisor` journal/receipt v2 becomes v3; earlier versions and integer identities fail closed. Composition and composed media types advance to version 2. Existing Python model names are structural type names, not accepted wire-version aliases.

## 5) Evidence and Validation Plan

- Require the observed 9223372036854805903 inode to round-trip in a strict installed-tool descriptor through `breadboard_engine.compilation.contracts.canonical_json_bytes`; reject `0123`, empty string, `-1`, `1e3`, and non-string identity input.
- Run each affected harness composition, sandbox, conductor, service and process test file separately; include the socket lifecycle and phase-5 authoring tests.
- Run `scripts/check_danger_zone_acr.py` on the changed path list and `scripts/check_kernel_contract_pack_v1.py` on the pinned manifest.
- Required replay check: an installed SquashFS native-tool descriptor composes without changing the canonical integer domain.
- Acceptance: exact root identity checks continue rejecting changed runtime roots.

## 6) Rollout Plan

1. Independent exact-head kernel/contracts review.
2. Merge the identity schema cutover as one change, then regenerate dependent installed composition artifacts.
3. Re-run installed native-tool replays against the merged product.

- Flags/toggles: none.
- Blast-radius constraint: filesystem identity fields only; unrelated integers retain existing canonical constraints.

## 7) Rollback Plan

- Trigger: valid installed-root identities are rejected or changed roots are accepted.
- Revert the identity schema, producer, consumer, fixture and ACR commit together; do not reuse artifacts signed under the newer versions against old models.
- Post-rollback verification: rerun the composition and sandbox tests, danger-zone check and exact installed replay.

## 8) Approvals

- Kernel reviewer: required on the exact candidate head.
- Contracts reviewer: required on the exact candidate head.
- Ops reviewer: required before installed qualification.
- Final decision: pending independent review; this ACR does not authorize merge.

## 9) Filesystem Identity Audit (issue 33)

Scope follows the issue-33 ruling of 2026-09-24. Canonical JSON rejects integers outside the binary64 domain, so an unconverted site fails closed on large-inode filesystems; it cannot silently corrupt an identity. A site is converted when a `st_dev`/`st_ino` value reaches `canonical_json_bytes`, `canonical_sha256`, `_canonical_bytes`, or a signed digest. Line numbers are at the branch head that carries this table; the broker is cited by symbol.

### In scope, converted

| Surface | Canonical or signed path | Change | Commit |
|---|---|---|---|
| `breadboard/rl/harness/composition.py` directory authority, runtime root, prebound socket plan/lease, OpenSSL authority, composition ref/manifest, composed manifest | `canonical_json_bytes` digests and composition media | decimal strings; ref v3/v4, manifest v3/v4, composed v2, socket plan/lease v2, OpenSSL v2 | `d6eb504b` |
| `breadboard/rl/harness/qualification.py` composition fixture identity (1948) | composition manifest input | decimal string | `d6eb504b` |
| `breadboard/rl/phase5/f2_composition.py`, `f3_composition.py`, `f2_authority_authoring.py`; consumers `f1_preflight.py`, `f4_authority_authoring.py`, `scripts/rl_phase5/run_f4_target_canaries.py`; producer `scripts/rl_phase5/run_f2_target_command.py` `_prebind_gateway_socket` | canonical F2/F3 inputs, composition refs, prebound socket plans | decimal strings; F2 input v3, C4/dynamic inputs v2; consumer version pins moved | `d6eb504b` |
| `breadboard/rl/phase5/f6_restart_replay_authoring.py`, `scripts/rl_phase5/run_f6_restart_replay.py` | F6 restart-replay input digest | decimal strings; input v2 | `b81d7b6f` |
| `breadboard/rl/phase5/runtime_capability_payload.py` | runtime capability report digest | decimal strings; report v2 | `b81d7b6f` |
| `breadboard/rl/phase5/rollback_store/models.py` `_ImmutableFileIdentity` | JCS rollback request digest | decimal strings; request v2 | `8483ed9c` |
| `breadboard/rl/phase5/rollback_store/base_cleanup.py` preparing/committed/receipt tuples | signed cleanup records | decimal strings; preparing v3, committed v4, receipt v5 | `6763069b` |
| `scripts/rl_phase5/phase5_authority_store.py` deployment anchor | HMAC over sorted compact JSON (`anchor.json`) | decimal strings; anchor v3 | `afe952c3` |
| `scripts/rl_phase5/g4_bind_mount_attack.py` `NodeIdentity`, `NamespaceIdentity` | `_canonical_bytes` manifest digest, result digest, protocol packets | decimal strings; manifest v2, result v2 | `14e9ea75` |
| `breadboard/rl/phase5/revocation_publication.py` `MonotonicRevocationAuthorityIdentity` root/config/lock | `_SignedRecord.monotonic_authority` HMAC, witness records, JCS rollback receipts | decimal strings; identity v2 | `f5c85a5b` |
| `breadboard/rl/harness/mount_namespace_broker.py` `_journal_process`, `_journal_path`, daemon-root and stage-root digests, `record_stage_receipt` | HMAC-signed supervisor journal (`_atomic_journal_write`, `validate_supervisor_receipt`) | decimal strings; journal v3; recovery compares `str(os.stat)` values | `12ab9670` |

### In scope, no canonical path

| Surface | Reason |
|---|---|
| `breadboard/rl/harness/sandbox.py` | `_PinnedExecutable` identities (709-712) are never read after construction; `InstalledToolAdapter` runtime-root integers are parsed from the converted composition strings and only compared with `os.stat` (981-990); workspace/root tuples (173, 495-515, 2382, 3046, 3121, 4523, 5369) are in-memory. |
| `breadboard/rl/harness/sandbox_docker.py` | `StagedDockerDescriptorMount`, `PrivateDockerDaemonBinding` and `DockerPreflightObservation` (2003) are compared in memory (1705-1760, 1789-1800); `release_failures` (2937-2960) are text inside a `CleanupStepReceipt` detail string. |
| `breadboard/rl/harness/qualification.py` `ExecutableIdentity` (2127-2134) | Frozen dataclass inside `MaterializedProductionCompositionFixture`, compared with `os.stat` in tests only. |
| `breadboard/rl/harness/materialization.py` | `_metadata_identity`, `_stable_directory_identity` and mount tuples are in-memory; `measure()` emits only `authority_id` text (988, 1406-1408); lease records carry no device/inode; 1136 compares major:minor bytes with mountinfo. |
| `breadboard/rl/harness/runner_identity.py` `ModuleArtifactIdentity` (47) | Equality only; headless, conductor and terminal serialize only `.digest`. |
| `breadboard/product/runtime/session_store.py` `SessionDirectoryIdentity` | In-memory tuple (171-201, 1449-1762); 365 builds `file:{dev}:{ino}` text that is hashed to a sha256 string. |
| `breadboard/rl/harness/project_quota.py` `measure()` (440) | Device/inode integers are dropped by the sole consumer (`sandbox.py` 4590-4625 reads authority, quota and owner only); `materialization.py` 1434 discards the result; `composition.py` delegates. |
| `breadboard/rl/harness/private_docker_daemon.py` | `PinnedFileObservation`, `PrivateContainerdObservation` and the binding are in-memory or unsigned IPC; they reach persistence only through the converted supervisor journal. |
| `breadboard/rl/harness/mount_namespace_broker.py` IPC, progress and error details | Unsigned, unpersisted `json.dumps` IPC between parent and broker child. |

### Out of scope, follow-up `issue-33b`

These remaining probes, migration tools, research tools and G4 source-deletion helpers still carry integer identities. They fail closed on large-inode filesystems and are not required for the issue-33 fix.

| Surface | Integer identity sites |
|---|---|
| `scripts/rl_phase5/probe_phase5_v2_rc4_runtime.py` | `"device": st_dev` report dicts (305-1908); `test_phase5_v2_rc4_freeze_contract` expects integers. |
| `scripts/rl_phase5/prepare_phase5_v2_migration.py` | 286-603 |
| `scripts/rl_phase5/validate_phase5_v2_migration_preparation.py` | 285 |
| `scripts/rl_phase5/run_f3_target_episode.py` | 660, 1506-1542 |
| `scripts/rl_phase5/run_f2_target_command.py` | 454-521 (the `_prebind_gateway_socket` plan producer is converted above) |
| `scripts/rl_phase5/f2_private_broker_lifecycle_probe.py` | 362-419 |
| `scripts/rl_phase5/build_transport_smoke_payload.py` | `*_inode` fields |
| `breadboard/rl/phase5/g4_source_deletion.py`, `g4_source_deletion_helper.py` | prior/anchor device integers (2019, 3207, 3931-3939, 4125-4133); helper `_integer` parsing |
