from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from jsonschema import Draft202012Validator
from agentic_coder_prototype.provider.routing import ProviderDescriptor
from breadboard.product.integrations import CaptureIntegrationAdapter, IncompatibleAdapterError, IntegrationCatalog, IntegrationDescriptor, IntegrationError, ProbeReport, ProjectDeclarationError, internal_capture_adapters, load_capture_entry_points, resolve_local_capture_declaration
from breadboard.product.integrations.provider import ProviderRuntimeAdapter


def test_internal_and_external_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = IntegrationCatalog(internal_capture_adapters())
    assert [x.integration_id for x in catalog.list(kind="capture_adapter")] == ["capture:json", "capture:memory"]
    adapter = CaptureIntegrationAdapter("external", lambda value: value, source_sha256="sha256:" + "a" * 64)
    point = SimpleNamespace(name="external", load=lambda: adapter)
    monkeypatch.setattr("breadboard.product.integrations.capture.metadata.entry_points", lambda: SimpleNamespace(select=lambda group: [point]))
    assert load_capture_entry_points()[0].descriptor.integration_id == "capture:external"
    monkeypatch.setattr("breadboard.product.integrations.capture.metadata.entry_points", lambda: SimpleNamespace(select=lambda group: [SimpleNamespace(name="bad", load=lambda: object())]))
    with pytest.raises(IncompatibleAdapterError): load_capture_entry_points()


def test_local_declaration_is_hash_and_grant_bound(tmp_path: Path) -> None:
    source = tmp_path / "adapter.py"; source.write_text("capture adapter\n"); digest = "sha256:" + sha256(source.read_bytes()).hexdigest()
    adapter = CaptureIntegrationAdapter("local", lambda value: value, source_sha256=digest)
    declaration = {"adapter_id": "local", "source_path": "adapter.py", "source_sha256": digest, "grants": ["capture"]}
    assert resolve_local_capture_declaration(declaration, project_root=str(tmp_path), adapter=adapter) is adapter
    with pytest.raises(ProjectDeclarationError): resolve_local_capture_declaration({**declaration, "grants": []}, project_root=str(tmp_path), adapter=adapter)


def test_probe_identity_and_timestamp_fail_closed() -> None:
    with pytest.raises(IntegrationError): ProbeReport("bb.capability_probe_report.v1", "probe:x", "x", "tool_executor", "", "available")
    with pytest.raises(IntegrationError): ProbeReport("bb.capability_probe_report.v1", "probe:x", "tool:x", "tool_executor", "impl", "available", checked_at_utc="now")
    descriptor = IntegrationDescriptor("bb.integration_descriptor.v1", "tool:x", "tool_executor", "v1", "impl")
    class Bad:
        def __init__(self) -> None: self.descriptor = descriptor
        def probe(self) -> ProbeReport: return ProbeReport("bb.capability_probe_report.v1", "probe:x", "tool:x", "host_driver", "impl", "available", checked_at_utc="now")
    assert IntegrationCatalog([Bad()]).probe("tool:x").status == "unavailable"


@pytest.mark.parametrize("checked_at_utc", ["0000-01-01T00:00:00Z", "2026-07-21T12:34:56Z", "2026-07-21t12:34:56z", "2016-12-31T23:59:60Z", "2017-01-01T00:59:60+01:00", "2026-07-21T12:34:56+05:30"])
def test_probe_accepts_rfc3339_timestamps(checked_at_utc: str) -> None:
    ProbeReport("bb.capability_probe_report.v1", "probe:x", "tool:x", "tool_executor", "impl", "available", checked_at_utc=checked_at_utc)


@pytest.mark.parametrize("checked_at_utc", ["now", "2026-07-21T12:34:56", "2026-02-30T12:34:56Z", "2026-07-21T24:00:00Z", "2026-07-21T12:34:60Z", "2026-07-21T12:34:56+24:00", "0001-01-01T00:00:60+00:01", "9999-12-31T23:59:60-00:01", None])
def test_probe_rejects_non_rfc3339_timestamps(checked_at_utc: object) -> None:
    with pytest.raises(IntegrationError):
        ProbeReport("bb.capability_probe_report.v1", "probe:x", "tool:x", "tool_executor", "impl", "available", checked_at_utc=checked_at_utc)


def test_configuration_schema_id_is_colon_free() -> None:
    IntegrationDescriptor("bb.integration_descriptor.v1", "tool:x", "tool_executor", "v1", "impl", (), "schema_v1")
    with pytest.raises(IntegrationError): IntegrationDescriptor("bb.integration_descriptor.v1", "tool:x", "tool_executor", "v1", "impl", (), "schema:v1")


def test_public_metadata_arrays_are_non_empty_and_unique() -> None:
    with pytest.raises(IntegrationError): IntegrationDescriptor("bb.integration_descriptor.v1", "tool:x", "tool_executor", "v1", "impl", capabilities=("same", "same"))
    with pytest.raises(IntegrationError): IntegrationDescriptor("bb.integration_descriptor.v1", "tool:x", "tool_executor", "v1", "impl", secret_reference_names=("TOKEN", "TOKEN"))
    for values in (("",), ("same", "same")):
        with pytest.raises(IntegrationError): ProbeReport("bb.capability_probe_report.v1", "probe:x", "tool:x", "tool_executor", "impl", "available", capabilities=values, checked_at_utc="2026-07-21T00:00:00Z")
        with pytest.raises(IntegrationError): ProbeReport("bb.capability_probe_report.v1", "probe:x", "tool:x", "tool_executor", "impl", "available", effects=values, checked_at_utc="2026-07-21T00:00:00Z")
        with pytest.raises(IntegrationError): ProbeReport("bb.capability_probe_report.v1", "probe:x", "tool:x", "tool_executor", "impl", "available", permissions=values, checked_at_utc="2026-07-21T00:00:00Z")


