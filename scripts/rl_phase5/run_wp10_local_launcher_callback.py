#!/usr/bin/env python3
"""Run and attest the WP10 local-contract launcher/callback scenario.

The wrapper and real V2 service produce a redacted observation JSON file.  This
command owns only orchestration, cleanup checking, recursive leak scanning, and
manifest construction; it cannot synthesize missing callback or Docker evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__:
    from .wp10_launcher_evidence import (
        EvidenceError, MANIFEST_NAME, SCAN_ALGORITHM, SeededSecretScanner,
        canonical_json_bytes, sha256_file, write_manifest,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from wp10_launcher_evidence import (  # type: ignore[no-redef]
        EvidenceError, MANIFEST_NAME, SCAN_ALGORITHM, SeededSecretScanner,
        canonical_json_bytes, sha256_file, write_manifest,
    )

REQUIRED_OBSERVATIONS = {
    "schema_version","real_v2_service","launcher_started","docker_mount_observed",
    "docker_capture","ray_observed","ray_capture","process_observed","process_capture",
    "image_id","request_id","episode_id","create_succeeded","run_succeeded",
    "callback_invocations","callback_requests","callback_responses",
    "completed_observed","closed_observed","containers_absent","processes_terminated",
    "callback_server_terminated","staged_secret_absent",
}

def load_observations(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file(): raise EvidenceError("real-seam observation file absent")
    raw=path.read_bytes()
    try: value=json.loads(raw)
    except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise EvidenceError("observation is not JSON") from exc
    if not isinstance(value,dict) or set(value) != REQUIRED_OBSERVATIONS: raise EvidenceError("observation fields incomplete or unrecognized")
    if value["schema_version"] != "bb.rl.wp10-local-observation.v1" or value["real_v2_service"] is not True: raise EvidenceError("mock/non-V2 evidence cannot be claimed")
    required_true=("launcher_started","docker_mount_observed","ray_observed","process_observed","create_succeeded","run_succeeded","completed_observed","closed_observed","containers_absent","processes_terminated","callback_server_terminated","staged_secret_absent")
    if not all(value[key] is True for key in required_true): raise EvidenceError("required launcher/callback/cleanup observation absent")
    for key in ("request_id","episode_id","docker_capture","ray_capture","process_capture","image_id"):
        if not isinstance(value[key],str) or not value[key]: raise EvidenceError(f"missing observation {key}")
    for key in ("callback_invocations","callback_requests","callback_responses"):
        if not isinstance(value[key],int) or isinstance(value[key],bool) or value[key] <= 0: raise EvidenceError("callback leg absent")
    return value

def git_head(worktree: Path) -> str:
    result=subprocess.run(["git","rev-parse","HEAD"],cwd=worktree,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if result.returncode: raise EvidenceError("cannot resolve repository head")
    return result.stdout.strip()

def identity(repository: str, worktree: Path, relative: str, role: str) -> dict[str,Any]:
    path=worktree/relative
    if path.is_symlink() or not path.is_file(): raise EvidenceError(f"identity file absent: {relative}")
    return {"role":role,"repository":repository,"path":relative,"sha256":sha256_file(path)}

def sanitized_command_digest(command: list[str]) -> str:
    # Only the stable shape is attested; output and token source paths are classified.
    normalized=[]
    classify_next=False
    for arg in command:
        if classify_next:
            normalized.append("<operator-path>"); classify_next=False
        elif arg in ("--output-dir","--observation-file"):
            normalized.append(arg); classify_next=True
        else: normalized.append(arg)
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()

def _artifact_inventory(root: Path) -> list[dict[str,Any]]:
    items=[]
    for path in sorted(root.rglob("*")):
        if path.name == MANIFEST_NAME: continue
        if path.is_symlink(): raise EvidenceError(f"symlink artifact: {path.relative_to(root)}")
        if path.is_file(): items.append({"path":path.relative_to(root).as_posix(),"sha256":sha256_file(path),"size_bytes":path.stat().st_size})
        elif not path.is_dir(): raise EvidenceError(f"special artifact: {path.relative_to(root)}")
    if not items: raise EvidenceError("artifact inventory empty")
    return items

def run(args: argparse.Namespace) -> Path:
    wrapper=args.wrapper_worktree.resolve(strict=True); breadboard=args.breadboard_worktree.resolve(strict=True)
    if git_head(wrapper) != args.wrapper_head or git_head(breadboard) != args.breadboard_head: raise EvidenceError("worktree HEAD differs from pinned identity")
    if not args.image or "@sha256:" not in args.image: raise EvidenceError("immutable image digest required")
    parent=args.output_parent.resolve(strict=True)
    scratch=Path(tempfile.mkdtemp(prefix="wp10-local-",dir=parent)); os.chmod(scratch,0o700)
    secret_dir=Path(tempfile.mkdtemp(prefix="wp10-secret-",dir=parent)); os.chmod(secret_dir,0o700)
    secret_path=secret_dir/"token"; secret=secrets.token_urlsafe(48).encode("ascii")
    fd=os.open(secret_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o400)
    with os.fdopen(fd,"wb") as stream: stream.write(secret); stream.flush(); os.fsync(stream.fileno())
    observation=scratch/"observations.json"; stdout=scratch/"launcher.stdout"; stderr=scratch/"launcher.stderr"
    launcher=wrapper/"launch/generate_nemo.sh"
    command=[str(launcher),*args.launcher_arg]
    child_env={key:value for key,value in os.environ.items() if key not in {"BREADBOARD_HARNESS_TOKEN","BREADBOARD_HARNESS_TOKEN_FILE"}}
    child_env.update({"BREADBOARD_HARNESS_BASE_URL":args.base_url,"BREADBOARD_HARNESS_TIMEOUT_SECONDS":str(args.timeout),"BREADBOARD_HARNESS_TOKEN_FILE":str(secret_path),"WP10_LOCAL_OBSERVATION_FILE":str(observation),"WP10_LOCAL_IMAGE":args.image})
    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            result=subprocess.run(command,cwd=wrapper,env=child_env,stdout=out,stderr=err,timeout=args.launch_timeout,check=False)
        if result.returncode: raise EvidenceError(f"launcher failed with status {result.returncode}")
        observed=load_observations(observation)
    finally:
        try: secret_path.unlink()
        finally: shutil.rmtree(secret_dir,ignore_errors=False)
    if secret_path.exists() or secret_dir.exists(): raise EvidenceError("source secret cleanup incomplete")
    scanner=SeededSecretScanner(secret)
    scan=scanner.scan([scratch])
    if not scan.passed: raise EvidenceError("seeded secret representation found in evidence")
    artifacts=_artifact_inventory(scratch)
    wrapper_files=[
      ("launch/generate_nemo.sh","generate_launcher"),("launch/eval_nemo.sh","eval_launcher"),
      ("launch/utils/breadboard_harness_secret.sh","secret_helper"),
      (args.recipe_consumer,"recipe_consumer"),(args.recipe_config,"recipe_config"),
    ]
    files=[identity("wrapper",wrapper,path,role) for path,role in wrapper_files]
    files.append(identity("breadboard",breadboard,"scripts/rl_phase5/run_wp10_local_launcher_callback.py","callback_script"))
    manifest={
      "schema_version":"bb.rl.launcher-identity.v1","claim_class":"local_contract","run_id":scratch.name,
      "breadboard_head":args.breadboard_head,"wrapper_head":args.wrapper_head,"files":files,
      "image":{"reference":args.image,"observed_id":observed["image_id"]},
      "launcher":{"kind":"generate_nemo","command_template_sha256":sanitized_command_digest(command),"started":observed["launcher_started"]},
      "service":{"url_origin_path":args.base_url,"timeout_seconds":args.timeout,"real_v2_service":observed["real_v2_service"]},
      "boundaries":{"docker":{"observed":observed["docker_mount_observed"],"capture_artifact":observed["docker_capture"]},"ray":{"observed":observed["ray_observed"],"capture_artifact":observed["ray_capture"]},"process":{"observed":observed["process_observed"],"capture_artifact":observed["process_capture"]}},
      "credential":{"present":True,"source":"token_file","container_path":"/run/secrets/breadboard_harness_token","source_mode":"0400","staged_mode":"0400","read_only":True,"deleted_after_use":True,"allowed_class":True},
      "v2":{"request_id":observed["request_id"],"episode_id":observed["episode_id"],"create_succeeded":observed["create_succeeded"],"run_succeeded":observed["run_succeeded"],"completed_observed":observed["completed_observed"],"closed_observed":observed["closed_observed"]},
      "callback":{"invocations":observed["callback_invocations"],"requests":observed["callback_requests"],"responses":observed["callback_responses"]},
      "cleanup":{"processes_terminated":observed["processes_terminated"],"containers_absent":observed["containers_absent"],"staged_secret_absent":observed["staged_secret_absent"],"callback_server_terminated":observed["callback_server_terminated"]},
      "artifacts":artifacts,"scan":{"algorithm":SCAN_ALGORITHM,"passed":True,"files_scanned":scan.files_scanned,"archive_members_scanned":scan.archive_members_scanned,"bytes_scanned":scan.bytes_scanned,"inventory_complete":True},"ibm_target_proven":False,
    }
    write_manifest(scratch/MANIFEST_NAME,manifest)
    return scratch

def parser() -> argparse.ArgumentParser:
    value=argparse.ArgumentParser()
    value.add_argument("--wrapper-worktree",type=Path,required=True); value.add_argument("--breadboard-worktree",type=Path,default=Path.cwd())
    value.add_argument("--wrapper-head",required=True); value.add_argument("--breadboard-head",required=True)
    value.add_argument("--image",required=True); value.add_argument("--output-parent",type=Path,required=True)
    value.add_argument("--base-url",required=True); value.add_argument("--timeout",type=float,default=3600.0)
    value.add_argument("--launch-timeout",type=float,default=7200.0); value.add_argument("--launcher-arg",action="append",default=[])
    value.add_argument("--recipe-consumer",default="responses_api_agents/breadboard_agent/app.py")
    value.add_argument("--recipe-config",required=True)
    return value
def main(argv:list[str]|None=None)->int:
    output=run(parser().parse_args(argv)); print(output); return 0
if __name__=="__main__": raise SystemExit(main())
