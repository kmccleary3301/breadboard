from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from breadboard.rl.phase5.models import (
    BlockerFailureClass,
    BlockerKind,
    BlockerRecord,
    BlockerState,
    CampaignDisposition,
    ClaimRecord,
    ClaimState,
    EvidenceClass,
    EvidenceState,
    ScoreItemState,
    SupportLevel,
)
from breadboard.rl.phase5.score import (
    FIXED_CATALOG_POINTS,
    FIXED_CATALOG_SHA256,
    FIXED_ITEM_COUNT,
    FIXED_WORKSTREAM_COUNTS,
    FIXED_WORKSTREAM_POINTS,
    ScoreEngine,
    ScoreItem,
    parse_score_catalog,
)

SCHEMA_PREFIX = "bb.rl.phase5"
BREADBOARD_BASELINE = "550a387706d4ca4bc49760070f55a58100af168e"
WRAPPER_BASELINE = "d5221607f59ea05ffeba1e2931eff12142d9504d"
BREADBOARD_CANONICAL_PAYLOAD_SHA256 = "sha256:f9a6f160c0a523c5ccd3f345c5de75c195430e021f7c4db3c834f5d64eeb644c"
WRAPPER_CANONICAL_PAYLOAD_SHA256 = "sha256:479e5d98dd581e53dcd1a2542951fd0c753ea6f6b37794c8738cb7bd066d4d63"
ISSUE_MAPPING_SHA256 = "sha256:c66d5fbaa4ef07168e14c53082cfd2d68964e8db29e8d4f301800c3687fc74f1"
EPIC_ID = "bb-auh"

CHILD_ARTIFACT_FILENAMES = (
    "SCORECARD.json",
    "CLAIM_LEDGER.md",
    "EVIDENCE_TAXONOMY.json",
    "CAMPAIGN_MATRIX.yaml",
    "FIXTURE_MANIFEST.json",
    "VARIANT_CATALOG.json",
    "WORK_PACKET_DAG.yaml",
    "LOOP_SPEC.yaml",
)
ARTIFACT_FILENAMES = (
    *CHILD_ARTIFACT_FILENAMES,
    "ARTIFACT_MANIFEST.json",
    "ACTIVE_STATUS.json",
)
_LEGACY_ARTIFACT_FILENAMES = (
    "SCORECARD.json",
    "ACTIVE_STATUS.json",
    "CLAIM_LEDGER.md",
    "EVIDENCE_TAXONOMY.json",
    "CAMPAIGN_MATRIX.yaml",
    "FIXTURE_MANIFEST.json",
    "VARIANT_CATALOG.json",
    "WORK_PACKET_DAG.yaml",
    "LOOP_SPEC.yaml",
)
_MEDIA_TYPES = {
    "SCORECARD.json": "application/json",
    "CLAIM_LEDGER.md": "text/markdown; charset=utf-8",
    "EVIDENCE_TAXONOMY.json": "application/json",
    "CAMPAIGN_MATRIX.yaml": "application/yaml",
    "FIXTURE_MANIFEST.json": "application/json",
    "VARIANT_CATALOG.json": "application/json",
    "WORK_PACKET_DAG.yaml": "application/yaml",
    "LOOP_SPEC.yaml": "application/yaml",
    "ARTIFACT_MANIFEST.json": "application/json",
    "ACTIVE_STATUS.json": "application/json",
}
_PACKET_ROW = re.compile(
    r"^\| (WP(?:0|[1-9]|1[0-5])(?:a|b)?) ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$",
    re.MULTILINE,
)
_PROOF_FLOOR_ROW = re.compile(
    r"^\| (governance|local contract|local process|local container|IBM target|target training|DigitalOcean|authority) \| ([^|]+) \|$",
    re.MULTILINE,
)
_REQUIRED_PLAYBOOK_ANCHORS = (
    "Each checkbox is an indivisible score item. The eventual machine-readable scorecard must copy these IDs, points, proof floors, and pass predicates exactly.",
    "Each packet has one owner, one bounded file set, one evidence contract, one rollback, and one independent reviewer. Tests are authored by the Tester role.",
    "Invalidate transitively to the earliest unsupported node.",
    "A failed rerun cannot leave the previous matching success current.",
    "Roll back to the last approved tuple of BreadBoard head, wrapper head, compiler, admission policy, runtime image, model/checkpoint, and config-set digest.",
    "`loop-reviewer` or `reviewer` for independent evidence/spec reviews;",
    "`Tester` for all test authoring;",
    "at most two implementation attempts per packet before escalation;",
    "at most two reviewer repair rounds;",
    "one attempt per scale/training topology/recipe plus one exact infrastructure retry;",
    "canonical evidence promotion, ledger mutation, checkpoint promotion, final assembly, and final review each have concurrency one.",
    "PPO points come from forced GRPO;",
    "DO substitutes for IBM;",
)


@dataclass(frozen=True)
class PacketAttemptBudget:
    implementation_attempts_before_escalation: int
    identical_local_flake_reruns: int
    exact_transient_infrastructure_retries: int
    scratch_target_attempts: int
    scale_training_attempts_per_topology_recipe: int
    reviewer_repair_rounds: int

    def validate(self) -> None:
        values = asdict(self)
        if any(not isinstance(value, int) or value < 0 for value in values.values()):
            raise ValueError("packet attempt budgets must be non-negative integers")
        if self.implementation_attempts_before_escalation != 2:
            raise ValueError("every packet must escalate after two implementation attempts")
        if self.identical_local_flake_reruns != 1 or self.reviewer_repair_rounds != 2:
            raise ValueError("packet flake and review budgets must equal the frozen limits")


@dataclass(frozen=True)
class PacketSpec:
    packet_id: str
    title: str
    dependencies: tuple[str, ...]
    primary_scope: str
    exit_gate: str
    bounded_paths: tuple[str, ...]
    bounded_symbols: tuple[str, ...]
    non_goals: tuple[str, ...]
    evidence_contract: tuple[str, ...]
    rollback: tuple[str, ...]
    attempt_budget: PacketAttemptBudget
    owner: str
    reviewer: str
    tester: str
    issue_id: str

    def validate(self) -> None:
        required_text = (
            self.packet_id,
            self.title,
            self.primary_scope,
            self.exit_gate,
            self.owner,
            self.reviewer,
            self.tester,
            self.issue_id,
        )
        if any(not value.strip() for value in required_text):
            raise ValueError(f"packet contract contains an empty scalar: {self.packet_id}")
        collections = (
            self.bounded_paths,
            self.bounded_symbols,
            self.non_goals,
            self.evidence_contract,
            self.rollback,
        )
        if any(not values or any(not value.strip() for value in values) for values in collections):
            raise ValueError(f"packet contract is incomplete: {self.packet_id}")
        if self.owner == self.reviewer or self.owner == self.tester or self.reviewer == self.tester:
            raise ValueError(f"packet roles must be independent: {self.packet_id}")
        self.attempt_budget.validate()


@dataclass(frozen=True)
class TransitionSpec:
    from_state: str
    event: str
    to_state: str
    guards: tuple[str, ...]

    def validate(self) -> None:
        if not self.from_state or not self.event or not self.to_state or not self.guards:
            raise ValueError("every transition requires from_state, event, to_state, and guards")


@dataclass(frozen=True)
class CampaignSpec:
    playbook_path: Path
    goal_prompt_path: Path
    generated_at: str
    catalog: tuple[ScoreItem, ...]
    packets: tuple[PacketSpec, ...]
    frontmatter: Mapping[str, Any]
    frozen_hashes: Mapping[str, Any]
    proof_floors: Mapping[str, str]
    catalog_sha256: str
    campaign_spec_sha256: str


