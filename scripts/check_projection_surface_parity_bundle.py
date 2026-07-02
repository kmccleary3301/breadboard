#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def rp(v):
    p=Path(v); return p if p.is_absolute() else ROOT/p

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--artifact-dir', required=True); ap.add_argument('--surface-manifest', required=True); ap.add_argument('--json-out', required=True); ns=ap.parse_args()
    manifest=json.loads(rp(ns.surface_manifest).read_text()); artifact=rp(ns.artifact_dir)
    total=0; failures=0; surfaces=0; violations=[]
    for surface in manifest.get('surfaces',[]):
        surfaces+=1; p=artifact/surface['report']
        if not p.exists(): violations.append(f'missing {p.name}'); continue
        data=json.loads(p.read_text())
        if data.get('schema_version') != surface.get('expected_schema_version'): violations.append(f'{p.name} schema_version mismatch')
        total += int(data.get('cases_total',0)); failures += int(data.get('cases_failed',0))
        if not data.get('ok', False): violations.append(f'{p.name} not ok')
        if int(data.get('cases_total',0)) < int(surface.get('min_cases_total',0)): violations.append(f'{p.name} below min cases')
    out={'all_ok': not violations and failures==0, 'manifest_constraints_valid': not violations, 'surface_count':surfaces, 'total_cases':total, 'cases_failed':failures, 'violations':violations}
    rp(ns.json_out).parent.mkdir(parents=True, exist_ok=True); rp(ns.json_out).write_text(json.dumps(out, indent=2, sort_keys=True)+'\n'); print(json.dumps(out, sort_keys=True)); return 0 if out['all_ok'] else 1
if __name__=='__main__': raise SystemExit(main())
