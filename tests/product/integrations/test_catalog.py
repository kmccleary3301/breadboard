from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from jsonschema import Draft202012Validator
from breadboard.product.integrations import CaptureIntegrationAdapter, IncompatibleAdapterError, IntegrationCatalog, IntegrationDescriptor, IntegrationError, ProbeReport, ProjectDeclarationError, internal_capture_adapters, load_capture_entry_points, resolve_local_capture_declaration


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
    descriptor = IntegrationDescriptor("bb.integration_descriptor.v1", "tool:x", "tool_executor", "v1", "impl")
    class Bad:
        def __init__(self) -> None: self.descriptor = descriptor
        def probe(self) -> ProbeReport: return ProbeReport("bb.capability_probe_report.v1", "probe:x", "tool:x", "host_driver", "impl", "available", checked_at_utc="now")
    assert IntegrationCatalog([Bad()]).probe("tool:x").status == "unavailable"


def test_configuration_schema_id_is_colon_free() -> None:
    IntegrationDescriptor("bb.integration_descriptor.v1", "tool:x", "tool_executor", "v1", "impl", (), "schema_v1")
    with pytest.raises(IntegrationError): IntegrationDescriptor("bb.integration_descriptor.v1", "tool:x", "tool_executor", "v1", "impl", (), "schema:v1")


def test_records_match_public_schemas_and_traversal_is_rejected(tmp_path: Path) -> None:
    catalog = IntegrationCatalog(internal_capture_adapters())
    for name, record in (("bb.integration_descriptor.v1", catalog.list()[0].to_record()), ("bb.capability_probe_report.v1", catalog.probe("capture:json").to_record())):
        schema = json.load(open("contracts/public/schemas/" + name + ".schema.json"))
        assert list(Draft202012Validator(schema).iter_errors(record)) == []
    declaration = {"adapter_id": "local", "source_path": "../adapter.py", "source_sha256": "sha256:" + "0" * 64, "grants": ["capture"]}
    with pytest.raises(ProjectDeclarationError): resolve_local_capture_declaration(declaration, project_root=str(tmp_path), adapter=internal_capture_adapters()[0])
