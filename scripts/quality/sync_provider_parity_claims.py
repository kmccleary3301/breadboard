#!/usr/bin/env python3
"""Generate and verify BreadBoard's bounded provider parity claims."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from breadboard_engine.provider_broker.catalog import (
    product_provider_catalog,
    provider_catalog,
)
from conformance.provider_differential.contracts import (
    ALL_ROW_IDS,
    ARTIFACT_ROW_IDS,
    ORACLE_IDENTITY,
    PROVIDERS,
    PROVIDER_FAMILIES,
    ROLE_ROW_IDS,
    canonical_json_bytes,
    validate_manifest as validate_f6_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "conformance/provider_parity_claims/manifest.v1.json"
REFERENCE_PATH = ROOT / "docs/reference/LLM_PROVIDER_DETAILS.md"
SCENARIO_PATH = ROOT / "conformance/provider_differential/scenario.v1.json"
ORACLE_FIXTURE_PATH = ROOT / "conformance/provider_differential/oracle/omp-v18.0.1.json"
ORACLE_RUNNER_PATH = ROOT / "scripts/quality/capture_f6_omp_oracle.ts"
HISTORICAL_SCOPE_PATH = (
    ROOT / "docs/conformance/provider_runtime_evidence/"
    "x1_openai_gpt55_product_journey_20260819/exact_scope_claim.json"
)
POLICY_PATHS = (
    ROOT / "docs/provider_plans/policy_manifests/anthropic_consumer_subscription.json",
    ROOT / "docs/provider_plans/policy_manifests/openai_codex_chatgpt_plan.json",
)
BEGIN_MARKER = "<!-- BEGIN GENERATED PROVIDER PARITY CLAIMS -->"
END_MARKER = "<!-- END GENERATED PROVIDER PARITY CLAIMS -->"
SCHEMA_VERSION = "bb.provider_parity_claims.v1"
SOURCE_PIN_ID = "omp-v18.0.1"
LIVE_ADVANCEMENT_GATES = ("L1", "L2")

ACCEPTED_F6 = {
    "breadboard_commit": "145912b8d2a235943f453ae8ac8fe825200c9bbd",
    "breadboard_tree": "7145ce07950f9384588eafe69f730760d41f4df0",
    "manifest_sha256": (
        "sha256:1d7b41facae20d6c6d39cfc9777867dae514970e19cd7d8e27447b86cbc4714e"
    ),
    "row_count": 45,
    "match_count": 42,
    "intentional_divergence_count": 3,
    "acceptance_record": "beads:bb-ny9w.35",
    "artifacts": {
        "artifact.installed_end_to_end_trace": {
            "source": "observations/artifact.installed_end_to_end_trace.json",
            "sha256": (
                "sha256:b5b7c8e1097ee0cbb14d878b85a8822f7151cfb3752b196f86f64b2e550ad8d6"
            ),
        },
        "artifact.sdk_local_responses": {
            "source": "artifacts/breadboard-sdk-0.3.0.tgz",
            "sha256": (
                "sha256:3121c3c5ccd172dbfb60bacf1af43eefeeaa6d4f6598b465d5c42de7fc4b90fe"
            ),
        },
        "artifact.wheel_provider_catalog_auth_role": {
            "source": "artifacts/breadboard_harness_cli-0.0.0-py3-none-any.whl",
            "sha256": (
                "sha256:935259a8b425b104e7391fcb78fda557a6f5c66135ab8c30a3ff2a3be0d23b70"
            ),
        },
    },
}

INTENTIONAL_DIVERGENCE_ROWS = frozenset(
    {
        "role.auth_policy_no_fallback",
        "role.cross_provider_default_forbidden",
        "role.lock_secret_rotation_restart",
    }
)

API_KEY_AUTH_ROWS = (
    "auth.api_key_precedence",
    "auth.explicit_account_binding",
    "auth.automatic_affinity_restart_rotation",
    "auth.classified_429_rotation",
)
OAUTH_REFRESH_ROWS = (
    "auth.refresh_single_flight",
    "auth.refresh_transient_deferral",
    "auth.refresh_definitive_tombstone",
    "auth.revoke_during_refresh",
)
AUTH_ROWS_BY_PROVIDER = {
    "codex": ("auth.codex_oauth_precedence",),
    "openai": API_KEY_AUTH_ROWS,
    "anthropic": API_KEY_AUTH_ROWS + OAUTH_REFRESH_ROWS,
    "openrouter": API_KEY_AUTH_ROWS,
}

GLOBAL_EXCLUSIONS = (
    "No real provider login or credential-validity claim.",
    "No live provider network or response-availability claim.",
    "No cost, latency, rate-limit, or quota claim.",
    "No model-quality or model-family claim beyond the named representative fixture.",
    "No release-wide, platform-installation, update, rollback, or signing claim.",
)

KNOWN_DIVERGENCES = (
    {
        "id": "bounded-catalog",
        "summary": (
            "BreadBoard supports four catalog providers; OMP v18.0.1 has a broader "
            "registry. Gemini is deferred and every other OMP entry is outside the "
            "product claim."
        ),
        "f6_rows": [],
    },
    {
        "id": "codex-alias",
        "summary": (
            "BreadBoard's canonical provider id is codex; the pinned OMP provider id "
            "is openai-codex."
        ),
        "f6_rows": [],
    },
    {
        "id": "configured-only-models",
        "summary": (
            "BreadBoard admits explicit configured models only; OMP may discover "
            "models dynamically and change defaults."
        ),
        "f6_rows": [],
    },
    {
        "id": "codex-transport",
        "summary": (
            "BreadBoard uses the Codex app-server runtime; OMP's oracle exercises its "
            "Responses transport. Only normalized product-visible semantics are compared."
        ),
        "f6_rows": [],
    },
    {
        "id": "auth-mechanics",
        "summary": (
            "BreadBoard owns a durable broker, precedence, affinity, refresh, revoke, "
            "and tombstones; OMP owns different credential storage and selection mechanics."
        ),
        "f6_rows": [],
    },
    {
        "id": "subscription-material-policy",
        "summary": (
            "Broker API-key/OAuth schemes do not authorize direct consumer-subscription "
            "material; scheme-specific policy manifests remain authoritative."
        ),
        "f6_rows": [],
    },
    {
        "id": "exchange-ir",
        "summary": (
            "BreadBoard exposes the strict F4 exchange IR and bounded native envelope, "
            "not OMP's internal or provider-native stream objects."
        ),
        "f6_rows": [],
    },
    {
        "id": "role-resolution",
        "summary": (
            "BreadBoard exposes a bounded public role subset with exact targets; OMP "
            "also has fuzzy aliases and internal roles."
        ),
        "f6_rows": ["role.lock_secret_rotation_restart"],
    },
    {
        "id": "fallback-policy",
        "summary": (
            "BreadBoard forbids auth/policy-triggered model fallback and implicit "
            "cross-provider fallback; OMP permits fallback behavior."
        ),
        "f6_rows": [
            "role.auth_policy_no_fallback",
            "role.cross_provider_default_forbidden",
        ],
    },
    {
        "id": "synthetic-only",
        "summary": (
            "F6 proves deterministic local semantics and provider-free artifacts, not "
            "real login, network, cost, latency, quota, quality, or release readiness."
        ),
        "f6_rows": [],
    },
)

COMMON_DIVERGENCE_IDS = (
    "bounded-catalog",
    "configured-only-models",
    "auth-mechanics",
    "subscription-material-policy",
    "exchange-ir",
    "role-resolution",
    "fallback-policy",
    "synthetic-only",
)
CODEX_DIVERGENCE_IDS = (
    "bounded-catalog",
    "codex-alias",
    "configured-only-models",
    "codex-transport",
    "auth-mechanics",
    "subscription-material-policy",
    "exchange-ir",
    "role-resolution",
    "fallback-policy",
    "synthetic-only",
)


class ClaimValidationError(ValueError):
    """Raised when a provider support claim exceeds its canonical evidence."""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _sha256_json(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def _pretty_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_owner_data() -> dict[str, Any]:
    scenario = _read_json(SCENARIO_PATH)
    fixture = _read_json(ORACLE_FIXTURE_PATH)
    policies = [_read_json(path) for path in POLICY_PATHS]
    historical_scope = _read_json(HISTORICAL_SCOPE_PATH)

    if scenario.get("schema_version") != "bb.provider_differential_scenario.v1":
        raise ClaimValidationError("provider scenario schema is not pinned")
    models = scenario.get("models")
    if not isinstance(models, Mapping) or set(models) != set(PROVIDERS):
        raise ClaimValidationError(
            "provider scenario must name exactly the four providers"
        )

    if fixture.get("schema_version") != "bb.f6.omp_oracle.v1":
        raise ClaimValidationError("F1 oracle fixture schema drifted")
    oracle = fixture.get("oracle")
    if not isinstance(oracle, Mapping):
        raise ClaimValidationError("F1 oracle identity is missing")
    if oracle.get("head") != ORACLE_IDENTITY["commit"]:
        raise ClaimValidationError("F1 oracle commit drifted")
    if oracle.get("tree") != ORACLE_IDENTITY["tree"]:
        raise ClaimValidationError("F1 oracle tree drifted")
    if fixture.get("runner_sha256") != _sha256_file(ORACLE_RUNNER_PATH):
        raise ClaimValidationError("F1 oracle runner digest drifted")

    fixture_rows = fixture.get("rows")
    if not isinstance(fixture_rows, Sequence) or isinstance(
        fixture_rows, (str, bytes, bytearray)
    ):
        raise ClaimValidationError("F1 oracle rows are missing")
    oracle_rows: dict[str, Mapping[str, Any]] = {}
    for row in fixture_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            raise ClaimValidationError("F1 oracle row is malformed")
        row_id = row["id"]
        if row_id in oracle_rows:
            raise ClaimValidationError(f"duplicate F1 oracle row: {row_id}")
        oracle_rows[row_id] = row
    expected_oracle_rows = set(ALL_ROW_IDS) - set(ARTIFACT_ROW_IDS)
    if set(oracle_rows) != expected_oracle_rows:
        raise ClaimValidationError("F1 oracle row inventory drifted")

    catalog = {entry.provider_id: entry for entry in provider_catalog()}
    product_catalog = tuple(entry.provider_id for entry in product_provider_catalog())
    if product_catalog != PROVIDERS:
        raise ClaimValidationError(
            "product provider catalog must contain exactly codex, openai, anthropic, openrouter"
        )

    return {
        "catalog": catalog,
        "historical_scope": historical_scope,
        "models": models,
        "oracle": oracle,
        "oracle_rows": oracle_rows,
        "policies": policies,
    }


def _source_pin(owner: Mapping[str, Any]) -> dict[str, Any]:
    oracle = owner["oracle"]
    source_blobs = oracle.get("source_blobs")
    if not isinstance(source_blobs, Mapping) or not source_blobs:
        raise ClaimValidationError("F1 oracle source blob map is missing")
    return {
        "commit": ORACLE_IDENTITY["commit"],
        "fixture_sha256": _sha256_file(ORACLE_FIXTURE_PATH),
        "repository": ORACLE_IDENTITY["repository"],
        "runner_sha256": _sha256_file(ORACLE_RUNNER_PATH),
        "source_blob_set_sha256": _sha256_json(source_blobs),
        "tag": ORACLE_IDENTITY["tag"],
        "tree": ORACLE_IDENTITY["tree"],
    }


def _policy_nonclaims(owner: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    policy_catalog_ids = {
        "consumer_subscription": "anthropic",
        "codex_chatgpt_subscription": "codex",
    }
    for path, policy in zip(POLICY_PATHS, owner["policies"], strict=True):
        plan_id = policy.get("plan_id")
        catalog_provider_id = policy_catalog_ids.get(plan_id)
        if catalog_provider_id is None:
            raise ClaimValidationError(f"unknown provider-plan policy: {plan_id!r}")
        if policy.get("schema") != "breadboard.provider_policy_manifest.v1":
            raise ClaimValidationError(
                f"provider-plan policy schema drifted: {plan_id}"
            )
        if policy.get("supported") is not False:
            raise ClaimValidationError(
                f"consumer-subscription policy unexpectedly became supported: {plan_id}"
            )
        result.append(
            {
                "catalog_provider_id": catalog_provider_id,
                "local_only": policy.get("local_only"),
                "plan_id": plan_id,
                "policy_provider_id": policy.get("provider_id"),
                "policy_sha256": _sha256_file(path),
                "policy_source": str(path.relative_to(ROOT)),
                "requires_explicit_enable": policy.get("requires_explicit_enable"),
                "requires_sealed_profile": policy.get("requires_sealed_profile"),
                "state": "unsupported",
                "support_mode": policy.get("support_mode"),
            }
        )
    return sorted(result, key=lambda row: row["plan_id"])


def _claim_f6_rows(
    provider_id: str, oracle_rows: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    row_ids = (
        tuple(f"{provider_id}.{family}" for family in PROVIDER_FAMILIES)
        + AUTH_ROWS_BY_PROVIDER[provider_id]
        + ROLE_ROW_IDS
        + ARTIFACT_ROW_IDS
    )
    result: list[dict[str, Any]] = []
    for row_id in row_ids:
        classification = (
            "intentional_divergence"
            if row_id in INTENTIONAL_DIVERGENCE_ROWS
            else "match"
        )
        row: dict[str, Any] = {
            "classification": classification,
            "row_id": row_id,
        }
        if row_id in ARTIFACT_ROW_IDS:
            row["artifact"] = copy.deepcopy(ACCEPTED_F6["artifacts"][row_id])
        else:
            oracle_row = oracle_rows[row_id]
            row["oracle_input_sha256"] = oracle_row["input_sha256"]
            row["oracle_output_sha256"] = oracle_row["output_sha256"]
        result.append(row)
    return result


def _claim_policy_refs(
    provider_id: str, policy_nonclaims: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(dict(policy))
        for policy in policy_nonclaims
        if policy["catalog_provider_id"] == provider_id
    ]


def _build_claim(
    provider_id: str,
    *,
    owner: Mapping[str, Any],
    source_pin: Mapping[str, Any],
    policy_nonclaims: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    entry = owner["catalog"][provider_id]
    model = owner["models"][provider_id]
    if model.get("runtime") != entry.runtime_id:
        raise ClaimValidationError(f"scenario runtime drifted for {provider_id}")
    if model.get("provider") not in {provider_id, *entry.aliases}:
        raise ClaimValidationError(f"scenario provider alias drifted for {provider_id}")

    divergence_ids = (
        CODEX_DIVERGENCE_IDS if provider_id == "codex" else COMMON_DIVERGENCE_IDS
    )
    return {
        "advancement_gates": list(LIVE_ADVANCEMENT_GATES),
        "api_variant": model["api"],
        "auth_owner": entry.auth_owner,
        "auth_schemes": list(entry.auth_schemes),
        "oauth_flow_ids": [flow.flow_id for flow in entry.oauth_flows],
        "breadboard_provider_id": provider_id,
        "contract_state": "declared_supported",
        "divergence_ids": list(divergence_ids),
        "exclusions": list(GLOBAL_EXCLUSIONS),
        "f6_evidence": _claim_f6_rows(provider_id, owner["oracle_rows"]),
        "f6_snapshot": {
            "acceptance_record": ACCEPTED_F6["acceptance_record"],
            "breadboard_commit": ACCEPTED_F6["breadboard_commit"],
            "breadboard_tree": ACCEPTED_F6["breadboard_tree"],
            "manifest_sha256": ACCEPTED_F6["manifest_sha256"],
        },
        "live_state": "unproved",
        "model_discovery": entry.model_discovery,
        "omp_provider_id": model["provider"],
        "representative_model": model["id"],
        "runtime_id": entry.runtime_id,
        "scheme_policy_nonclaims": _claim_policy_refs(provider_id, policy_nonclaims),
        "source_pin": copy.deepcopy(dict(source_pin)),
        "verification_state": "synthetic_verified",
    }


def _catalog_nonclaims(owner: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for provider_id, entry in owner["catalog"].items():
        if entry.support_tier == "core":
            continue
        rows.append(
            {
                "auth_schemes": list(entry.auth_schemes),
                "provider_id": provider_id,
                "reason": f"catalog support_tier={entry.support_tier}; no F6 provider rows",
                "runtime_id": entry.runtime_id,
                "state": (
                    "deferred" if entry.support_tier == "deferred" else "evidence_only"
                ),
            }
        )
    rows.append(
        {
            "provider_id": "*",
            "reason": (
                "Any provider/model present in OMP v18.0.1 but absent from the four "
                "BreadBoard core rows remains outside the product support claim."
            ),
            "state": "native_omp_only",
        }
    )
    rows.append(
        {
            "evidence_id": "x1_openai_gpt55_product_journey_20260819",
            "evidence_sha256": _sha256_file(HISTORICAL_SCOPE_PATH),
            "reason": (
                "One old-wheel, tools-disabled, one-turn Codex app-server journey; "
                "its own exclusions forbid current-head or provider-wide promotion."
            ),
            "source": str(HISTORICAL_SCOPE_PATH.relative_to(ROOT)),
            "state": "historical_exact_scope",
        }
    )
    return rows


def build_manifest() -> dict[str, Any]:
    """Build the only public provider-claim registry from canonical owners."""

    owner = _load_owner_data()
    source_pin = _source_pin(owner)
    policy_nonclaims = _policy_nonclaims(owner)
    claims = [
        _build_claim(
            provider_id,
            owner=owner,
            source_pin=source_pin,
            policy_nonclaims=policy_nonclaims,
        )
        for provider_id in PROVIDERS
    ]
    return {
        "accepted_f6": copy.deepcopy(ACCEPTED_F6),
        "claims": claims,
        "known_divergences": copy.deepcopy(list(KNOWN_DIVERGENCES)),
        "manifest_id": "f7-provider-parity-omp-v18.0.1-f6-145912b8",
        "nonclaims": _catalog_nonclaims(owner),
        "policy_nonclaims": policy_nonclaims,
        "schema_version": SCHEMA_VERSION,
        "source_pin": source_pin,
    }


def _claim_by_provider(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    claims = manifest.get("claims")
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes, bytearray)):
        raise ClaimValidationError("claims must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise ClaimValidationError("claim row must be an object")
        provider_id = claim.get("breadboard_provider_id")
        if not isinstance(provider_id, str):
            raise ClaimValidationError("claim row is missing breadboard_provider_id")
        if provider_id in result:
            raise ClaimValidationError(f"duplicate provider claim: {provider_id}")
        result[provider_id] = claim
    return result


def validate_claim_manifest(manifest: Any) -> Mapping[str, Any]:
    """Reject support claims that exceed catalog, F1, F6, policy, or K/L evidence."""

    if not isinstance(manifest, Mapping):
        raise ClaimValidationError("provider parity manifest must be an object")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ClaimValidationError("provider parity schema version drifted")

    owner = _load_owner_data()
    claims = _claim_by_provider(manifest)
    if set(claims) != set(PROVIDERS):
        raise ClaimValidationError("claims must name exactly the four core providers")

    known = manifest.get("known_divergences")
    if not isinstance(known, Sequence) or isinstance(known, (str, bytes, bytearray)):
        raise ClaimValidationError("known_divergences must be an array")
    known_ids = {
        row.get("id") for row in known if isinstance(row, Mapping) and row.get("id")
    }
    expected_divergence_ids = {row["id"] for row in KNOWN_DIVERGENCES}
    if known_ids != expected_divergence_ids:
        raise ClaimValidationError("known divergence inventory drifted")
    approved_f6_divergences = {
        row_id
        for row in known
        if isinstance(row, Mapping)
        for row_id in row.get("f6_rows", [])
    }
    if approved_f6_divergences != INTENTIONAL_DIVERGENCE_ROWS:
        raise ClaimValidationError("unapproved F6 divergence is present")

    for provider_id, claim in claims.items():
        entry = owner["catalog"].get(provider_id)
        model = owner["models"].get(provider_id)
        if entry is None or entry.support_tier != "core":
            raise ClaimValidationError(
                f"unknown or non-core provider claim: {provider_id}"
            )
        if claim.get("auth_schemes") != list(entry.auth_schemes):
            raise ClaimValidationError(f"unknown auth scheme claim for {provider_id}")
        if claim.get("oauth_flow_ids") != [flow.flow_id for flow in entry.oauth_flows]:
            raise ClaimValidationError(f"unknown OAuth flow claim for {provider_id}")
        if claim.get("representative_model") != model.get("id"):
            raise ClaimValidationError(
                f"unknown representative model for {provider_id}"
            )
        if claim.get("runtime_id") != entry.runtime_id:
            raise ClaimValidationError(f"unknown runtime for {provider_id}")
        if claim.get("live_state") != "unproved":
            raise ClaimValidationError(
                f"live_verified is forbidden before L1/L2 for {provider_id}"
            )
        if claim.get("advancement_gates") != list(LIVE_ADVANCEMENT_GATES):
            raise ClaimValidationError(
                f"missing K/L advancement gates for {provider_id}"
            )
        exclusions = claim.get("exclusions")
        if (
            not isinstance(exclusions, Sequence)
            or isinstance(exclusions, (str, bytes, bytearray))
            or not exclusions
        ):
            raise ClaimValidationError(f"missing exclusions for {provider_id}")
        evidence = claim.get("f6_evidence")
        if (
            not isinstance(evidence, Sequence)
            or isinstance(evidence, (str, bytes, bytearray))
            or not evidence
        ):
            raise ClaimValidationError(f"missing F6 evidence for {provider_id}")
        for row in evidence:
            if not isinstance(row, Mapping):
                raise ClaimValidationError(f"malformed F6 evidence for {provider_id}")
            row_id = row.get("row_id")
            classification = row.get("classification")
            if row_id not in ALL_ROW_IDS:
                raise ClaimValidationError(
                    f"unknown F6 row for {provider_id}: {row_id}"
                )
            expected_classification = (
                "intentional_divergence"
                if row_id in INTENTIONAL_DIVERGENCE_ROWS
                else "match"
            )
            if classification != expected_classification:
                raise ClaimValidationError(
                    f"unapproved F6 classification for {provider_id}: {row_id}"
                )
        divergence_ids = claim.get("divergence_ids")
        if not isinstance(divergence_ids, Sequence) or isinstance(
            divergence_ids, (str, bytes, bytearray)
        ):
            raise ClaimValidationError(f"missing divergence ids for {provider_id}")
        unknown = set(divergence_ids) - known_ids
        if unknown:
            raise ClaimValidationError(
                f"unapproved divergence for {provider_id}: {sorted(unknown)}"
            )
        source_pin = claim.get("source_pin")
        if not isinstance(source_pin, Mapping):
            raise ClaimValidationError(f"missing F1 source pin for {provider_id}")
        if source_pin.get("commit") != ORACLE_IDENTITY["commit"]:
            raise ClaimValidationError(f"F1 source pin drift for {provider_id}")
        if source_pin.get("tree") != ORACLE_IDENTITY["tree"]:
            raise ClaimValidationError(f"F1 source tree drift for {provider_id}")
        f6_snapshot = claim.get("f6_snapshot")
        if not isinstance(f6_snapshot, Mapping):
            raise ClaimValidationError(f"missing F6 snapshot for {provider_id}")
        if not f6_snapshot.get("manifest_sha256"):
            raise ClaimValidationError(f"missing F6 manifest digest for {provider_id}")

    expected = build_manifest()
    if manifest != expected:
        raise ClaimValidationError(
            "provider parity manifest differs from catalog/F1/F6/policy-derived output"
        )
    return manifest


def _format_sha(value: str) -> str:
    return value.removeprefix("sha256:")


def render_reference_block(manifest: Mapping[str, Any]) -> str:
    """Render the bounded support and known-divergence documentation block."""

    source_pin = manifest["source_pin"]
    accepted_f6 = manifest["accepted_f6"]
    lines = [
        BEGIN_MARKER,
        "## 0. Proved product scope",
        "",
        "> [!IMPORTANT]",
        "> This generated block is the complete BreadBoard provider-support claim.",
        "> The protocol guide below is implementation reference, not product support proof.",
        "",
        (
            f"Source pin: `{source_pin['repository']}` `{source_pin['tag']}` at "
            f"`{source_pin['commit']}` (tree `{source_pin['tree']}`). Oracle fixture "
            f"SHA-256: `{_format_sha(source_pin['fixture_sha256'])}`; runner SHA-256: "
            f"`{_format_sha(source_pin['runner_sha256'])}`."
        ),
        "",
        (
            f"Accepted F6 snapshot: BreadBoard `{accepted_f6['breadboard_commit']}` "
            f"(tree `{accepted_f6['breadboard_tree']}`), manifest SHA-256 "
            f"`{_format_sha(accepted_f6['manifest_sha256'])}`; "
            f"{accepted_f6['row_count']} rows = {accepted_f6['match_count']} matches + "
            f"{accepted_f6['intentional_divergence_count']} approved divergences."
        ),
        "",
        (
            "Built-artifact bindings: wheel `"
            + _format_sha(
                accepted_f6["artifacts"]["artifact.wheel_provider_catalog_auth_role"][
                    "sha256"
                ]
            )
            + "`; TypeScript SDK `"
            + _format_sha(
                accepted_f6["artifacts"]["artifact.sdk_local_responses"]["sha256"]
            )
            + "`; provider-free installed trace `"
            + _format_sha(
                accepted_f6["artifacts"]["artifact.installed_end_to_end_trace"][
                    "sha256"
                ]
            )
            + "`."
        ),
        "",
        "Machine-readable source: "
        "[`conformance/provider_parity_claims/manifest.v1.json`](../../conformance/provider_parity_claims/manifest.v1.json).",
        "",
        "### Bounded provider claims",
        "",
        "| BreadBoard provider / OMP id | Auth scheme / flow / owner | Representative model | Runtime / API | Contract / evidence | Live state | Divergences |",
        "|---|---|---|---|---|---|---|",
    ]
    for claim in manifest["claims"]:
        provider_label = (
            f"`{claim['breadboard_provider_id']}` / `{claim['omp_provider_id']}`"
        )
        auth = ", ".join(f"`{scheme}`" for scheme in claim["auth_schemes"])
        flows = ", ".join(f"`{flow}`" for flow in claim["oauth_flow_ids"])
        if flows:
            auth = f"{auth}; flow {flows}"
        auth = f"{auth}; owner `{claim['auth_owner']}`"
        runtime = f"`{claim['runtime_id']}` / `{claim['api_variant']}`"
        contract = f"`{claim['contract_state']}` / `{claim['verification_state']}`"
        live = "`unproved`; requires `L1` + `L2`"
        divergences = ", ".join(f"`{item}`" for item in claim["divergence_ids"])
        lines.append(
            f"| {provider_label} | {auth} | `{claim['representative_model']}` | "
            f"{runtime} | {contract} | {live} | {divergences} |"
        )

    lines.extend(
        [
            "",
            "Every row excludes real login, credential validity, live provider network, "
            "cost, latency, quota, model-quality, model-family, and release-wide claims. "
            "The representative model is a deterministic F6 fixture, not a family claim.",
            "",
            "Direct consumer-subscription material is a separate policy surface. "
            "Anthropic consumer subscription is unsupported. Codex/ChatGPT subscription "
            "material is default-off and harness-backed-only; neither policy broadens the "
            "broker API-key/OAuth rows above.",
            "",
            "Deferred providers: `google-gemini-cli`, `google-antigravity`. Evidence-only "
            "providers: `mock`, `cli_mock`, `smoke`, `replay`. Other OMP registry entries "
            "are `native_omp_only`, not BreadBoard support claims. Historical live evidence "
            "remains `historical_exact_scope` and does not prove the current head.",
            "",
            "### Known-divergence matrix",
            "",
            "| ID | Exact boundary | F6 intentional rows |",
            "|---|---|---|",
        ]
    )
    for divergence in manifest["known_divergences"]:
        rows = ", ".join(f"`{row}`" for row in divergence["f6_rows"]) or "None"
        lines.append(f"| `{divergence['id']}` | {divergence['summary']} | {rows} |")
    lines.extend([END_MARKER, ""])
    return "\n".join(lines)


def _replace_reference_block(content: str, block: str) -> str:
    if content.count(BEGIN_MARKER) != 1 or content.count(END_MARKER) != 1:
        raise ClaimValidationError(
            "provider reference must contain one generated block"
        )
    start = content.index(BEGIN_MARKER)
    end = content.index(END_MARKER, start) + len(END_MARKER)
    return content[:start] + block.rstrip("\n") + content[end:]


def expected_reference_bytes(manifest: Mapping[str, Any]) -> bytes:
    content = REFERENCE_PATH.read_text(encoding="utf-8")
    rendered = _replace_reference_block(content, render_reference_block(manifest))
    return rendered.encode("utf-8")


def validate_current_f6(manifest_path: Path, root: Path) -> dict[str, Any]:
    """Validate an exact current-head F6 ledger and its artifact hashes."""

    value = _read_json(manifest_path)
    validated = validate_f6_manifest(value, root=root)
    rows = validated["rows"]
    row_by_id = {row["row_id"]: row for row in rows}
    if set(row_by_id) != set(ALL_ROW_IDS) or len(rows) != len(ALL_ROW_IDS):
        raise ClaimValidationError("current F6 ledger row inventory drifted")
    for row_id, row in row_by_id.items():
        expected = (
            "intentional_divergence"
            if row_id in INTENTIONAL_DIVERGENCE_ROWS
            else "match"
        )
        if row["classification"] != expected:
            raise ClaimValidationError(
                f"current F6 row is not claimable: {row_id}={row['classification']}"
            )
    if validated["oracle_identity"] != ORACLE_IDENTITY:
        raise ClaimValidationError("current F6 oracle identity drifted")
    artifacts: dict[str, dict[str, str]] = {}
    for row_id in ARTIFACT_ROW_IDS:
        provenance = row_by_id[row_id].get("artifact_provenance")
        if not isinstance(provenance, Mapping):
            raise ClaimValidationError(
                f"current F6 artifact provenance is missing: {row_id}"
            )
        source = provenance.get("source")
        sha256 = provenance.get("sha256")
        if not isinstance(source, str) or not isinstance(sha256, str):
            raise ClaimValidationError(
                f"current F6 artifact provenance is malformed: {row_id}"
            )
        artifacts[row_id] = {"source": source, "sha256": sha256}
    return {
        "artifacts": artifacts,
        "breadboard_commit": validated["breadboard_commit"],
        "breadboard_tree": validated["breadboard_tree"],
        "manifest_sha256": _sha256_file(manifest_path),
        "row_count": len(rows),
        "match_count": len(rows) - len(INTENTIONAL_DIVERGENCE_ROWS),
        "intentional_divergence_count": len(INTENTIONAL_DIVERGENCE_ROWS),
    }


def _validate_accepted_f6_summary(summary: Mapping[str, Any]) -> None:
    expected = {
        key: copy.deepcopy(value)
        for key, value in ACCEPTED_F6.items()
        if key != "acceptance_record"
    }
    mismatches = sorted(
        key for key, value in expected.items() if summary.get(key) != value
    )
    if mismatches:
        raise ClaimValidationError(
            "accepted F6 snapshot does not match embedded claim evidence: "
            + ", ".join(mismatches)
        )


def validate_accepted_f6(manifest_path: Path, root: Path) -> dict[str, Any]:
    """Verify that external F6 evidence exactly backs the embedded snapshot."""

    summary = validate_current_f6(manifest_path, root)
    _validate_accepted_f6_summary(summary)
    return summary


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _check_or_write(*, check: bool) -> None:
    manifest = build_manifest()
    validate_claim_manifest(manifest)
    manifest_bytes = _pretty_json_bytes(manifest)
    reference_bytes = expected_reference_bytes(manifest)

    stale: list[str] = []
    if check:
        if not MANIFEST_PATH.is_file() or MANIFEST_PATH.read_bytes() != manifest_bytes:
            stale.append(_display_path(MANIFEST_PATH))
        else:
            validate_claim_manifest(_read_json(MANIFEST_PATH))
        if REFERENCE_PATH.read_bytes() != reference_bytes:
            stale.append(_display_path(REFERENCE_PATH))
        if stale:
            raise ClaimValidationError(
                "stale generated provider claim output: " + ", ".join(stale)
            )
        return

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_bytes(manifest_bytes)
    REFERENCE_PATH.write_bytes(reference_bytes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--accepted-f6-manifest", type=Path)
    parser.add_argument("--accepted-f6-root", type=Path)
    parser.add_argument("--current-f6-manifest", type=Path)
    parser.add_argument("--current-f6-root", type=Path)
    args = parser.parse_args(argv)
    if bool(args.accepted_f6_manifest) != bool(args.accepted_f6_root):
        parser.error(
            "--accepted-f6-manifest and --accepted-f6-root are required together"
        )
    if bool(args.current_f6_manifest) != bool(args.current_f6_root):
        parser.error(
            "--current-f6-manifest and --current-f6-root are required together"
        )

    try:
        _check_or_write(check=args.check)
        accepted_f6 = None
        if args.accepted_f6_manifest is not None:
            accepted_f6 = validate_accepted_f6(
                args.accepted_f6_manifest, args.accepted_f6_root
            )
        current_f6 = None
        if args.current_f6_manifest is not None:
            current_f6 = validate_current_f6(
                args.current_f6_manifest, args.current_f6_root
            )
    except (ClaimValidationError, OSError, ValueError) as exc:
        print(f"provider parity claims invalid: {exc}")
        return 1

    result: dict[str, Any] = {
        "claims": len(PROVIDERS),
        "generated_outputs": "verified" if args.check else "written",
        "live_state": "unproved",
        "schema_version": SCHEMA_VERSION,
    }
    if accepted_f6 is not None:
        result["accepted_f6"] = accepted_f6
    if current_f6 is not None:
        result["current_f6"] = current_f6
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
