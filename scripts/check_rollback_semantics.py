#!/usr/bin/env python3
from __future__ import annotations
from ct_semantic_helpers import as_list, report, run_mode
def dropped_range_rewind(data: dict)->dict:
 dropped=as_list(data.get('dropped_ranges',[]), '/dropped_ranges'); return report({'dropped_count':len(dropped)}, [], schema_version='bb.ct.rollback_report.v1', mode='dropped_range_rewind', payload_path='')
if __name__=='__main__': raise SystemExit(run_mode({'dropped_range_rewind':dropped_range_rewind}, schema_version='bb.ct.rollback_report.v1'))
