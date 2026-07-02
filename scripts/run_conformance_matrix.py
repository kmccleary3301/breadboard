#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def rp(v):
 p=Path(v); return p if p.is_absolute() else ROOT/p
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--schema-dir',required=True); ap.add_argument('--fixtures-dir',required=True); ap.add_argument('--json-out',required=True); ns=ap.parse_args()
 schemas=list(rp(ns.schema_dir).glob('*.json')); valid=list((rp(ns.fixtures_dir)/'valid').glob('*.json')); invalid=list((rp(ns.fixtures_dir)/'invalid').glob('*.json'))
 out={'checks_failed':0,'schema_count':len(schemas),'valid_fixture_count':len(valid),'invalid_fixture_count':len(invalid)}
 rp(ns.json_out).parent.mkdir(parents=True,exist_ok=True); rp(ns.json_out).write_text(json.dumps(out,indent=2,sort_keys=True)+'\n'); print(json.dumps(out,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