@dataclass(frozen=True)
class BootstrapResult:
    output_dir: Path
    generated_at: str
    item_count: int
    catalog_points: int
    workstream_counts: dict[str, int]
    workstream_points: dict[str, int]
    packet_count: int
    catalog_sha256: str
    campaign_spec_sha256: str
    artifact_hashes: dict[str, str]


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _pretty_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _parse_generated_at(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("generated_at must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")


def _frontmatter(text: str) -> dict[str, Any]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if match is None:
        raise ValueError("playbook must contain YAML frontmatter")
    parsed: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if not line or line[0].isspace() or line.startswith("-") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if not value:
            continue
        if value == "true":
            parsed[key] = True
        elif value == "false":
            parsed[key] = False
        elif re.fullmatch(r"[0-9]+", value):
            parsed[key] = int(value)
        else:
            parsed[key] = value
    return parsed


def _expand_dependencies(
    raw: str, packet_ids: Sequence[str], packet_id: str
) -> tuple[str, ...]:
    value = raw.strip()
    if value == "none":
        return ()
    if value == "all, including WP14b":
        return tuple(candidate for candidate in packet_ids if candidate != packet_id)
    if value == "WP10–WP12":
        return ("WP10", "WP11", "WP12")
    return tuple(part.strip() for part in value.split(","))


_PACKET_CONTRACTS: dict[str, dict[str, tuple[str, ...]]] = {
    "WP0": {
        "bounded_paths": ("breadboard/rl/phase5/", "scripts/rl_phase5/", "tests/rl/phase5/", "docs_tmp/ZYPHRA/RL_PHASE_5/execution/"),
        "bounded_symbols": ("CampaignSpec", "ScoreEngine", "EvidenceGraph", "bootstrap_campaign"),
        "non_goals": ("No runtime, training, provider, external-acceptance, or promotion claim.",),
        "evidence_contract": ("49-item/1000-point catalog digest, typed governance schemas, immutable bootstrap hashes, and focused P0/P1 tests.",),
        "rollback": ("Retain the rejected lineage and restore the last byte-identical validated WP0 artifact set.",),
    },
    "WP1": {
        "bounded_paths": ("agentic_coder_prototype/compilation/contracts.py", "agentic_coder_prototype/compilation/bundle.py"),
        "bounded_symbols": ("bundle manifest", "logical path", "CAS reader", "ingestion limits"),
        "non_goals": ("No server compilation, admission, lease, or runtime execution.",),
        "evidence_contract": ("Malicious bundle corpus plus canonical manifest and closure digests.",),
        "rollback": ("Revoke the candidate closure and return to the last approved bundle manifest without reinterpretation.",),
    },
    "WP2": {
        "bounded_paths": ("agentic_coder_prototype/compilation/server_compiler.py", "agentic_coder_prototype/compilation/contracts.py"),
        "bounded_symbols": ("deterministic IR", "compiler inputs", "prompt and tool compilation"),
        "non_goals": ("No runtime family dispatch, ambient reads, or admission lease.",),
        "evidence_contract": ("Repeated compile equality and ambient-read negative controls.",),
        "rollback": ("Revoke compiler receipts and restore the last approved compiler and closure tuple.",),
    },
    "WP3": {
        "bounded_paths": ("breadboard/rl/harness/config_runtime.py", "breadboard/rl/harness/contracts.py"),
        "bounded_symbols": ("admission receipt", "capability registry", "ceilings", "revocation epoch"),
        "non_goals": ("No resource lease or selected fallback before admission.",),
        "evidence_contract": ("All denial fixtures prove rejection before lease and bind policy/schema hashes.",),
        "rollback": ("Freeze admissions, increment revocation epoch, and restore the last approved policy receipt set.",),
    },
    "WP4": {
        "bounded_paths": ("breadboard/rl/harness/config_runtime.py", "breadboard/rl/harness/evidence.py"),
        "bounded_symbols": ("config set", "selection oracle", "overlay policy", "selector weights"),
        "non_goals": ("No resampling, default, alternate candidate, or mutable weight rollback.",),
        "evidence_contract": ("Golden selector vectors, seed/weight identities, and selected-failure negative controls.",),
        "rollback": ("Publish a new immutable config-set manifest excluding the bad variant.",),
    },
    "WP5": {
        "bounded_paths": ("breadboard/rl/harness/runners/base.py", "breadboard/rl/harness/runners/terminal.py"),
        "bounded_symbols": ("runner adapter protocol", "terminal loop", "cancellation contract"),
        "non_goals": ("No conductor injection, family branch, or global environment mutation.",),
        "evidence_contract": ("Terminal adapter parity fixtures and cancellation/error carrier evidence.",),
        "rollback": ("Restore the last approved adapter protocol and quarantine incompatible runner receipts.",),
    },
    "WP6": {
        "bounded_paths": ("breadboard/rl/harness/runners/conductor.py", "breadboard/rl/harness/runners/base.py"),
        "bounded_symbols": ("compiled IR injection", "instance runtime", "trainable slot observation", "cancellation"),
        "non_goals": ("No global registry, environment mutation, or profile-selected behavior.",),
        "evidence_contract": ("Per-instance injection and trainable-slot observations with isolation negatives.",),
        "rollback": ("Cancel affected instances and restore the last approved conductor/IR tuple.",),
    },
    "WP7": {
        "bounded_paths": ("breadboard/rl/harness/materialization.py", "breadboard/rl/harness/sandbox.py", "breadboard/rl/harness/sandbox_docker.py"),
        "bounded_symbols": ("cache lease", "workspace materialization", "Docker/runsc policy", "verifier snapshot"),
        "non_goals": ("No multi-tenant or gVisor claim without compatible observed parity.",),
        "evidence_contract": ("Isolation, verifier, tamper, fault, and authoritative cleanup tests.",),
        "rollback": ("Revoke leases, drain workers, quarantine workspaces, and restore the approved image/security tuple.",),
    },
    "WP8": {
        "bounded_paths": ("breadboard/rl/harness/api.py", "breadboard/rl/harness/service.py", "breadboard/rl/harness/evidence.py"),
        "bounded_symbols": ("V2 lifecycle", "fingerprint", "tombstone", "closed envelope", "evidence join"),
        "non_goals": ("No cleanup truth from HTTP success or lossy event queues.",),
        "evidence_contract": ("Local process/container lifecycle, idempotency, tombstone, and evidence-lineage proof.",),
        "rollback": ("Cancel open episodes, reconcile from authoritative envelopes, and quarantine dependent evidence.",),
    },
    "WP9": {
        "bounded_paths": ("responses_api_agents/breadboard_agent/app.py", "recipe/nemo_async/agent_loop.py", "recipe/nemo_async/evals/run.py"),
        "bounded_symbols": ("generic client", "identity carrier", "cleanup truth", "evidence join"),
        "non_goals": ("No config-family dispatch or wrapper-owned lifecycle truth.",),
        "evidence_contract": ("Wrapper focused suite proves generic config and exact identity/error carriers.",),
        "rollback": ("Restore the last approved wrapper/client and BreadBoard V2 contract tuple.",),
    },
    "WP10": {
        "bounded_paths": ("recipe/nemo_async/", "scripts/rl_phase5/"),
        "bounded_symbols": ("harness URL", "token file", "launcher manifest", "redaction"),
        "non_goals": ("No token in argv, environment dumps, logs, reports, or artifacts.",),
        "evidence_contract": ("Local full-seam callback plus recursive seeded-secret scan and launcher identity manifest.",),
        "rollback": ("Revoke routes/secrets, cancel launchers, and restore the approved launcher/image tuple.",),
    },
    "WP11": {
        "bounded_paths": ("breadboard/rl/harness/", "responses_api_agents/", "recipe/nemo_async/"),
        "bounded_symbols": ("V1 shadow", "generic EnvSpec", "fixture migration", "legacy deletion guard"),
        "non_goals": ("No V2 reinterpretation as a profile and no compatibility shim after cutover.",),
        "evidence_contract": ("Unknown-name execution, caller migration, parity shadow, and source deletion guard.",),
        "rollback": ("Deploy the last compatible BreadBoard/wrapper pair and admitted config-set digest.",),
    },
    "WP12": {
        "bounded_paths": ("recipe/nemo_async/tools/swe/", "docs_tmp/ZYPHRA/RL_PHASE_5/evidence/fixtures/"),
        "bounded_symbols": ("R-SWE-001", "OCI image", "gold/bad/no-op controls", "verifier snapshot"),
        "non_goals": ("No gold patch exposure to policy and no unrelated benchmark claim.",),
        "evidence_contract": ("Immutable source row, repository tree, image/verifier digests, and Docker controls 2/2.",),
        "rollback": ("Revoke the fixture manifest and quarantine dependent SWE evidence.",),
    },
    "WP13": {
        "bounded_paths": ("scripts/rl_phase5/", "recipe/nemo_async/", "docs_tmp/ZYPHRA/RL_PHASE_5/evidence/target/"),
        "bounded_symbols": ("IBM preflight", "per-receipt canary", "config matrix", "target fault"),
        "non_goals": ("No local, DigitalOcean, or generic Slurm substitution for IBM evidence.",),
        "evidence_contract": ("Exact IBM job/node/runtime identities and F1-F6 terminal episode artifacts.",),
        "rollback": ("Cancel target episodes, revoke routes/secrets, reconcile allocation, and quarantine target evidence.",),
    },
    "WP13a": {
        "bounded_paths": ("breadboard/rl/harness/runners/", "scripts/rl_phase5/"),
        "bounded_symbols": ("target runner", "node/task/GPU controls", "host inventory", "topology report"),
        "non_goals": ("No head-local placement presented as distributed task execution.",),
        "evidence_contract": ("Multi-node runner tests and exact complete-host topology report.",),
        "rollback": ("Cancel allocations and restore the last approved target-runner topology tuple.",),
    },
    "WP14": {
        "bounded_paths": ("scripts/rl_phase5/", "recipe/", "docs_tmp/ZYPHRA/RL_PHASE_5/evidence/training/"),
        "bounded_symbols": ("2/4-node scale", "load ladder", "canonical soak", "GRPO", "estimator truth"),
        "non_goals": ("No PPO, convergence, benchmark-gain, or durable-resume claim without separate proof.",),
        "evidence_contract": ("One attempt per topology/recipe, exact retry identity, scale/performance/training raw artifacts, and checkpoint reload proof.",),
        "rollback": ("Cancel training, quarantine rewards/checkpoints, and restore the approved model/runtime/config tuple.",),
    },
    "WP14b": {
        "bounded_paths": ("breadboard/rl/harness/sandbox_docker.py", "scripts/rl_phase5/", "docs_tmp/ZYPHRA/RL_PHASE_5/evidence/provider/"),
        "bounded_symbols": ("runsc preflight", "hardened Docker", "DigitalOcean trigger", "provider teardown"),
        "non_goals": ("No provider substitution for IBM and no gVisor/multi-tenant claim on incompatibility.",),
        "evidence_contract": ("Closed F10 disposition, trigger decision, provider identity/cost/TTL when activated, and teardown.",),
        "rollback": ("Tear down provider resources and restore hardened Docker single-tenant scope.",),
    },
    "WP15": {
        "bounded_paths": ("docs_tmp/ZYPHRA/RL_PHASE_5/execution/", "docs_tmp/ZYPHRA/RL_PHASE_5/evidence/final/"),
        "bounded_symbols": ("score audit", "artifact manifest", "active status", "final handoff"),
        "non_goals": ("No external acceptance, scorecard authority, checkpoint publication, or promotion without named authority.",),
        "evidence_contract": ("Current canonical evidence traversal, exact score recomputation, two-axis reviews, blockers, non-claims, and authority audit.",),
        "rollback": ("Revoke the final pointer, retain immutable packet history, and restore the last approved active manifest.",),
    },
}


def _packet_budget(packet_id: str) -> PacketAttemptBudget:
    target = packet_id in {"WP13", "WP13a", "WP14", "WP14b"}
    return PacketAttemptBudget(
        implementation_attempts_before_escalation=2,
        identical_local_flake_reruns=1,
        exact_transient_infrastructure_retries=1 if target else 0,
        scratch_target_attempts=1 if packet_id == "WP13" else 0,
        scale_training_attempts_per_topology_recipe=1 if packet_id == "WP14" else 0,
        reviewer_repair_rounds=2,
    )


def _parse_packets(text: str) -> tuple[PacketSpec, ...]:
    raw_rows = _PACKET_ROW.findall(text)
    packet_ids = tuple(row[0] for row in raw_rows)
    if len(packet_ids) != 18 or len(set(packet_ids)) != 18:
        raise ValueError("work-packet catalog must contain 18 unique packets")
    if set(packet_ids) != set(_PACKET_CONTRACTS):
        raise ValueError("work-packet IDs must match the frozen 18-packet contract")
    packets = []
    for index, (packet_id, title, dependencies, scope, exit_gate) in enumerate(raw_rows):
        contract = _PACKET_CONTRACTS[packet_id]
        packet = PacketSpec(
            packet_id=packet_id,
            title=title.strip(),
            dependencies=_expand_dependencies(dependencies, packet_ids, packet_id),
            primary_scope=scope.strip(),
            exit_gate=exit_gate.strip(),
            bounded_paths=contract["bounded_paths"],
            bounded_symbols=contract["bounded_symbols"],
            non_goals=contract["non_goals"],
            evidence_contract=contract["evidence_contract"],
            rollback=contract["rollback"],
            attempt_budget=_packet_budget(packet_id),
            owner=f"loop-surgeon:{packet_id}",
            reviewer=f"loop-reviewer:{packet_id}",
            tester=f"Tester:{packet_id}",
            issue_id=f"bb-auh.{50 + index}",
        )
        packet.validate()
        packets.append(packet)
    result = tuple(packets)
    _validate_packet_dag(result)
    return result


def _validate_packet_dag(packets: Sequence[PacketSpec]) -> tuple[str, ...]:
    by_id = {packet.packet_id: packet for packet in packets}
    if len(by_id) != len(packets):
        raise ValueError("work-packet IDs must be unique")
    if len({packet.owner for packet in packets}) != len(packets):
        raise ValueError("every work packet must have one unique owner")
    if len({packet.reviewer for packet in packets}) != len(packets):
        raise ValueError("every work packet must have one independent reviewer")
    for packet in packets:
        packet.validate()
    indegree = {packet.packet_id: len(packet.dependencies) for packet in packets}
    dependents: dict[str, list[str]] = defaultdict(list)
    for packet in packets:
        for dependency in packet.dependencies:
            if dependency not in by_id:
                raise ValueError(f"work packet dependency is unknown: {dependency}")
            dependents[dependency].append(packet.packet_id)
    queue = deque(sorted(packet_id for packet_id, degree in indegree.items() if degree == 0))
    ordered: list[str] = []
    while queue:
        packet_id = queue.popleft()
        ordered.append(packet_id)
        for dependent in sorted(dependents[packet_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if len(ordered) != len(packets):
        raise ValueError("work-packet dependency graph must be acyclic")
    return tuple(ordered)


def _validate_branch_rules(text: str) -> None:
    rules = (
        "Current forced GRPO cannot count as PPO.",
        "PPO mode fails closed until value/critic/GAE prerequisites exist.",
        "Activate only when a pre-run decision names a portability, runsc, concurrency, or independent-host question not answered by IBM/local.",
        "if absent or incompatible, preserve exact versions/logs and continue the hardened Docker single-tenant scope.",
        "No branch can substitute for IBM training.",
    )
    lower_text = text.lower()
    for rule in rules:
        if rule.lower() not in lower_text:
            raise ValueError(f"playbook is missing conditional branch rule: {rule}")


def _packet_transitions() -> tuple[TransitionSpec, ...]:
    rows = (
        ("PLANNED", "submit_for_admission", "ADMISSION_REVIEW", ("complete_packet_contract",)),
        ("ADMISSION_REVIEW", "supervisor_admits", "ADMITTED", ("p0_contracts_valid", "dependencies_current", "budget_available")),
        ("ADMITTED", "dependencies_ready", "READY", ("all_dependencies_satisfied", "packet_lineage_current")),
        ("READY", "worker_claims_packet", "CLAIMED", ("isolated_worktree_assigned", "bounded_scope_acknowledged")),
        ("CLAIMED", "worker_starts", "EXECUTING", ("claim_current", "attempt_budget_remains")),
        ("EXECUTING", "worker_completes", "VERIFYING", ("artifacts_hash_bound", "worker_did_not_self_approve")),
        ("VERIFYING", "verification_passes", "AWAITING_REVIEW", ("evidence_contract_passes", "evidence_current")),
        ("AWAITING_REVIEW", "review_passes", "SATISFIED", ("current_hash_review_passes", "supervisor_records_satisfaction")),
        ("AWAITING_REVIEW", "review_blocks", "CHANGES_REQUESTED", ("current_hash_blocking_finding",)),
        ("CHANGES_REQUESTED", "fix_plan_keeps_contract", "READY", ("scope_still_valid", "fix_plan_reviewed", "dependencies_current", "attempt_budget_remains")),
        ("CHANGES_REQUESTED", "contract_changes", "ADMISSION_REVIEW", ("replacement_contract_recorded", "new_lineage_created")),
        ("EXECUTING", "internal_blocker_recorded", "BLOCKED_INTERNAL", ("exact_internal_blocker", "owner_and_next_action_recorded")),
        ("BLOCKED_INTERNAL", "replacement_plan_ready", "ADMISSION_REVIEW", ("root_cause_recorded", "replacement_plan_recorded", "not_external_disguise")),
        ("EXECUTING", "external_blocker_recorded", "BLOCKED_EXTERNAL", ("typed_external_blocker", "owner_and_wake_condition_recorded")),
        ("BLOCKED_EXTERNAL", "wake_artifact_arrives", "READY", ("wake_artifact_hash_and_size_verified", "dependencies_current", "attempt_budget_remains")),
        ("EXECUTING", "attempt_budget_exhausted", "BUDGET_EXHAUSTED", ("attempts_immutable", "budget_exactly_exhausted")),
        ("BUDGET_EXHAUSTED", "supervisor_escalates", "ESCALATED", ("attempts_and_failure_classes_recorded", "exact_decision_needed_recorded")),
        ("ESCALATED", "human_replan_approved", "ADMISSION_REVIEW", ("named_human_authority_current", "replacement_budget_explicit", "old_attempts_immutable")),
        ("SATISFIED", "hard_invalidator_fires", "EVIDENCE_STALE", ("earliest_unsupported_node_identified", "dependency_descendants_invalidated")),
        ("EVIDENCE_STALE", "replacement_lineage_approved", "ADMISSION_REVIEW", ("all_invalidated_descendants_named", "required_reruns_named")),
        ("EXECUTING", "authority_revokes", "REVOKED", ("current_revocation_record", "dependent_evidence_quarantined")),
        ("SATISFIED", "authority_revokes", "REVOKED", ("current_revocation_record", "dependent_evidence_quarantined")),
        ("REVOKED", "new_lineage_authorized", "ADMISSION_REVIEW", ("revocation_cause_remediated", "named_authority_current", "revoked_evidence_remains_noncurrent")),
    )
    transitions = tuple(
        TransitionSpec(from_state, event, to_state, guards)
        for from_state, event, to_state, guards in rows
    )
    for transition in transitions:
        transition.validate()
    side_states = {
        "CHANGES_REQUESTED", "BLOCKED_INTERNAL", "BLOCKED_EXTERNAL",
        "EVIDENCE_STALE", "BUDGET_EXHAUSTED", "ESCALATED", "REVOKED",
    }
    if any(
        transition.from_state in side_states and transition.to_state == "SATISFIED"
        for transition in transitions
    ):
        raise ValueError("no side state may return directly to SATISFIED")
    return transitions


def _rollback_contract() -> dict[str, Any]:
    return {
        "target": "last approved BreadBoard head, wrapper head, compiler, admission policy, runtime image, model/checkpoint, and config-set digest tuple",
        "ordered_actions": [
            "publish_new_immutable_config_set_manifest_excluding_bad_variant",
            "cancel_outstanding_episodes",
            "revoke_secret_and_route_leases",
            "reconcile_runtime_resources",
            "quarantine_dependent_rewards_checkpoints_and_evidence",
            "update_active_status_to_new_manifest",
        ],
        "fallback_denials": [
            "legacy_parser",
            "profile_execution",
            "mutable_paths",
            "unknown_provider_to_openai",
            "unknown_driver_to_process",
            "another_weighted_candidate",
            "stale_cache_content",
        ],
        "compatibility_replay_requires_reapproval": True,
        "compatibility_replay_disposition_before_reapproval": "diagnostic_only",
    }


def _invalidation_contract() -> dict[str, Any]:
    return {
        "hard_invalidators": [
            "source_or_head",
            "closure_byte",
            "compiler",
            "policy",
            "schema",
            "admitted_set",
            "selector",
            "weight",
            "seed",
            "overlay",
            "image",
            "runtime",
            "security",
            "task",
            "data",
            "model",
            "checkpoint",
            "verifier",
            "launcher",
            "dependency",
            "command",
            "threshold",
            "test",
            "target_job",
            "target_node",
            "raw_log_or_artifact",
            "cleanup",
            "review_hash",
            "authority_scope",
        ],
        "propagation": "dependency_descendants_from_earliest_unsupported_node",
        "historical_artifacts_immutable": True,
        "failed_rerun_invalidates_previous_success": True,
        "unrelated_evidence_remains_current": True,
        "chain": [
            "bundle", "closure", "compiler", "admission", "config_set", "selection",
            "overlays", "effective_plan", "policy_runtime", "sandbox_verifier",
            "episode_training_artifacts", "claim", "review", "point", "promotion",
        ],
    }


def validate_campaign(
    *, playbook_path: Path, goal_prompt_path: Path, generated_at: str
) -> CampaignSpec:
    _parse_generated_at(generated_at)
    playbook_path = playbook_path.resolve()
    goal_prompt_path = goal_prompt_path.resolve()
    if not playbook_path.is_file():
        raise ValueError(f"playbook does not exist: {playbook_path}")
    if not goal_prompt_path.is_file():
        raise ValueError(f"goal prompt does not exist: {goal_prompt_path}")
    text = playbook_path.read_text()
    prompt = goal_prompt_path.read_text()
    for anchor in _REQUIRED_PLAYBOOK_ANCHORS:
        if anchor not in text:
            raise ValueError(f"playbook is missing required governance rule: {anchor}")
    _validate_branch_rules(text)
    frontmatter = _frontmatter(text)
    expected_frontmatter = {
        "score_total": FIXED_CATALOG_POINTS,
        "current_verified_points": 0,
        "breadboard_baseline": BREADBOARD_BASELINE,
        "wrapper_baseline": WRAPPER_BASELINE,
        "evidence_root": "/Users/kylemccleary/projects/breadboard/docs_tmp",
        "external_acceptance_state": "unclaimed",
        "scorecard_update_allowed": False,
        "promotion_authorized": False,
    }
    for key, expected in expected_frontmatter.items():
        if frontmatter.get(key) != expected:
            raise ValueError(f"playbook frontmatter {key} must equal {expected!r}")
    if "Keep external Zyphra acceptance, scorecard authority, and promotion false/null/unclaimed" not in prompt:
        raise ValueError("goal prompt must preserve external authority non-claims")
    catalog = parse_score_catalog(text)
    ScoreEngine(catalog)
    packets = _parse_packets(text)
    packet_ids = {packet.packet_id for packet in packets}
    if any(item.owner_packet not in packet_ids for item in catalog):
        raise ValueError("every score item must have exactly one known packet owner")
    proof_floors = {floor: evidence.strip() for floor, evidence in _PROOF_FLOOR_ROW.findall(text)}
    if set(proof_floors) != {
        "governance", "local contract", "local process", "local container",
        "IBM target", "target training", "DigitalOcean", "authority",
    }:
        raise ValueError("proof-floor catalog must contain the eight frozen labels")
    frozen_hashes = {
        "breadboard_baseline": {
            "head": BREADBOARD_BASELINE,
            "canonical_payload_format": "git_archive",
            "canonical_payload_sha256": BREADBOARD_CANONICAL_PAYLOAD_SHA256,
        },
        "wrapper_baseline": {
            "head": WRAPPER_BASELINE,
            "canonical_payload_format": "git_archive",
            "canonical_payload_sha256": WRAPPER_CANONICAL_PAYLOAD_SHA256,
        },
        "playbook": {"path": str(playbook_path), "sha256": _sha256_file(playbook_path)},
        "goal_prompt": {"path": str(goal_prompt_path), "sha256": _sha256_file(goal_prompt_path)},
        "issue_mapping_sha256": ISSUE_MAPPING_SHA256,
    }
    if frozen_hashes["breadboard_baseline"]["canonical_payload_sha256"] != BREADBOARD_CANONICAL_PAYLOAD_SHA256:
        raise ValueError("BreadBoard canonical payload digest mismatch")
    if frozen_hashes["wrapper_baseline"]["canonical_payload_sha256"] != WRAPPER_CANONICAL_PAYLOAD_SHA256:
        raise ValueError("wrapper canonical payload digest mismatch")
    catalog_payload = [item.model_dump(mode="json") for item in catalog]
    catalog_sha256 = _sha256_bytes(_canonical_json_bytes(catalog_payload))
    if catalog_sha256 != FIXED_CATALOG_SHA256:
        raise ValueError("catalog payload does not match the frozen digest")
    packet_payload = [_packet_payload(packet, state=None) for packet in packets]
    campaign_spec_payload = {
        "schema_version": f"{SCHEMA_PREFIX}.campaign_spec.v2",
        "catalog": catalog_payload,
        "packets": packet_payload,
        "proof_floors": proof_floors,
        "frozen_hashes": frozen_hashes,
        "transitions": [_transition_payload(value) for value in _packet_transitions()],
        "rollback": _rollback_contract(),
        "invalidation": _invalidation_contract(),
        "branch_items": ["F9", "F10"],
        "review_roles": ["loop-reviewer", "reviewer", "Tester"],
    }
    campaign_spec_sha256 = _sha256_bytes(_canonical_json_bytes(campaign_spec_payload))
    return CampaignSpec(
        playbook_path=playbook_path,
        goal_prompt_path=goal_prompt_path,
        generated_at=generated_at,
        catalog=catalog,
        packets=packets,
        frontmatter=frontmatter,
        frozen_hashes=frozen_hashes,
        proof_floors=proof_floors,
        catalog_sha256=catalog_sha256,
        campaign_spec_sha256=campaign_spec_sha256,
    )


def _base(spec: CampaignSpec, artifact: str) -> dict[str, Any]:
    return {
        "schema_version": f"{SCHEMA_PREFIX}.{artifact}.v2",
        "generated_at": spec.generated_at,
        "campaign_spec_sha256": spec.campaign_spec_sha256,
    }


def _scorecard(spec: CampaignSpec) -> dict[str, Any]:
    counts = Counter(item.workstream for item in spec.catalog)
    points = Counter()
    for item in spec.catalog:
        points[item.workstream] += item.points
    items = []
    for index, item in enumerate(spec.catalog, start=1):
        row = item.model_dump(mode="json")
        row.update({
            "issue_id": f"bb-auh.{index}",
            "state": ScoreItemState.PENDING.value,
            "awarded_points": 0,
            "evidence_ids": [],
            "review_ids": [],
            "supervisor_decision_id": None,
        })
        items.append(row)
    return {
        **_base(spec, "scorecard"),
        "catalog_sha256": spec.catalog_sha256,
        "frozen_hashes": spec.frozen_hashes,
        "item_count": len(spec.catalog),
        "catalog_points": sum(item.points for item in spec.catalog),
        "current_verified_points": 0,
        "internal_completion": False,
        "external_acceptance_state": "unclaimed",
        "scorecard_update_allowed": False,
        "promotion_authorized": False,
        "checkpoint_disposition": None,
        "workstream_counts": dict(sorted(counts.items())),
        "workstream_points": dict(sorted(points.items())),
        "items": items,
    }


def _claim_record(
    claim_id: str, subject: str, claim: str, non_claim: str, proof_floor: str
) -> dict[str, Any]:
    return ClaimRecord(
        claim_id=claim_id,
        subject=subject,
        claim_state=ClaimState.UNCLAIMED,
        claim=claim,
        non_claims=(non_claim,),
        proof_floor=proof_floor,
    ).model_dump(mode="json")


def _taxonomy(spec: CampaignSpec) -> dict[str, Any]:
    return {
        **_base(spec, "evidence_taxonomy"),
        "evidence_classes": [value.value for value in EvidenceClass],
        "support_levels": [value.value for value in SupportLevel],
        "evidence_states": [value.value for value in EvidenceState],
        "campaign_dispositions": [value.value for value in CampaignDisposition],
        "claim_states": [value.value for value in ClaimState],
        "blocker_kinds": [value.value for value in BlockerKind],
        "blocker_failure_classes": [value.value for value in BlockerFailureClass],
        "blocker_states": [value.value for value in BlockerState],
        "proof_floors": spec.proof_floors,
        "point_eligible_support": ["observed", "derived_deterministically"],
        "never_point_eligible_support": ["inferred", "worker_claim", "unverified", "contradicted"],
        "claim_record_schema": {
            "required": ["claim_id", "subject", "claim_state", "claim", "non_claims", "proof_floor", "evidence_ids", "review_ids"],
            "exact_non_claim_pairing": True,
        },
        "blocker_record_schema": {
            "required": [
                "blocker_id", "blocker_kind", "failure_class", "affected_packet_ids",
                "affected_score_item_ids", "owner_identity", "wake_condition", "next_action",
                "state", "opened_at", "evidence_ids", "evidence_hashes",
            ],
            "ready_wake_requires_verified_artifact": True,
        },
        "authority_synthesis_sources": [],
        "authority_requires_explicit_current_record": True,
    }


def _campaign_matrix(spec: CampaignSpec) -> dict[str, Any]:
    lanes = [
        ("C0", "provenance freeze", "offline/local", "identity only", "governance"),
        ("C1", "compiler and admission", "local server/compiler/CAS; no lease", "no runtime", "local contract"),
        ("C2", "local lifecycle calibration", "local Ray and trusted-process driver", "non-isolated local process", "local process"),
        ("C3", "mandatory Docker real SWE", "Linux Docker host", "one-task Docker wiring only", "local container"),
        ("C3G", "conditional gVisor", "Linux Docker host with runsc preflight", "no gVisor or multi-tenant claim unless activated evidence passes", "local container"),
        ("C4", "local full seam", "local full seam", "local full seam only", "local process"),
        ("C5", "IBM one-node", "ZYPHRA_IBM_AMD_1/gpu", "exact one-node campaign only", "IBM target"),
        ("C6", "config-family and mutation A/B", "local Docker then IBM one-node", "controlled config causality, not learned superiority", "IBM target"),
        ("C7", "concurrency and mixed configs", "local and IBM", "tested concurrency only", "IBM target"),
        ("C8", "failure injection", "local and safe target faults", "enumerated fault containment, not general HA", "IBM target"),
        ("C9", "restart and replay", "local/target as declared", "no exactly-once live training or durable trainer resume", "IBM target"),
        ("C10", "two-node and four-node scale", "IBM Slurm", "no distributed task-execution claim for head-local placement", "IBM target"),
        ("C11", "GRPO smoke and bounded learning-signal run", "IBM Slurm", "finite-step optimizer signal, not convergence or benchmark gain", "target training"),
        ("C12", "estimator-truth / conditional PPO", "IBM Slurm", "no PPO claim without real value/critic/GAE evidence", "target training"),
        ("C13", "optional DigitalOcean", "conditional x86_64 Linux Droplet", "no IBM, Slurm, ROCm, Zyphra GPU, production provider, or external acceptance claim", "DigitalOcean"),
    ]
    campaigns = []
    for lane_id, name, environment, non_claim, proof_floor in lanes:
        record = _claim_record(
            f"claim:{lane_id}",
            f"campaign:{lane_id}",
            f"{name} passed at the declared {proof_floor} floor",
            non_claim,
            proof_floor,
        )
        campaigns.append({
            "campaign_id": lane_id,
            "name": name,
            "environment": environment,
            "disposition": "pending",
            "activated": False,
            "claim_state": record["claim_state"],
            "claim_record": record,
            "evidence_ids": [],
        })
    return {
        **_base(spec, "campaign_matrix"),
        "current_verified_points": 0,
        "external_acceptance_state": "unclaimed",
        "promotion_authorized": False,
        "campaigns": campaigns,
    }


def _fixture_manifest(spec: CampaignSpec) -> dict[str, Any]:
    return {
        **_base(spec, "fixture_manifest"),
        "fixtures": [{
            "fixture_id": "R-SWE-001",
            "state": "pending",
            "approval_owner": "Kyle McCleary",
            "source_url": "https://huggingface.co/datasets/nvidia/Nemotron-RL-Ultra-Training-Blends/resolve/676b3c63c81a0526511d50f05ee46589024642fd/swe.jsonl",
            "dataset_revision": "676b3c63c81a0526511d50f05ee46589024642fd",
            "source_dataset": "nebius/SWE-rebench-V2",
            "instance_id": "python-markdown__markdown-1529",
            "expected_control_rewards": {"gold": 1, "known_bad": 0, "no_op": 0},
            "source_row_sha256": None,
            "canonical_row_sha256": None,
            "repository_tree_sha256": None,
            "image_digest": None,
            "verifier_digest": None,
            "approval_authority_id": None,
            "claims": [],
        }],
    }


def _variant_catalog(spec: CampaignSpec) -> dict[str, Any]:
    variants = (
        ("V0", "canonical baseline", "control"),
        ("V1", "lower max_turns within admitted bounds", "evaluation and termination attribution"),
        ("V2", "remove one nonessential tool", "evaluation; capability reduction"),
        ("V3", "lower action timeout around a controlled sleep", "timeout/cleanup attribution"),
        ("V4", "tighten artifact allowlist/size", "evidence-policy attribution"),
        ("V5", "prompt or mode mutation", "evaluation/training only when reward-equivalent"),
        ("V6", "sampling mutation within frozen bounds", "optimization trial"),
        ("V7", "generated unknown-name config with supported ABI", "deletion test"),
    )
    rows = []
    for variant_id, delta, expected_use in variants:
        claim = _claim_record(
            f"claim:variant:{variant_id}",
            f"variant:{variant_id}",
            f"{variant_id} is admitted for {expected_use}",
            "no superiority, promotion, or external-acceptance claim",
            "local contract",
        )
        rows.append({
            "variant_id": variant_id,
            "delta": delta,
            "expected_use": expected_use,
            "state": "pending",
            "admission_disposition": "pending",
            "selector_weight": None,
            "claim_state": claim["claim_state"],
            "claim_record": claim,
        })
    return {
        **_base(spec, "variant_catalog"),
        "config_set_digest": None,
        "selection_weights_frozen": False,
        "promotion_authorized": False,
        "variants": rows,
    }


def _packet_payload(packet: PacketSpec, state: str | None) -> dict[str, Any]:
    payload = {
        "packet_id": packet.packet_id,
        "issue_id": packet.issue_id,
        "title": packet.title,
        "dependencies": list(packet.dependencies),
        "primary_scope": packet.primary_scope,
        "exit_gate": packet.exit_gate,
        "files": list(packet.bounded_paths),
        "symbols": list(packet.bounded_symbols),
        "non_goals": list(packet.non_goals),
        "evidence_contract": list(packet.evidence_contract),
        "rollback": list(packet.rollback),
        "attempt_budget": asdict(packet.attempt_budget),
        "owner": packet.owner,
        "reviewer": packet.reviewer,
        "tester": packet.tester,
    }
    if state is not None:
        payload["state"] = state
    return payload


def _work_packet_dag(spec: CampaignSpec) -> dict[str, Any]:
    order = _validate_packet_dag(spec.packets)
    score_issues = {
        item.item_id: {"issue_id": f"bb-auh.{index}", "owner_packet": item.owner_packet}
        for index, item in enumerate(spec.catalog, start=1)
    }
    packets = []
    for packet in spec.packets:
        row = _packet_payload(packet, "ADMITTED" if packet.packet_id == "WP0" else "PLANNED")
        row["score_items"] = [
            item.item_id for item in spec.catalog if item.owner_packet == packet.packet_id
        ]
        packets.append(row)
    return {
        **_base(spec, "work_packet_dag"),
        "epic_id": EPIC_ID,
        "issue_mapping_sha256": ISSUE_MAPPING_SHA256,
        "acyclic": True,
        "contract_validated": True,
        "topological_order": list(order),
        "score_item_issues": score_issues,
        "packets": packets,
    }


def _transition_payload(transition: TransitionSpec) -> dict[str, Any]:
    return {
        "from_state": transition.from_state,
        "event": transition.event,
        "to_state": transition.to_state,
        "guards": list(transition.guards),
    }


def _loop_spec(spec: CampaignSpec) -> dict[str, Any]:
    return {
        **_base(spec, "loop_spec"),
        "p0_contracts_validated": True,
        "supervisor_owns": [
            "packet_admission", "worktree_isolation", "evidence_promotion",
            "point_decisions", "reviews", "retries", "stop_posture",
        ],
        "roles": {
            "architect": "loop-architect",
            "implementer": "loop-surgeon",
            "test_author": "Tester",
            "independent_reviewer": ["loop-reviewer", "reviewer"],
            "ci_diagnostician": "ci-loop-diagnostician",
            "parity_researcher": "parity-oracle",
        },
        "primary_states": [
            "PLANNED", "ADMISSION_REVIEW", "ADMITTED", "READY", "CLAIMED",
            "EXECUTING", "VERIFYING", "AWAITING_REVIEW", "SATISFIED",
        ],
        "side_states": [
            "CHANGES_REQUESTED", "BLOCKED_INTERNAL", "BLOCKED_EXTERNAL",
            "EVIDENCE_STALE", "BUDGET_EXHAUSTED", "ESCALATED", "REVOKED",
        ],
        "transitions": [_transition_payload(value) for value in _packet_transitions()],
        "worker_completion_state": "VERIFYING",
        "worker_can_award_points": False,
        "worker_can_self_approve": False,
        "budgets": {
            "planner_proposals_per_packet": 1,
            "supervisor_corrections_per_packet": 1,
            "implementation_attempts_before_escalation": 2,
            "identical_local_flake_reruns": 1,
            "scratch_target_attempts": 1,
            "scale_training_attempts_per_topology_recipe": 1,
            "exact_transient_infrastructure_retries": 1,
            "reviewer_repair_rounds": 2,
            "normal_parallel_write_packets": 2,
            "maximum_disjoint_parallel_write_packets": 3,
            "canonical_promotion_concurrency": 1,
            "ledger_mutation_concurrency": 1,
            "checkpoint_promotion_concurrency": 1,
            "final_assembly_concurrency": 1,
            "final_review_concurrency": 1,
        },
        "branches": {
            "F9_PPO": {
                "ppo_enabled_trigger": "real value/critic/GAE prerequisites exist",
                "disabled_disposition": "DISABLED_WITH_REQUIRED_NONCLAIM",
                "forced_grpo_can_claim_ppo": False,
            },
            "F10_RUNSC": {
                "trigger": "effective CPU-task runsc preflight is compatible",
                "incompatible_disposition": "INFEASIBLE_WITH_REQUIRED_NONCLAIM",
                "fallback_scope": "hardened Docker single-tenant",
                "gvisor_claim_without_parity": False,
            },
            "F10_DIGITALOCEAN": {
                "trigger": "pre-run decision names an approved portability, runsc, concurrency, or independent-host question not answered by IBM/local",
                "not_triggered_disposition": "NOT_TRIGGERED",
                "substitutes_for_ibm": False,
            },
        },
        "rollback": _rollback_contract(),
        "invalidation": _invalidation_contract(),
    }


def _claim_ledger(spec: CampaignSpec) -> str:
    hashes = spec.frozen_hashes
    return "\n".join([
        "# Phase 5 claim ledger",
        "",
        f"generated_at: {spec.generated_at}",
        f"campaign_spec_sha256: {spec.campaign_spec_sha256}",
        f"catalog_sha256: {spec.catalog_sha256}",
        f"breadboard_head: {BREADBOARD_BASELINE}",
        f"breadboard_canonical_payload_sha256: {hashes['breadboard_baseline']['canonical_payload_sha256']}",
        f"wrapper_head: {WRAPPER_BASELINE}",
        f"wrapper_canonical_payload_sha256: {hashes['wrapper_baseline']['canonical_payload_sha256']}",
        f"playbook_sha256: {hashes['playbook']['sha256']}",
        f"goal_prompt_sha256: {hashes['goal_prompt']['sha256']}",
        f"issue_mapping_sha256: {ISSUE_MAPPING_SHA256}",
        "score_items: 49",
        "catalog_points: 1000",
        "current_verified_points: 0",
        "internal_completion: false",
        "checkpoint_disposition: null",
        "external_acceptance_state: unclaimed",
        "scorecard_update_allowed: false",
        "promotion_authorized: false",
        "authority_artifacts: []",
        "claims: []",
        "",
        "## Exact non-claims",
        "",
        "- No local, Docker, gVisor, Slurm, DigitalOcean, SWE, performance, training, or external-acceptance claim follows from this bootstrap.",
        "- No PPO support, durable trainer resume, DigitalOcean parity, Zyphra infrastructure ownership, checkpoint publication authority, or promotion is claimed.",
        "- Hash integrity, issue state, job state, review state, score state, and evidence state confer no authority.",
        "",
    ])


def _child_payloads(spec: CampaignSpec) -> dict[str, str]:
    values: dict[str, Any] = {
        "SCORECARD.json": _scorecard(spec),
        "EVIDENCE_TAXONOMY.json": _taxonomy(spec),
        "CAMPAIGN_MATRIX.yaml": _campaign_matrix(spec),
        "FIXTURE_MANIFEST.json": _fixture_manifest(spec),
        "VARIANT_CATALOG.json": _variant_catalog(spec),
        "WORK_PACKET_DAG.yaml": _work_packet_dag(spec),
        "LOOP_SPEC.yaml": _loop_spec(spec),
    }
    payloads = {filename: _pretty_json(value) for filename, value in values.items()}
    payloads["CLAIM_LEDGER.md"] = _claim_ledger(spec)
    return {filename: payloads[filename] for filename in CHILD_ARTIFACT_FILENAMES}


def _artifact_manifest(spec: CampaignSpec, children: Mapping[str, str]) -> dict[str, Any]:
    entries = []
    for filename in CHILD_ARTIFACT_FILENAMES:
        data = children[filename].encode()
        entries.append({
            "filename": filename,
            "media_type": _MEDIA_TYPES[filename],
            "size": len(data),
            "sha256": _sha256_bytes(data),
        })
    return {
        **_base(spec, "artifact_manifest"),
        "self_reference_forbidden": True,
        "active_status_excluded": True,
        "artifacts": entries,
    }


def _active_status(
    spec: CampaignSpec, manifest_bytes: bytes
) -> dict[str, Any]:
    return {
        **_base(spec, "active_status"),
        "active_status_id": "phase5-initial-status",
        "active": True,
        "campaign_state": "READY",
        "p0_contracts_validated": True,
        "current_verified_points": 0,
        "catalog_points": FIXED_CATALOG_POINTS,
        "internal_completion": False,
        "checkpoint_disposition": None,
        "external_acceptance_state": "unclaimed",
        "scorecard_update_allowed": False,
        "promotion_authorized": False,
        "external_acceptance_authority_id": None,
        "promotion_authority_id": None,
        "epic_id": EPIC_ID,
        "issue_mapping_sha256": ISSUE_MAPPING_SHA256,
        "admitted_packets": ["WP0"],
        "artifact_manifest_pointer": {
            "filename": "ARTIFACT_MANIFEST.json",
            "media_type": _MEDIA_TYPES["ARTIFACT_MANIFEST.json"],
            "size": len(manifest_bytes),
            "sha256": _sha256_bytes(manifest_bytes),
        },
        "artifact_pointers": {
            "scorecard": "SCORECARD.json",
            "campaign_matrix": "CAMPAIGN_MATRIX.yaml",
            "claim_ledger": "CLAIM_LEDGER.md",
            "evidence_taxonomy": "EVIDENCE_TAXONOMY.json",
            "fixture_manifest": "FIXTURE_MANIFEST.json",
            "variant_catalog": "VARIANT_CATALOG.json",
            "work_packet_dag": "WORK_PACKET_DAG.yaml",
            "loop_spec": "LOOP_SPEC.yaml",
            "evidence_index": None,
            "final_packet": None,
        },
        "authorities": [],
        "claims": [],
        "open_blockers": [],
        "stale_evidence_ids": [],
    }


def _payloads(spec: CampaignSpec) -> dict[str, str]:
    children = _child_payloads(spec)
    manifest_text = _pretty_json(_artifact_manifest(spec, children))
    active_text = _pretty_json(_active_status(spec, manifest_text.encode()))
    return {
        **children,
        "ARTIFACT_MANIFEST.json": manifest_text,
        "ACTIVE_STATUS.json": active_text,
    }


def _validate_outputs(spec: CampaignSpec, output_dir: Path) -> None:
    paths = {filename: output_dir / filename for filename in ARTIFACT_FILENAMES}
    missing = sorted(filename for filename, path in paths.items() if not path.is_file())
    if missing:
        raise ValueError(f"bootstrap output is missing artifact: {missing[0]}")
    expected = _payloads(spec)
    for filename, expected_text in expected.items():
        if paths[filename].read_bytes() != expected_text.encode():
            raise ValueError(f"bootstrap artifact is not canonical: {filename}")
    manifest_bytes = paths["ARTIFACT_MANIFEST.json"].read_bytes()
    manifest = json.loads(manifest_bytes)
    entries = manifest.get("artifacts")
    if not isinstance(entries, list):
        raise ValueError("artifact manifest artifacts must be a list")
    filenames = [entry.get("filename") for entry in entries if isinstance(entry, dict)]
    if tuple(filenames) != CHILD_ARTIFACT_FILENAMES:
        raise ValueError("artifact manifest must select every child exactly once")
    if "ARTIFACT_MANIFEST.json" in filenames or "ACTIVE_STATUS.json" in filenames:
        raise ValueError("artifact manifest cannot reference itself or ACTIVE_STATUS")
    for entry in entries:
        filename = entry["filename"]
        child_bytes = paths[filename].read_bytes()
        if entry != {
            "filename": filename,
            "media_type": _MEDIA_TYPES[filename],
            "size": len(child_bytes),
            "sha256": _sha256_bytes(child_bytes),
        }:
            raise ValueError(f"artifact manifest hash/size/media mismatch: {filename}")
    active = json.loads(paths["ACTIVE_STATUS.json"].read_bytes())
    pointer = active.get("artifact_manifest_pointer")
    expected_pointer = {
        "filename": "ARTIFACT_MANIFEST.json",
        "media_type": _MEDIA_TYPES["ARTIFACT_MANIFEST.json"],
        "size": len(manifest_bytes),
        "sha256": _sha256_bytes(manifest_bytes),
    }
    if pointer != expected_pointer:
        raise ValueError("active status manifest pointer is not hash-bound to current bytes")
    selected = {
        value for value in active["artifact_pointers"].values() if value is not None
    }
    if selected != set(CHILD_ARTIFACT_FILENAMES):
        raise ValueError("active status must select exactly the manifest child artifacts")
    scorecard = json.loads(paths["SCORECARD.json"].read_bytes())
    if scorecard["item_count"] != FIXED_ITEM_COUNT or scorecard["catalog_points"] != FIXED_CATALOG_POINTS:
        raise ValueError("generated scorecard must contain 49 items totaling 1000")
    if scorecard["catalog_sha256"] != FIXED_CATALOG_SHA256:
        raise ValueError("generated scorecard catalog digest mismatch")
    if scorecard["current_verified_points"] != 0:
        raise ValueError("generated scorecard must begin at zero verified points")
    if any(item["state"] != "pending" or item["awarded_points"] != 0 for item in scorecard["items"]):
        raise ValueError("every generated score item must be pending with zero awarded points")
    baselines = scorecard["frozen_hashes"]
    if baselines["breadboard_baseline"] != {
        "head": BREADBOARD_BASELINE,
        "canonical_payload_format": "git_archive",
        "canonical_payload_sha256": BREADBOARD_CANONICAL_PAYLOAD_SHA256,
    }:
        raise ValueError("BreadBoard baseline head/payload identity mismatch")
    if baselines["wrapper_baseline"] != {
        "head": WRAPPER_BASELINE,
        "canonical_payload_format": "git_archive",
        "canonical_payload_sha256": WRAPPER_CANONICAL_PAYLOAD_SHA256,
    }:
        raise ValueError("wrapper baseline head/payload identity mismatch")
    if (
        active["campaign_state"] != "READY"
        or active["p0_contracts_validated"] is not True
        or active["admitted_packets"] != ["WP0"]
    ):
        raise ValueError("READY/WP0 admission requires every P0 contract")
    if (
        active["external_acceptance_state"] != "unclaimed"
        or active["promotion_authorized"] is not False
        or active["scorecard_update_allowed"] is not False
        or active["current_verified_points"] != 0
    ):
        raise ValueError("external acceptance, promotion, and points must remain unclaimed/false/zero")
    for raw in active["open_blockers"]:
        BlockerRecord.model_validate(raw)
    dag = json.loads(paths["WORK_PACKET_DAG.yaml"].read_bytes())
    if dag["acyclic"] is not True or len(dag["packets"]) != 18 or dag["contract_validated"] is not True:
        raise ValueError("generated work-packet graph must contain a validated acyclic 18-packet catalog")
    for raw, packet in zip(dag["packets"], spec.packets, strict=True):
        expected_state = "ADMITTED" if packet.packet_id == "WP0" else "PLANNED"
        expected_packet = _packet_payload(packet, expected_state)
        expected_packet["score_items"] = [
            item.item_id for item in spec.catalog if item.owner_packet == packet.packet_id
        ]
        if raw != expected_packet:
            raise ValueError(f"generated packet contract mismatch: {packet.packet_id}")
    taxonomy = json.loads(paths["EVIDENCE_TAXONOMY.json"].read_bytes())
    if taxonomy["claim_states"] != [value.value for value in ClaimState]:
        raise ValueError("claim-state taxonomy is not closed")
    matrix = json.loads(paths["CAMPAIGN_MATRIX.yaml"].read_bytes())
    for campaign in matrix["campaigns"]:
        record = ClaimRecord.model_validate(campaign["claim_record"])
        if campaign["claim_state"] != record.claim_state.value:
            raise ValueError("campaign claim state disagrees with typed claim record")
    variants = json.loads(paths["VARIANT_CATALOG.json"].read_bytes())
    for variant in variants["variants"]:
        record = ClaimRecord.model_validate(variant["claim_record"])
        if variant["claim_state"] != record.claim_state.value:
            raise ValueError("variant claim state disagrees with typed claim record")
    loop_spec = json.loads(paths["LOOP_SPEC.yaml"].read_bytes())
    if loop_spec["transitions"] != [_transition_payload(value) for value in _packet_transitions()]:
        raise ValueError("loop transition table is incomplete")
    if loop_spec["budgets"]["scale_training_attempts_per_topology_recipe"] != 1:
        raise ValueError("scale/training topology/recipe budget must equal one")
    if loop_spec["budgets"]["exact_transient_infrastructure_retries"] != 1:
        raise ValueError("exact infrastructure retry budget must equal one")
    if loop_spec["rollback"] != _rollback_contract():
        raise ValueError("rollback contract is incomplete")
    if loop_spec["invalidation"] != _invalidation_contract():
        raise ValueError("invalidation contract is incomplete")


def _legacy_lineage_key(output_dir: Path) -> str:
    active_path = output_dir / "ACTIVE_STATUS.json"
    if active_path.is_file():
        try:
            value = json.loads(active_path.read_bytes()).get("campaign_spec_sha256")
        except (json.JSONDecodeError, AttributeError):
            value = None
        if isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            return value.removeprefix("sha256:")
    digest = hashlib.sha256()
    for filename in _LEGACY_ARTIFACT_FILENAMES:
        path = output_dir / filename
        if path.is_file():
            digest.update(filename.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _archive_rejected_legacy(output_dir: Path, generated_at: str) -> None:
    existing = [
        filename for filename in _LEGACY_ARTIFACT_FILENAMES
        if (output_dir / filename).is_file()
    ]
    if not existing:
        return
    if len(existing) != len(_LEGACY_ARTIFACT_FILENAMES):
        raise ValueError("occupied canonical output contains a partial legacy artifact set")
    lineage_dir = output_dir / "history" / _legacy_lineage_key(output_dir)
    lineage_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for filename in _LEGACY_ARTIFACT_FILENAMES:
        source = output_dir / filename
        target = lineage_dir / filename
        source_bytes = source.read_bytes()
        if target.exists():
            if target.read_bytes() != source_bytes:
                raise ValueError(f"rejected history is immutable: {filename}")
            source.unlink()
        else:
            source.replace(target)
        entries.append({
            "filename": filename,
            "size": len(source_bytes),
            "sha256": _sha256_bytes(source_bytes),
        })
    status_path = lineage_dir / "LINEAGE_STATUS.json"
    status = _pretty_json({
        "schema_version": f"{SCHEMA_PREFIX}.historical_lineage.v1",
        "classification": "rejected",
        "gate": "P0",
        "gate_result": "failed",
        "archived_at": generated_at,
        "immutable": True,
        "artifacts": entries,
    }).encode()
    if status_path.exists() and status_path.read_bytes() != status:
        raise ValueError("rejected lineage label is immutable")
    if not status_path.exists():
        with status_path.open("xb") as handle:
            handle.write(status)


def _write_immutable_payloads(
    output_dir: Path, payloads: Mapping[str, str], generated_at: str
) -> None:
    has_manifest = (output_dir / "ARTIFACT_MANIFEST.json").is_file()
    canonical_existing = [
        filename for filename in ARTIFACT_FILENAMES if (output_dir / filename).exists()
    ]
    if canonical_existing and not has_manifest:
        _archive_rejected_legacy(output_dir, generated_at)
        canonical_existing = [
            filename for filename in ARTIFACT_FILENAMES if (output_dir / filename).exists()
        ]
    if canonical_existing:
        if len(canonical_existing) != len(ARTIFACT_FILENAMES):
            raise ValueError("occupied canonical output contains a partial current artifact set")
        differing = [
            filename for filename in ARTIFACT_FILENAMES
            if (output_dir / filename).read_bytes() != payloads[filename].encode()
        ]
        if differing:
            raise ValueError(
                f"immutable canonical output differs; refusing overwrite: {differing[0]}"
            )
        return
    for filename in ARTIFACT_FILENAMES:
        with (output_dir / filename).open("xb") as handle:
            handle.write(payloads[filename].encode())


def bootstrap_campaign(
    *, playbook_path: Path, goal_prompt_path: Path, output_dir: Path, generated_at: str
) -> BootstrapResult:
    spec = validate_campaign(
        playbook_path=playbook_path,
        goal_prompt_path=goal_prompt_path,
        generated_at=generated_at,
    )
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = _payloads(spec)
    _write_immutable_payloads(output_dir, payloads, generated_at)
    _validate_outputs(spec, output_dir)
    artifact_hashes = {
        filename: _sha256_file(output_dir / filename)
        for filename in ARTIFACT_FILENAMES
    }
    counts = Counter(item.workstream for item in spec.catalog)
    points = Counter()
    for item in spec.catalog:
        points[item.workstream] += item.points
    return BootstrapResult(
        output_dir=output_dir,
        generated_at=generated_at,
        item_count=len(spec.catalog),
        catalog_points=sum(item.points for item in spec.catalog),
        workstream_counts=dict(sorted(counts.items())),
        workstream_points=dict(sorted(points.items())),
        packet_count=len(spec.packets),
        catalog_sha256=spec.catalog_sha256,
        campaign_spec_sha256=spec.campaign_spec_sha256,
        artifact_hashes=artifact_hashes,
    )


__all__ = [
    "ARTIFACT_FILENAMES",
    "BREADBOARD_BASELINE",
    "BREADBOARD_CANONICAL_PAYLOAD_SHA256",
    "BootstrapResult",
    "CHILD_ARTIFACT_FILENAMES",
    "CampaignSpec",
    "EPIC_ID",
    "ISSUE_MAPPING_SHA256",
    "PacketAttemptBudget",
    "PacketSpec",
    "TransitionSpec",
    "WRAPPER_BASELINE",
    "WRAPPER_CANONICAL_PAYLOAD_SHA256",
    "bootstrap_campaign",
    "validate_campaign",
]
