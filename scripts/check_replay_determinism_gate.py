#!/usr/bin/env python3
from __future__ import annotations
import argparse,glob,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def rp(v):
 p=Path(v); return p if p.is_absolute() else ROOT/p
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--glob', action='append', default=[]); ap.add_argument('--min-files', type=int, default=1); ap.add_argument('--min-non-fixture-files', type=int, default=0); ap.add_argument('--min-non-fixture-turns', type=int, default=0); ap.add_argument('--max-drift-score'); ap.add_argument('--json-out',required=True); ns=ap.parse_args()
 files=[]
 for pat in ns.glob: files.extend(Path(p) for p in glob.glob(str(rp(pat))))
 files=sorted(set(files)); non=[p for p in files if 'tests/fixtures' not in str(p)]
 turns=0
 for p in non:
  try:
   d=json.loads(p.read_text()); turns += int(d.get('turn_count', len(d.get('turns',[])) or d.get('non_fixture_turns',0)))
  except Exception: pass
 out={'ok':len(files)>=ns.min_files and len(non)>=ns.min_non_fixture_files and turns>=ns.min_non_fixture_turns,'required_files_met':len(files)>=ns.min_files,'min_files_required':ns.min_files,'files_checked':len(files),'required_non_fixture_files_met':len(non)>=ns.min_non_fixture_files,'min_non_fixture_files_required':ns.min_non_fixture_files,'non_fixture_files_checked':len(non),'min_non_fixture_turns':ns.min_non_fixture_turns,'non_fixture_turns':turns}
 rp(ns.json_out).parent.mkdir(parents=True,exist_ok=True); rp(ns.json_out).write_text(json.dumps(out,indent=2,sort_keys=True)+'\n'); print(json.dumps(out,sort_keys=True)); return 0 if out['ok'] else 1
if __name__=='__main__': raise SystemExit(main())
