// GENERATED FILE - do not edit by hand.
// generator: scripts/dev/generate_ts_sdk.py (deterministic, in-process, no network)
// openapi-schema-sha256: f93fb357f321e782724795c2230d1ca56dcd9610223b7560a3846a9370c5022c
// app-source-sha256: 4b61a81504a4eb7d916c371df69e2e2019f9191cc651a7c12504c5d15aaf45fc

export interface AuthActionResponse { "detail"?: { [key: string]: unknown; } | null; "ok": boolean; }

export interface AuthCredentialRefreshState { "expected_secret_version"?: number | null; "last_failure_at_ms"?: number | null; "last_failure_class"?: string | null; "last_failure_code"?: string | null; "lease_acquired_at_ms"?: number | null; "lease_expires_at_ms"?: number | null; "retry_not_before_ms"?: number | null; "status": string; "updated_at_ms"?: number | null; }

export interface AuthCredentialView { "account_id": string; "alias"?: string | null; "auth_scheme_id": string; "created_at_ms": number; "credential_id": string; "credential_kind"?: string; "expires_at_ms"?: number | null; "has_api_key"?: boolean; "label": string; "metadata"?: { [key: string]: unknown; }; "provider_id": string; "refresh_state"?: AuthCredentialRefreshState; "secret_version": number; "source"?: string; "status": string; "updated_at_ms": number; }

export interface AuthLoginSession { "authorization_url"?: string | null; "created_at_ms"?: number | null; "credential"?: AuthCredentialView | null; "expires_at_ms"?: number | null; "flow_id"?: string | null; "flow_kind"?: string | null; "instructions"?: string | null; "login_session_id": string; "problem"?: { [key: string]: unknown; } | null; "provider_id": string; "redirect_uri"?: string | null; "status": string; "updated_at_ms"?: number | null; "user_code"?: string | null; }

export interface AuthProviderView { "aliases": Array<string>; "auth_owner": "broker" | "provider"; "auth_schemes": Array<string>; "availability_reason"?: "provider_managed" | "missing_auth" | null; "available": boolean; "base_url"?: string | null; "compatible_protocol"?: string | null; "display_name": string; "login_available"?: boolean; "model_discovery": "configured_only"; "oauth_flows"?: Array<string>; "provider_id": string; "runtime_id"?: string | null; "support_tier": "core"; }

export interface BeginAuthLoginRequest { "auth_scheme_id"?: string | null; "flow"?: string; "provider_id": string; }

export interface BeginControlDrainRequest { "control_request_id": string; "engine_boot_id": string; "engine_instance_id": string; "expected_admission_epoch": number; "launch_id": string; "owner_generation": number; "registration_id": string; "requester_client_instance_id": string; "requester_registration_generation": number; }

export interface BootstrapChallengeRequest { "engine_boot_id": string; "engine_instance_id": string; "launch_id": string; }

export interface BootstrapChallengeResponse { "challenge": string; "challenge_id": string; "engine_boot_id": string; "engine_instance_id": string; "expires_at_unix": number; "launch_id": string; "schema_version"?: "bb.engine_bootstrap_challenge.v1"; }

export interface ClientLeaseRequest { "client_instance_id": string; "engine_instance_id": string; "registration_generation": number; "registration_id": string; }

export interface ClientRegisterRequest { "client_instance_id": string; "engine_instance_id": string; "first_slice_contract_id"?: "p30-e4-session-v1"; "first_slice_schema_sha256"?: "sha256:385c19de8557a958b10d4a78afc64014a200558b8f089295882a1d9eb4b5d55a"; "lifecycle_mode": "local-owned" | "local-external" | "remote" | "off"; "workspace_id": string; }

export interface ClientRegistrationResponse { "admission_epoch": number; "client_instance_id": string; "engine_instance_id": string; "expires_at_unix"?: number | null; "first_slice_contract_id"?: "p30-e4-session-v1"; "first_slice_schema_sha256"?: "sha256:385c19de8557a958b10d4a78afc64014a200558b8f089295882a1d9eb4b5d55a"; "lease_ttl_seconds"?: 30; "lifecycle_mode": "local-owned" | "local-external" | "remote"; "registered_at_unix": number; "registration_generation": number; "registration_id": string; "renewal_interval_seconds"?: 10; "result": "registered" | "renewed" | "detached" | "already_detached"; "schema_version"?: "bb.engine_client_registration.v1"; "workspace_id": string; }

