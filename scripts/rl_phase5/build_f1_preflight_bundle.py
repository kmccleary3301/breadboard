from __future__ import annotations

import argparse
import hashlib
import gzip
import io
import json
import os
import stat
import tarfile
from pathlib import Path, PurePosixPath

BREADBOARD_MEMBERS=("breadboard/rl","agentic_coder_prototype/compilation","tests/compilation/test_server_compiler.py","tests/rl/harness/production_composition_fixture.py","tests/rl/harness/test_production_composition_public_lifecycle.py","tests/rl/harness/test_config_admission.py","tests/rl/harness/test_config_selection.py","tests/rl/harness/test_policy_capability_registry.py","tests/rl/harness/v2_service_fixtures.py","tests/fixtures/rl/harness/production_composition","tests/fixtures/rl/config_runtime")
WRAPPER_MEMBERS=("responses_api_agents/breadboard_agent","recipe/nemo_async/envs","recipe/nemo_common","third_party/nemo-gym/nemo_gym")
SCRIPT_MEMBERS=("scripts/rl_phase5/f1_container_entry.py","scripts/rl_phase5/run_f1_target_command.py","scripts/rl_phase5/f1_requirements.lock")

def _files(root: Path, members: tuple[str,...]):
    for member in members:
        source=root/member
        if not source.exists(): raise FileNotFoundError(source)
        paths=[source] if source.is_file() else sorted(source.rglob("*"))
        for path in paths:
            st=path.lstat()
            if stat.S_ISLNK(st.st_mode) or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                raise ValueError(f"links/special files forbidden: {path}")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc",".pyo"}:
                yield path, PurePosixPath(path.relative_to(root).as_posix())

def _git_head(root: Path) -> str:
    git = root / ".git"
    if git.is_file():
        line = git.read_text("utf-8").strip()
        if not line.startswith("gitdir: "):
            raise ValueError(f"invalid git file: {git}")
        git = (root / line[8:]).resolve()
    common = git
    if (git / "commondir").is_file():
        common = (git / (git / "commondir").read_text("utf-8").strip()).resolve()
    head = (git / "HEAD").read_text("ascii").strip()
    if head.startswith("ref: "):
        ref = head[5:]
        loose_candidates = (git / ref, common / ref)
        loose = next((candidate for candidate in loose_candidates if candidate.is_file()), None)
        if loose is not None:
            head = loose.read_text("ascii").strip()
        else:
            packed = common / "packed-refs"
            matches = [
                line.split(" ", 1)[0]
                for line in packed.read_text("ascii").splitlines()
                if line and not line.startswith(("#", "^")) and line.endswith(" " + ref)
            ]
            if len(matches) != 1:
                raise ValueError(f"cannot resolve git HEAD for {root}")
            head = matches[0]
    if not __import__("re").fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError(f"invalid git HEAD for {root}")
    return head
def build_bundle(breadboard_root: Path, wrapper_root: Path, output: Path) -> dict[str,object]:
    roots=((breadboard_root,BREADBOARD_MEMBERS+SCRIPT_MEMBERS),(wrapper_root,WRAPPER_MEMBERS))
    entries=[]
    for root,members in roots:
        for path,name in _files(root,members):
            raw=path.read_bytes(); after=path.stat()
            if after.st_size!=len(raw): raise RuntimeError(f"source changed during read: {path}")
            entries.append((str(name),raw))
    names=[x[0] for x in entries]
    if len(names)!=len(set(names)): raise ValueError("duplicate bundle member")
    entries.sort()
    inventory=[{"path":n,"size_bytes":len(b),"sha256":hashlib.sha256(b).hexdigest()} for n,b in entries]
    tree=hashlib.sha256(b"".join(n.encode()+b"\0"+hashlib.sha256(b).digest() for n,b in entries)).hexdigest()
    inv={"schema_version":"bb.rl.f1.source-bundle-inventory.v2","breadboard_head":_git_head(breadboard_root),"wrapper_head":_git_head(wrapper_root),"tree_sha256":tree,"members":inventory}
    entries.append(("F1_SOURCE_INVENTORY.json",json.dumps(inv,sort_keys=True,separators=(",",":")).encode()))
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("wb") as sink, gzip.GzipFile(filename="",mode="wb",fileobj=sink,compresslevel=9,mtime=0) as compressed, tarfile.open(fileobj=compressed,mode="w") as tf:
        for name,raw in entries:
            info=tarfile.TarInfo(name); info.size=len(raw); info.mode=0o644; info.mtime=0; info.uid=info.gid=0; info.uname=info.gname=""
            tf.addfile(info,io.BytesIO(raw))
    return {**inv,"archive_sha256":hashlib.sha256(output.read_bytes()).hexdigest(),"archive_size_bytes":output.stat().st_size}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--breadboard-root",type=Path,required=True); p.add_argument("--wrapper-root",type=Path,required=True); p.add_argument("--output",type=Path,required=True); p.add_argument("--inventory",type=Path,required=True); a=p.parse_args()
    result=build_bundle(a.breadboard_root.resolve(),a.wrapper_root.resolve(),a.output.resolve()); a.inventory.write_text(json.dumps(result,sort_keys=True,separators=(",",":"))+"\n")
if __name__=="__main__": main()
