"""Pinned stock process isolation and opt-in auxiliary provider routing."""
from pathlib import Path
import json

import pytest

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession, StockExecutionError
from breadboard_engine.provider.routing import ProviderRouter
from breadboard_engine.provider_broker import get_provider_broker
from breadboard_engine.security.isolation_errors import ProcessIsolationUnavailable
from breadboard_engine.security.stock_install import stock_install_digest, validate_stock_read_only_roots
from tests.compaction.test_opencode_stock_tools import MODEL, binary, isolated_environment, response


def test_install_digest_rejects_mutation_and_credential_overlap(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    install = tmp_path / "install"
    install.mkdir()
    bundle = install / "index.js"
    bundle.write_text("export default function plugin() {}")
    pin = {"path": str(install), "digest": stock_install_digest(install)}
    assert validate_stock_read_only_roots([pin], workspace=workspace, protected_paths=()) == (install,)
    with pytest.raises(ProcessIsolationUnavailable, match="protected credential"):
        validate_stock_read_only_roots([pin], workspace=workspace, protected_paths=(install,))
    bundle.write_text("mutated")
    with pytest.raises(ProcessIsolationUnavailable, match="digest mismatch"):
        validate_stock_read_only_roots([pin], workspace=workspace, protected_paths=())


def test_stock_cannot_read_protected_path_or_write_install_root(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = tmp_path / "protected"
    protected.mkdir()
    secret = protected / "secret.txt"
    secret.write_text("fixture-secret-never-return")
    install = tmp_path / "install"
    install.mkdir()
    data = install / "asset.txt"
    data.write_text("unchanged")
    pin = {"path": str(install), "digest": stock_install_digest(install)}
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60,
                                 protected_paths=(str(protected),), read_only_roots=[pin],
                                 stock_config={"permission": "allow", "agent": {"title": {"disable": True}}},
                                 environment=isolated_environment(tmp_path / "ignored-home"))
    try:
        session.user([{"type": "text", "text": "Exercise fixture isolation."}])
        session.submit(response(session, [("read_protected", "read", {"filePath": str(secret)}),
                         ("write_install", "bash", {"command": f"printf changed > {data}",
                                                    "description": "Check read-only fixture"})]))
        denied = session.result("read_protected")["__mvi_text_output"]
        assert "fixture-secret-never-return" not in denied
        assert "permission denied" in denied.lower() or "operation not permitted" in denied.lower()
        assert "operation not permitted" in session.result("write_install")["__mvi_text_output"].lower()
        assert data.read_text() == "unchanged"
    finally:
        session.close()


def test_home_auth_and_global_config_are_ignored(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fake_home = tmp_path / "host-home"
    (fake_home / ".config/opencode").mkdir(parents=True)
    (fake_home / ".config/opencode/opencode.json").write_text(json.dumps({
        "provider": {"host-only": {"npm": "@ai-sdk/openai", "options": {"apiKey": "not-a-real-secret"}}}}))
    (fake_home / ".local/share/opencode").mkdir(parents=True)
    (fake_home / ".local/share/opencode/auth.json").write_text(json.dumps({
        "host-only": {"type": "api", "key": "not-a-real-secret"}}))
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60,
                                 environment={"HOME": str(fake_home), "XDG_CONFIG_HOME": str(fake_home / ".config"),
                                              "XDG_DATA_HOME": str(fake_home / ".local/share"),
                                              "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1"})
    try:
        config = session.api("GET", "/config")
        assert "host-only" not in config.get("provider", {})
        assert "host-only" not in session.api("GET", "/provider")["connected"]
        assert config.get("permission") != "allow"
    finally:
        session.close()


def test_project_provider_fails_loudly_before_model_call(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "opencode.json").write_text(json.dumps({"provider": {"project-unrouted": {
        "npm": "@ai-sdk/openai", "options": {"baseURL": "http://127.0.0.1:1/v1", "apiKey": "fixture"},
        "models": {"fixture": {"name": "Fixture"}}}}}))
    with pytest.raises(StockExecutionError, match="project-unrouted"):
        OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60,
                            environment={"OPENCODE_DISABLE_DEFAULT_PLUGINS": "1"})


def test_zen_route_is_declared_only_and_keeps_stock_public_auth():
    router = ProviderRouter(get_provider_broker())
    model = "opencode/minimax-m2.5-free"
    assert "opencode" not in router.providers
    with pytest.raises(ValueError, match=model):
        router.get_declared_provider_config(model, set())
    config, actual, native = router.get_declared_provider_config(model, {model})
    assert actual == "minimax-m2.5-free" and native
    assert config.base_url == "https://opencode.ai/zen/v1"
    assert config.default_headers == {"authorization": "Bearer public"}
    assert not config.credential_required
    assert "opencode" not in router.providers


def test_unroutable_child_model_is_not_substituted():
    router = ProviderRouter(get_provider_broker())
    with pytest.raises(ValueError, match="openai/undeclared-child"):
        router.get_declared_provider_config("openai/undeclared-child", {MODEL})
