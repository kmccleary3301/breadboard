from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import scripts.quality.sync_provider_parity_claims as parity


def _manifest() -> dict[str, object]:
    return parity.build_manifest()


def test_manifest_is_derived_from_exact_catalog_oracle_and_f6_owners() -> None:
    manifest = _manifest()

    assert parity.validate_claim_manifest(manifest) is manifest
    assert [row["breadboard_provider_id"] for row in manifest["claims"]] == [
        "codex",
        "openai",
        "anthropic",
        "openrouter",
    ]
    assert manifest["accepted_f6"]["row_count"] == 45
    assert manifest["accepted_f6"]["match_count"] == 42
    assert manifest["accepted_f6"]["intentional_divergence_count"] == 3
    assert {row["live_state"] for row in manifest["claims"]} == {"unproved"}


def test_accepted_f6_snapshot_binds_exact_head_manifest_and_artifacts() -> None:
    valid_summary = copy.deepcopy(parity.ACCEPTED_F6)
    valid_summary.pop("acceptance_record")
    parity._validate_accepted_f6_summary(valid_summary)

    for field, value in (
        ("breadboard_commit", "0" * 40),
        ("manifest_sha256", "sha256:" + ("0" * 64)),
    ):
        drifted = copy.deepcopy(valid_summary)
        drifted[field] = value
        with pytest.raises(
            parity.ClaimValidationError, match=f"accepted F6 snapshot.*{field}"
        ):
            parity._validate_accepted_f6_summary(drifted)

    drifted = copy.deepcopy(valid_summary)
    drifted["artifacts"]["artifact.sdk_local_responses"]["sha256"] = "sha256:" + (
        "0" * 64
    )
    with pytest.raises(
        parity.ClaimValidationError, match="accepted F6 snapshot.*artifacts"
    ):
        parity._validate_accepted_f6_summary(drifted)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("auth_schemes", ["oauth2"], "unknown auth scheme"),
        ("oauth_flow_ids", ["not-a-flow"], "unknown OAuth flow"),
        ("representative_model", "openai/not-proved", "unknown representative model"),
        ("runtime_id", "not_a_runtime", "unknown runtime"),
    ],
)
def test_validator_rejects_unknown_scheme_flow_model_or_runtime(
    field: str, value: object, message: str
) -> None:
    manifest = _manifest()
    manifest["claims"][1][field] = value

    with pytest.raises(parity.ClaimValidationError, match=message):
        parity.validate_claim_manifest(manifest)


def test_validator_rejects_live_claim_without_final_external_gates() -> None:
    manifest = _manifest()
    manifest["claims"][0]["live_state"] = "live_verified"

    with pytest.raises(parity.ClaimValidationError, match="live_verified is forbidden"):
        parity.validate_claim_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "message"),
    [("exclusions", "missing exclusions"), ("f6_evidence", "missing F6 evidence")],
)
def test_validator_rejects_claim_without_evidence_boundary(
    field: str, message: str
) -> None:
    manifest = _manifest()
    manifest["claims"][0][field] = []

    with pytest.raises(parity.ClaimValidationError, match=message):
        parity.validate_claim_manifest(manifest)


def test_validator_rejects_unapproved_divergence_and_f1_drift() -> None:
    manifest = _manifest()
    manifest["claims"][0]["divergence_ids"].append("silent-fallback")

    with pytest.raises(parity.ClaimValidationError, match="unapproved divergence"):
        parity.validate_claim_manifest(manifest)

    manifest = _manifest()
    manifest["claims"][0]["source_pin"]["commit"] = "0" * 40
    with pytest.raises(parity.ClaimValidationError, match="F1 source pin drift"):
        parity.validate_claim_manifest(manifest)


def test_nonclaims_cannot_be_rendered_as_supported_rows() -> None:
    manifest = _manifest()
    block = parity.render_reference_block(manifest)
    supported_table = block.split("### Bounded provider claims", 1)[1].split(
        "Every row excludes", 1
    )[0]

    for provider_id in (
        "google-gemini-cli",
        "google-antigravity",
        "mock",
        "cli_mock",
        "smoke",
        "replay",
    ):
        assert f"`{provider_id}` /" not in supported_table
    assert "Historical live evidence remains `historical_exact_scope`" in block
    assert "Anthropic consumer subscription is unsupported" in block


def test_public_claim_surfaces_link_manifest_and_preserve_nonclaims() -> None:
    readme = (parity.ROOT / "README.md").read_text(encoding="utf-8")
    index = (parity.ROOT / "docs/INDEX.md").read_text(encoding="utf-8")
    auth = (parity.ROOT / "docs/concepts/provider-plan-auth.md").read_text(
        encoding="utf-8"
    )

    manifest_link = "conformance/provider_parity_claims/manifest.v1.json"
    assert manifest_link in readme
    assert manifest_link in index
    assert manifest_link in auth
    assert "real login, credential validity" in readme
    assert "permission-hardened plaintext SQLite store" in auth
    assert "This is not encryption" in " ".join(auth.split())
    assert "Anthropic consumer-subscription material is unsupported" in auth
    assert "default-off and harness-backed-only" in auth


def test_generated_manifest_and_documentation_reject_hand_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest()
    manifest_path = tmp_path / "manifest.v1.json"
    reference_path = tmp_path / "LLM_PROVIDER_DETAILS.md"
    manifest_path.write_bytes(parity._pretty_json_bytes(manifest))
    reference_path.write_text(
        "before\n" + parity.render_reference_block(manifest) + "after\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(parity, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(parity, "REFERENCE_PATH", reference_path)

    parity._check_or_write(check=True)

    reference_path.write_text(
        reference_path.read_text(encoding="utf-8").replace(
            "`declared_supported`", "`live_verified`", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(parity.ClaimValidationError, match="stale generated"):
        parity._check_or_write(check=True)

    reference_path.write_text(
        "before\n" + parity.render_reference_block(manifest) + "after\n",
        encoding="utf-8",
    )
    edited = copy.deepcopy(manifest)
    edited["claims"][0]["runtime_id"] = "evil_runtime"
    manifest_path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(parity.ClaimValidationError, match="stale generated"):
        parity._check_or_write(check=True)
