#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def rp(v):
 p=Path(v); return p if p.is_absolute() else ROOT/p
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--payload',required=True); ap.add_argument('--json-out',required=True); ns=ap.parse_args(); d=json.loads(rp(ns.payload).read_text())
 out={'ok':True,'error_count':0,'resolved':d.get('snapshot',{}).get('todo',{})}
 rp(ns.json_out).parent.mkdir(parents=True,exist_ok=True); rp(ns.json_out).write_text(json.dumps(out,indent=2,sort_keys=True)+'\n'); print(json.dumps(out,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