export interface CompleteAuthLoginRequest { "account_label"?: string | null; "alias"?: string | null; "authorization_code"?: string | null; "callback_url"?: string | null; "code"?: string | null; "state"?: string | null; }

export interface DrainControlRequest { "drain_generation": number; "engine_boot_id": string; "engine_instance_id": string; "launch_id": string; "owner_generation": number; }

export interface DrainControlResponse { "admission_epoch": number; "control_request_id": string; "drain_generation": number; "engine_boot_id": string; "engine_instance_id": string; "launch_id": string; "registrations_open": boolean; "result": "draining" | "shutdown_started" | "rollback_permitted" | "hard_signal_decision_pending" | "signal_sent" | "process_exited" | "rolled_back"; "schema_version"?: "bb.engine_drain_control.v1"; "session_admission_open": boolean; "signal_permitted": boolean; "turn_admission_open": boolean; }

export interface EngineArtifactRevision { "engine_artifact_sha256": string; "served_backend_commit"?: string | null; "served_backend_dirty"?: boolean | null; }

export interface EngineIdentityReadinessResponse { "artifact_revision": EngineArtifactRevision; "launch": EngineLaunchIdentity; "liveness": EngineLiveness; "process": EngineProcessStart; "protocol": EngineProtocolIdentity; "schema_version"?: "bb.engine_identity.v1"; "session_contract": EngineSessionContractIdentity; "session_readiness": EngineSessionReadiness; }

export interface EngineLaunchIdentity { "launch_id": string; "source": "supervisor" | "external_unmanaged"; }

export interface EngineLiveness { "status"?: "live"; }

export interface EngineProcessStart { "engine_boot_id": string; "engine_instance_id": string; "os_process_start_token": string; "pid": number; "started_at": string; "started_at_unix": number; }

export interface EngineProtocolIdentity { "protocol_version"?: "1.0"; }

export interface EngineSessionContractIdentity { "compatibility": "compatible" | "incompatible"; "contract_id"?: "p30-e4-session-v1"; "schema_sha256"?: "sha256:385c19de8557a958b10d4a78afc64014a200558b8f089295882a1d9eb4b5d55a"; "sessionReplayContractDigest": string; }

export interface EngineSessionReadiness { "ready": boolean; "reason": "ready" | "session_contract_missing" | "session_contract_mismatch"; }

export interface ErrorResponse { "detail"?: string | { [key: string]: unknown; } | null; "error": string; "path"?: string | null; }

export interface GracefulControlResultRequest { "drain_generation": number; "engine_boot_id": string; "engine_instance_id": string; "launch_id": string; "outcome": "accepted" | "definitive_rejection" | "timeout" | "uncertain"; "owner_generation": number; }

export interface HTTPValidationError { "detail"?: Array<ValidationError>; }

export interface HardSignalCommitRequest { "authorization_id": string; "drain_generation": number; "engine_boot_id": string; "engine_instance_id": string; "launch_id": string; "os_process_start_token": string; "owner_generation": number; "pid": number; }

export interface HardSignalOutcomeRequest { "authorization_id": string; "drain_generation": number; "engine_boot_id": string; "engine_instance_id": string; "launch_id": string; "outcome": "sent" | "abandoned" | "process_exited"; "owner_generation": number; }

export interface HardSignalPermitResponse { "authorization_id": string; "drain_generation": number; "engine_boot_id": string; "engine_instance_id": string; "expires_at_unix": number; "launch_id": string; "owner_generation": number; "result"?: "signal_permitted"; "schema_version"?: "bb.engine_hard_signal_permit.v1"; "signal_permitted"?: true; }

export interface HardSignalPreparationResponse { "authorization_id": string; "drain_generation": number; "engine_boot_id": string; "engine_instance_id": string; "expires_at_unix": number; "launch_id": string; "owner_generation": number; "result"?: "prepared"; "schema_version"?: "bb.engine_hard_signal_preparation.v1"; "signal_permitted"?: false; }

