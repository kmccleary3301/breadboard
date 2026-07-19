from __future__ import annotations
import json
from pathlib import Path
import pytest
from scripts.rl_phase5.run_wp10_local_launcher_callback import REQUIRED_OBSERVATIONS, load_observations, parser, sanitized_command_digest
from scripts.rl_phase5.wp10_launcher_evidence import EvidenceError

def observation() -> dict:
    value={key:True for key in REQUIRED_OBSERVATIONS}
    value.update({"schema_version":"bb.rl.wp10-local-observation.v1","image_id":"sha256:"+"a"*64,"request_id":"request","episode_id":"episode","docker_capture":"docker.json","ray_capture":"ray.json","process_capture":"process.json","callback_invocations":1,"callback_requests":1,"callback_responses":1})
    return value

def test_real_v2_callback_observation_requires_every_leg(tmp_path: Path):
    path=tmp_path/"observation.json"; value=observation(); path.write_text(json.dumps(value)); assert load_observations(path)==value
    for key in ("docker_mount_observed","create_succeeded","run_succeeded","completed_observed","closed_observed","staged_secret_absent","callback_server_terminated"):
        broken=observation(); broken[key]=False; path.write_text(json.dumps(broken))
        with pytest.raises(EvidenceError): load_observations(path)

def test_mock_or_omitted_callback_cannot_claim_evidence(tmp_path: Path):
    path=tmp_path/"observation.json"
    for change in ({"real_v2_service":False},{"callback_invocations":0},{"callback_responses":0}):
        value=observation(); value.update(change); path.write_text(json.dumps(value))
        with pytest.raises(EvidenceError): load_observations(path)
    value=observation(); value.pop("ray_capture"); path.write_text(json.dumps(value))
    with pytest.raises(EvidenceError): load_observations(path)

def test_observation_symlink_is_rejected(tmp_path: Path):
    target=tmp_path/"target"; target.write_text(json.dumps(observation())); link=tmp_path/"link"; link.symlink_to(target)
    with pytest.raises(EvidenceError): load_observations(link)

def test_command_digest_classifies_operator_paths():
    first=sanitized_command_digest(["launch/generate_nemo.sh","--output-dir","/private/a"])
    second=sanitized_command_digest(["launch/generate_nemo.sh","--output-dir","/private/b"])
    assert first==second

def test_recipe_config_has_no_deleted_default():
    arguments=[
        "--wrapper-worktree","/tmp/wrapper","--wrapper-head","wrapper-head",
        "--breadboard-head","breadboard-head","--image","image@sha256:"+"1"*64,
        "--output-parent","/tmp","--base-url","http://127.0.0.1",
    ]
    with pytest.raises(SystemExit) as missing:
        parser().parse_args(arguments)
    assert missing.value.code==2
    parsed=parser().parse_args(arguments+["--recipe-config","configs/generated-zeta.yaml"])
    assert parsed.recipe_config=="configs/generated-zeta.yaml"
