"""Selective dependency admission stays independent of stock installations."""

import pytest

from tests.rl.harness import native_dependencies as dependencies


@pytest.mark.parametrize("version,env_name", [
    ("16.2.13", "OMP16213_CODING_AGENT_NODE_MODULES"),
    ("18.1.17", "OMP_CODING_AGENT_NODE_MODULES"),
])
def test_omp_resolution_respects_version_specific_override(monkeypatch, tmp_path, version, env_name):
    monkeypatch.setenv(env_name, str(tmp_path))
    assert dependencies.resolve_omp(version) == tmp_path


@pytest.mark.parametrize("version", ["0.57.1", "0.73.1"])
def test_pi_resolution_keeps_versioned_portable_pin(monkeypatch, tmp_path, version):
    monkeypatch.delenv("PI057_CODING_AGENT_NODE_MODULES", raising=False)
    monkeypatch.delenv("PI_CODING_AGENT_NODE_MODULES", raising=False)
    monkeypatch.setattr(dependencies, "_CACHE", tmp_path)
    assert dependencies.resolve_pi(version) == tmp_path / "pkgs" / f"@mariozechner__pi-coding-agent@{version}" / "node_modules"


def test_openclaw_resolution_uses_portable_pin(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENCLAW_DIST", raising=False)
    monkeypatch.setattr(dependencies, "_CACHE", tmp_path)
    assert dependencies.resolve_openclaw() == tmp_path / "pkgs/openclaw@2026.9.4/node_modules/openclaw/dist"


def test_absent_openclaw_is_skipped_at_use_not_discovery(monkeypatch, tmp_path):
    absent = tmp_path / "absent"
    monkeypatch.setenv("OPENCLAW_DIST", str(absent))
    assert dependencies.resolve_openclaw() == absent
    with pytest.raises(pytest.skip.Exception, match="pinned OpenClaw"):
        dependencies.require_openclaw()


def test_absent_hermes_is_skipped_before_agent_import(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_SOURCE_ROOT", str(tmp_path))
    with pytest.raises(pytest.skip.Exception, match="pinned Hermes"):
        dependencies.require_hermes()


def test_present_dist_is_not_validated_or_errors_swallowed_by_guard(monkeypatch, tmp_path):
    # Admission checks availability only. The stock loader must reject corrupt
    # bytes instead of converting its digest/parse failure into an optional skip.
    (tmp_path / "resource-loader-Bu_pVD2t.mjs").write_bytes(b"not valid JavaScript")
    monkeypatch.setenv("OPENCLAW_DIST", str(tmp_path))
    monkeypatch.setattr(dependencies.shutil, "which", lambda name: "/stock/node")
    assert dependencies.require_openclaw() == tmp_path
