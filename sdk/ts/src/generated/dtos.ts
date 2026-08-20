// GENERATED FILE - do not edit by hand.
// generator: scripts/dev/generate_ts_sdk.py (deterministic, in-process, no network)
// openapi-schema-sha256: 432b37826623941a5b10c332468cf4f52a5af81c205721333851b3de9446689c
// app-source-sha256: 93b5c80ff32b2403f480de2ee846fbe2c4328c2059774bd0e20f76755627d002

export interface HTTPValidationError { "detail"?: Array<ValidationError>; }

export interface HarnessCreateRequest { "directory"?: string; }

export interface HarnessUpdateRequest { "definition": { [key: string]: unknown; }; }

export interface Problem { "error_code": string; "failed_stage"?: string | null; "hint"?: string | null; "message": string; "next_actions"?: Array<string>; "record_refs"?: Array<string>; "schema_version"?: "bb.problem.v1"; }

export interface PublicResult { "command": Array<string>; "data": { [key: string]: unknown; }; "error": Problem | null; "exit_code": number; "hashes": { [key: string]: string; }; "next_actions": Array<string>; "ok": boolean; "record_refs": Array<string>; "schema_version"?: "bb.cli.result.v1"; "stage_outcomes": Array<StageOutcome>; "status": "ok" | "error"; "warnings": Array<string>; }

export interface SessionApprovalRequest { "decision": "allow" | "deny" | "once" | "always" | "reject"; "request_id": string; }

export interface SessionCancelRequest { "reason"?: string; }

export interface SessionInputRequest { "content": string; }

export interface SessionStartRequest { "lock_id": string; "session_id"?: string | null; "task": string; }

export interface StageOutcome { "next_action"?: string | null; "report_ref"?: string | null; "stage": string; "status": string; }

export interface ValidationError { "ctx"?: Record<string, unknown>; "input"?: unknown; "loc": Array<string | number>; "msg": string; "type": string; }
