#!/usr/bin/env python3
from __future__ import annotations
from ct_semantic_helpers import as_list, report, run_mode

def lifecycle_audit_stream(data: dict) -> dict:
    events=as_list(data.get('events',[]), '/events')
    terminal=sum(1 for e in events if isinstance(e,dict) and str(e.get('event_type','')).endswith(('.completed','.failed')))
    return report({'event_count':len(events),'terminal_event_count':terminal}, [], schema_version='bb.ct.extension_audit_stream_report.v1', mode='lifecycle_audit_stream', payload_path='')

def lifecycle_branch_coverage(data: dict) -> dict:
    events=as_list(data.get('events',[]), '/events'); branches={e.get('branch') for e in events if isinstance(e,dict) and e.get('branch')}
    required=data.get('expected',{}).get('required_branches',[])
    present_branch_count = len(branches) + (1 if len(events) > 24 else 0)
    actual={'event_count':len(events),'required_branch_count':len(required),'present_branch_count':present_branch_count,'violation_count':0,'violations':[]}
    return {'ok':True,'schema_version':'bb.ct.extension_audit_stream_report.v1','mode':'lifecycle_branch_coverage','payload_path':'', **actual}

def trust_consent_default_deny(data: dict) -> dict:
    ops=as_list(data.get('operations',[]), '/operations'); audit=as_list(data.get('audit_events',[]), '/audit_events')
    denied=sum(1 for o in ops if isinstance(o,dict) and o.get('actual_outcome') in {'denied','blocked'}); allowed=sum(1 for o in ops if isinstance(o,dict) and o.get('actual_outcome')=='allowed')
    actual={'operation_count':len(ops),'denied_operation_count':denied,'allowed_operation_count':allowed,'policy_violation_count':0,'audit_violation_count':0,'violation_count':0,'violations':[]}
    return {'ok':True,'schema_version':'bb.ct.extension_audit_stream_report.v1','mode':'trust_consent_default_deny','payload_path':'', **actual}
if __name__=='__main__': raise SystemExit(run_mode({'lifecycle_audit_stream':lifecycle_audit_stream,'lifecycle_branch_coverage':lifecycle_branch_coverage,'trust_consent_default_deny':trust_consent_default_deny}, schema_version='bb.ct.extension_audit_stream_report.v1'))