export interface HardSignalPrepareRequest { "drain_generation": number; "engine_boot_id": string; "engine_instance_id": string; "launch_id": string; "os_process_start_token": string; "owner_generation": number; "pid": number; }

export interface HarnessCreateRequest { "directory"?: string; }

export interface HarnessUpdateRequest { "definition": { [key: string]: unknown; }; }

export interface ModelCatalogEntry { "adapter"?: string | null; "availability_reason"?: "provider_managed" | "missing_auth" | "unsupported_provider" | "deferred_provider" | null; "available": boolean; "canonical_provider"?: string | null; "context_length"?: number | null; "discovery": "configured_only"; "id": string; "metadata"?: { [key: string]: unknown; } | null; "name"?: string | null; "params"?: { [key: string]: unknown; } | null; "provider"?: string | null; "routing"?: { [key: string]: unknown; } | null; "source": "configured"; "support_tier": "core" | "deferred" | "evidence" | "unsupported"; }

export interface ModelCatalogIssue { "code": "invalid_model" | "duplicate_model" | "unsupported_provider" | "deferred_provider" | "stale_dynamic_catalog"; "index"?: number | null; "model_id"?: string | null; "provider_id"?: string | null; "source": "configured" | "dynamic"; }

export interface ModelCatalogResponse { "config_path"?: string | null; "default_model"?: string | null; "discovery_policy": "configured_only"; "issues": Array<ModelCatalogIssue>; "models": Array<ModelCatalogEntry>; }

export interface ModelRolesResolveRequest { "model_catalog"?: Array<{ [key: string]: unknown; }> | null; "model_roles": { [key: string]: unknown; }; "role_overrides"?: { [key: string]: unknown; } | null; "session_started"?: boolean; }

export interface ModelRolesResolveResponse { "lock": { [key: string]: unknown; }; "lock_hash": string; }

export interface OwnerAcquireRequest { "bootstrap_challenge_id"?: string | null; "bootstrap_proof_sha256"?: string | null; "engine_boot_id": string; "engine_instance_id": string; "expected_owner_generation": number; "launch_id": string; }

export interface OwnerLeaseRequest { "engine_boot_id": string; "engine_instance_id": string; "launch_id": string; "owner_generation": number; }

export interface OwnerLeaseResponse { "engine_boot_id": string; "engine_instance_id": string; "expires_at_unix"?: number | null; "launch_id": string; "lease_ttl_seconds"?: 30; "owner_generation": number; "renewal_interval_seconds"?: 10; "result": "acquired" | "renewed" | "released" | "already_released"; "schema_version"?: "bb.engine_owner.v1"; }

export interface Problem { "error_code": string; "failed_stage"?: string | null; "hint"?: string | null; "message": string; "next_actions"?: Array<string>; "record_refs"?: Array<string>; "schema_version"?: "bb.problem.v1"; }

export interface PublicResult { "command": Array<string>; "data": { [key: string]: unknown; }; "error": Problem | null; "exit_code": number; "hashes": { [key: string]: string; }; "next_actions": Array<string>; "ok": boolean; "record_refs": Array<string>; "schema_version"?: "bb.cli.result.v1"; "stage_outcomes": Array<StageOutcome>; "status": "ok" | "error"; "warnings": Array<string>; }

export interface PutApiKeyRequest { "alias"?: string | null; "api_key": string; "auth_scheme_id"?: string; "base_url"?: string | null; "expires_at_ms"?: number | null; "headers"?: { [key: string]: string; }; "metadata"?: { [key: string]: unknown; }; "routing"?: { [key: string]: unknown; } | null; "ttl_seconds"?: number | null; }

export interface SessionApprovalRequest { "decision": "allow" | "deny" | "once" | "always" | "reject"; "request_id": string; }

export interface SessionCancelRequest { "reason"?: string; }

export interface SessionInputRequest { "content": string; }

export interface SessionStartRequest { "lock_id": string; "session_id"?: string | null; "task": string; }

export interface StageOutcome { "next_action"?: string | null; "report_ref"?: string | null; "stage": string; "status": string; }

export interface ValidationError { "ctx"?: Record<string, unknown>; "input"?: unknown; "loc": Array<string | number>; "msg": string; "type": string; }
