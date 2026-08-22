from __future__ import annotations
import base64, io, json, os, tarfile, zipfile
from pathlib import Path
import pytest
from scripts.rl_phase5.wp10_launcher_evidence import EvidenceError, ScanLimits, SeededSecretScanner, canonical_json_bytes, load_manifest, validate_manifest, write_manifest

def manifest(tmp_path: Path) -> dict:
    digest="a"*64; head="b"*40
    artifact=tmp_path/"capture.json"; artifact.write_text("{}")
    return {"schema_version":"bb.rl.launcher-identity.v1","claim_class":"local_contract","run_id":"wp10-run-0001","breadboard_head":head,"wrapper_head":head,
      "files":[{"role":role,"repository":"wrapper" if role!="callback_script" else "breadboard","path":f"x/{role}","sha256":digest} for role in ("generate_launcher","eval_launcher","secret_helper","recipe_consumer","recipe_config","callback_script")],
      "image":{"reference":"repo/image@sha256:"+digest,"observed_id":"sha256:"+digest},"launcher":{"kind":"generate_nemo","command_template_sha256":digest,"started":True},
      "service":{"url_origin_path":"http://127.0.0.1:8000/v2","timeout_seconds":30,"real_v2_service":True},
      "boundaries":{key:{"observed":True,"capture_artifact":"capture.json"} for key in ("docker","ray","process")},
      "credential":{"present":True,"source":"token_file","container_path":"/run/secrets/breadboard_harness_token","source_mode":"0400","staged_mode":"0400","read_only":True,"deleted_after_use":True,"allowed_class":True},
      "v2":{"request_id":"r","episode_id":"e","create_succeeded":True,"run_succeeded":True,"completed_observed":True,"closed_observed":True},"callback":{"invocations":1,"requests":1,"responses":1},
      "cleanup":{"processes_terminated":True,"containers_absent":True,"staged_secret_absent":True,"callback_server_terminated":True},
      "artifacts":[{"path":"capture.json","sha256":__import__('hashlib').sha256(b"{}").hexdigest(),"size_bytes":2}],
      "scan":{"algorithm":"bb.rl.seeded-secret-scan.v1","passed":True,"files_scanned":1,"archive_members_scanned":0,"bytes_scanned":2,"inventory_complete":True},"ibm_target_proven":False}

def test_manifest_is_strict_and_canonical(tmp_path: Path):
    value=manifest(tmp_path); validate_manifest(value)
    path=tmp_path/"LAUNCHER_IDENTITY_MANIFEST.json"; write_manifest(path,value)
    assert path.read_bytes()==canonical_json_bytes(value); assert load_manifest(path)==value
    for key in ("image","callback","cleanup","scan"):
        broken=json.loads(json.dumps(value)); broken.pop(key)
        with pytest.raises(EvidenceError): validate_manifest(broken)

def test_manifest_rejects_mutable_secret_and_false_claims(tmp_path: Path):
    value=manifest(tmp_path)
    mutations=[("image",{"reference":"repo:latest","observed_id":"sha256:"+"a"*64}), ("ibm_target_proven",True), ("authorization","Bearer redacted")]
    for key,replacement in mutations:
        broken=json.loads(json.dumps(value)); broken[key]=replacement
        with pytest.raises(EvidenceError): validate_manifest(broken)

def test_scanner_finds_all_seed_encodings(tmp_path: Path):
    secret=b"Seeded-WP10-secret_42"
    encodings=[secret,json.dumps(secret.decode())[1:-1].encode(),__import__('urllib.parse').parse.quote_from_bytes(secret,safe="").encode(),base64.b64encode(secret),base64.urlsafe_b64encode(secret).rstrip(b"="),secret.hex().encode(),secret.decode().encode("utf-16le")]
    for index,payload in enumerate(encodings):
        root=tmp_path/str(index); root.mkdir(); (root/"nested").mkdir(); (root/"nested"/"capture.bin").write_bytes(payload)
        result=SeededSecretScanner(secret).scan([root.resolve()]); assert result.matches

def test_scanner_recurses_archives_and_rejects_omissions(tmp_path: Path):
    secret=b"archive-secret-value"; nested=io.BytesIO()
    with zipfile.ZipFile(nested,"w") as archive: archive.writestr("deep/value.txt",base64.b64encode(secret))
    outer=tmp_path/"outer.tar"
    with tarfile.open(outer,"w") as archive:
        info=tarfile.TarInfo("nested.zip"); info.size=len(nested.getvalue()); archive.addfile(info,io.BytesIO(nested.getvalue()))
    assert SeededSecretScanner(secret).scan([tmp_path.resolve()]).matches
    outer.unlink(); (tmp_path/"unsupported.gz").write_bytes(b"not scanned")
    with pytest.raises(EvidenceError,match="unsupported archive"): SeededSecretScanner(secret).scan([tmp_path.resolve()])
    (tmp_path/"unsupported.gz").unlink(); target=tmp_path/"target"; target.write_text("safe"); (tmp_path/"link").symlink_to(target)
    with pytest.raises(EvidenceError,match="symlink"): SeededSecretScanner(secret).scan([tmp_path.resolve()])

def test_scanner_fails_on_budgets(tmp_path: Path):
    (tmp_path/"a").write_bytes(b"12345")
    with pytest.raises(EvidenceError,match="budget"): SeededSecretScanner(b"secret",ScanLimits(max_file_bytes=4)).scan([tmp_path.resolve()])