def test_capture_adapter_batches_register_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    existing = internal_capture_adapters()[0]
    added = CaptureIntegrationAdapter("added", lambda value: value, source_sha256="sha256:" + "a" * 64)
    catalog = IntegrationCatalog([existing])
    monkeypatch.setattr("breadboard.product.integrations.capture.load_capture_entry_points", lambda group: [added, existing])
    with pytest.raises(IntegrationError): catalog.load_capture_adapters()
    assert [item.integration_id for item in catalog.list()] == [existing.descriptor.integration_id]

    first_source = tmp_path / "first.py"; first_source.write_text("first\n")
    second_source = tmp_path / "second.py"; second_source.write_text("second\n")
    first_digest = "sha256:" + sha256(first_source.read_bytes()).hexdigest()
    second_digest = "sha256:" + sha256(second_source.read_bytes()).hexdigest()
    first = CaptureIntegrationAdapter("first", lambda value: value, source_sha256=first_digest)
    second = CaptureIntegrationAdapter("second", lambda value: value, source_sha256=second_digest)
    declarations = [
        {"adapter_id": "first", "source_path": "first.py", "source_sha256": first_digest, "grants": ["capture"]},
        {"adapter_id": "second", "source_path": "second.py", "source_sha256": "sha256:" + "f" * 64, "grants": ["capture"]},
    ]
    catalog = IntegrationCatalog()
    with pytest.raises(ProjectDeclarationError): catalog.preflight(declarations, project_root=str(tmp_path), adapters={"first": first, "second": second})
    assert catalog.list() == []


def test_records_match_public_schemas_and_traversal_is_rejected(tmp_path: Path) -> None:
    catalog = IntegrationCatalog(internal_capture_adapters())
    for name, record in (("bb.integration_descriptor.v1", catalog.list()[0].to_record()), ("bb.capability_probe_report.v1", catalog.probe("capture:json").to_record())):
        schema = json.load(open("contracts/public/schemas/" + name + ".schema.json"))
        assert list(Draft202012Validator(schema).iter_errors(record)) == []
    declaration = {"adapter_id": "local", "source_path": "../adapter.py", "source_sha256": "sha256:" + "0" * 64, "grants": ["capture"]}
    with pytest.raises(ProjectDeclarationError): resolve_local_capture_declaration(declaration, project_root=str(tmp_path), adapter=internal_capture_adapters()[0])


def test_provider_replay_client_identity_binds_creation_options() -> None:
    descriptor = ProviderDescriptor("fixture", "fixture_runtime", "chat", True, False, False, False, "openai", None, "FIXTURE_KEY", {})

    class Runtime:
        def create_client(self, api_key: str, *, base_url: str | None = None, default_headers: dict[str, str] | None = None):
            return SimpleNamespace(api_key=api_key, base_url=base_url, timeout=10, max_retries=2)
        def invoke(self, **kwargs):
            raise AssertionError("not used")

    adapter = ProviderRuntimeAdapter(Runtime(), descriptor)
    first = adapter.create_client("secret", base_url="https://fixture.invalid", default_headers={"X-Tenant": "one"})
    second = adapter.create_client("secret", base_url="https://fixture.invalid", default_headers={"X-Tenant": "two"})
    first_identity = adapter.replay_client_identity(first, {"FIXTURE_KEY": "secret"})
    second_identity = adapter.replay_client_identity(second, {"FIXTURE_KEY": "secret"})
    assert first_identity["verified"] is True
    assert first_identity["creation_options"] != second_identity["creation_options"]
    assert adapter.replay_client_identity(SimpleNamespace(api_key="secret"), {"FIXTURE_KEY": "secret"})["verified"] is False
    assert "secret" not in json.dumps(first_identity)
    assert "one" not in json.dumps(first_identity)


def test_provider_replay_client_identity_supports_mapping_clients() -> None:
    descriptor = ProviderDescriptor("fixture", "fixture_runtime", "chat", True, False, False, False, "openai", None, "FIXTURE_KEY", {})

    class Runtime:
        def create_client(self, api_key: str, *, base_url: str | None = None, default_headers: dict[str, str] | None = None):
            return {"api_key": api_key, "base_url": base_url}
        def invoke(self, **kwargs):
            raise AssertionError("not used")

    adapter = ProviderRuntimeAdapter(Runtime(), descriptor)
    client = adapter.create_client("secret", base_url="https://fixture.invalid")
    assert adapter.replay_client_identity(client, {"FIXTURE_KEY": "secret"})["verified"] is True
    client["api_key"] = "changed"
    assert adapter.replay_client_identity(client, {"FIXTURE_KEY": "secret"})["verified"] is False
    client["api_key"] = "secret"
    client["base_url"] = "https://changed.invalid"
    assert adapter.replay_client_identity(client, {"FIXTURE_KEY": "secret"})["verified"] is False
