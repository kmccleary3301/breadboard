from __future__ import annotations

import os
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException, status

from agentic_coder_prototype.api.cli_bridge.models import (
    ATPReplBatchRequest,
    ATPReplRequest,
    ATPReplResponse,
    ErrorResponse,
    EvoLakeHealthResponse,
    EvoLakeHelloResponse,
    EvoLakeRunCampaignRequest,
    EvoLakeRunCampaignResponse,
)
from agentic_coder_prototype.api.cli_bridge.service import SessionService

from breadboard.ext.interfaces import EndpointProvider

from .manifest import MANIFEST
from .tools import (
    _build_campaign_record,
    append_anchor_records,
    list_campaign_checkpoints,
    load_campaign_checkpoint,
    normalize_tenant_id,
    persist_campaign_record_scoped,
    resolve_resume_round,
    summarize_batch_result,
    write_campaign_checkpoint,
    write_replay_artifacts,
)
from .contracts import (
    EVOLAKE_CAMPAIGN_API_VERSION,
    validate_campaign_payload,
)


def _coerce_positive_int(value, default: int, *, max_value: int = 256) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    if parsed <= 0:
        parsed = default
    if parsed > max_value:
        parsed = max_value
    return parsed


def _build_router(get_service) -> APIRouter:
    router = APIRouter()

    def _budget_diagnostic(message: str, *, detail: dict) -> dict:
        return {
            "classification": "budget",
            "severity": "error",
            "title": "Campaign budget exceeded",
            "action": "Reduce requested campaign rounds/iterations/time and retry.",
            "message": message,
            "detail": detail,
        }

    def _budget_limits() -> dict:
        return {
            "max_rounds": _coerce_positive_int(os.environ.get("EVOLAKE_MAX_ROUNDS", 256), default=256, max_value=8192),
            "max_iterations": _coerce_positive_int(
                os.environ.get("EVOLAKE_MAX_ITERATIONS", 4096), default=4096, max_value=1_000_000
            ),
            "max_candidate_limit": _coerce_positive_int(
                os.environ.get("EVOLAKE_MAX_CANDIDATE_LIMIT", 1024), default=1024, max_value=1_000_000
            ),
            "max_wall_time_s": float(os.environ.get("EVOLAKE_MAX_WALL_TIME_S", "3600") or "3600"),
        }

    def _validate_budget(payload: dict, round_count: int) -> tuple[bool, dict | None]:
        budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
        limits = _budget_limits()
        if round_count > int(limits["max_rounds"]):
            return (
                False,
                _budget_diagnostic(
                    "rounds exceed policy limit",
                    detail={"rounds": round_count, "max_rounds": limits["max_rounds"]},
                ),
            )
        if budget:
            iterations = budget.get("iterations")
            if isinstance(iterations, int) and iterations > int(limits["max_iterations"]):
                return (
                    False,
                    _budget_diagnostic(
                        "iterations exceed policy limit",
                        detail={"iterations": iterations, "max_iterations": limits["max_iterations"]},
                    ),
                )
            candidate_limit = budget.get("candidate_limit")
            if isinstance(candidate_limit, int) and candidate_limit > int(limits["max_candidate_limit"]):
                return (
                    False,
                    _budget_diagnostic(
                        "candidate_limit exceeds policy limit",
                        detail={
                            "candidate_limit": candidate_limit,
                            "max_candidate_limit": limits["max_candidate_limit"],
                        },
                    ),
                )
            wall_time_s = budget.get("wall_time_s")
            if isinstance(wall_time_s, (int, float)) and float(wall_time_s) > float(limits["max_wall_time_s"]):
                return (
                    False,
                    _budget_diagnostic(
                        "wall_time_s exceeds policy limit",
                        detail={"wall_time_s": wall_time_s, "max_wall_time_s": limits["max_wall_time_s"]},
                    ),
                )
        return True, None

    @router.get(
        "/ext/evolake/health",
        response_model=EvoLakeHealthResponse,
    )
    async def evolake_health() -> EvoLakeHealthResponse:
        return EvoLakeHealthResponse()

    @router.get(
        "/ext/evolake/hello",
        response_model=EvoLakeHelloResponse,
    )
    async def evolake_hello() -> EvoLakeHelloResponse:
        return EvoLakeHelloResponse()

    @router.post(
        "/ext/evolake/tools/run_campaign",
        response_model=EvoLakeRunCampaignResponse,
        responses={
            400: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def evolake_run_campaign(
        payload: EvoLakeRunCampaignRequest,
        svc: SessionService = Depends(get_service),
    ) -> EvoLakeRunCampaignResponse:
        record_payload = payload.payload if isinstance(payload.payload, dict) else {}
        issues = validate_campaign_payload(record_payload)
        if issues:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "evolake_payload_invalid",
                    "message": "EvoLake campaign payload validation failed",
                    "detail": [{"path": issue.path, "message": issue.message} for issue in issues],
                },
            )
        record = _build_campaign_record(record_payload)
        tenant_id = normalize_tenant_id(record_payload.get("tenant_id"))
        atp_payload = record_payload.get("atp") if isinstance(record_payload, dict) else None
        rounds_source = record_payload.get("rounds")
        if isinstance(atp_payload, dict) and atp_payload.get("rounds") is not None:
            rounds_source = atp_payload.get("rounds")
        round_count = _coerce_positive_int(rounds_source, default=1)
        budget_ok, budget_diag = _validate_budget(record_payload, round_count=round_count)
        record["tenant_id"] = tenant_id
        record["rounds"] = round_count
        record["api_version"] = EVOLAKE_CAMPAIGN_API_VERSION

        atp_requests = []
        if isinstance(atp_payload, dict) and isinstance(atp_payload.get("requests"), list):
            atp_requests = list(atp_payload.get("requests") or [])
        elif isinstance(record_payload, dict) and isinstance(record_payload.get("atp_requests"), list):
            atp_requests = list(record_payload.get("atp_requests") or [])

        if not budget_ok:
            return EvoLakeRunCampaignResponse(
                status="rejected",
                dry_run=bool(payload.dry_run),
                error_code="evolake_budget_exceeded",
                error_detail=budget_diag,
                harness_diagnostic=budget_diag,
                received=record,
            )

        resume_from = record_payload.get("resume_from")
        checkpoint_id = record_payload.get("checkpoint_id")
        if resume_from is not None and checkpoint_id is not None:
            return EvoLakeRunCampaignResponse(
                status="rejected",
                dry_run=bool(payload.dry_run),
                error_code="evolake_resume_conflict",
                error_detail={"message": "Provide either checkpoint_id or resume_from, not both."},
                harness_diagnostic={
                    "classification": "request_shape",
                    "severity": "error",
                    "title": "Invalid resume selector",
                    "action": "Use only one of checkpoint_id or resume_from.",
                },
                received=record,
            )

        start_round = 0
        resume_checkpoint = None
        if not payload.dry_run and (checkpoint_id is not None or resume_from is not None):
            try:
                start_round, resume_checkpoint = resolve_resume_round(
                    tenant_id=tenant_id,
                    campaign_id=record["campaign_id"],
                    checkpoint_id=str(checkpoint_id).strip() if checkpoint_id else None,
                    resume_from=int(resume_from) if isinstance(resume_from, int) else None,
                )
            except Exception as exc:
                return EvoLakeRunCampaignResponse(
                    status="rejected",
                    dry_run=False,
                    error_code="evolake_checkpoint_not_found",
                    error_detail={"message": str(exc)},
                    harness_diagnostic={
                        "classification": "checkpoint",
                        "severity": "error",
                        "title": "Checkpoint resume failed",
                        "action": "Use an existing checkpoint_id for this tenant/campaign.",
                    },
                    received=record,
                )
        record["start_round"] = start_round
        if resume_checkpoint is not None:
            record["resume_checkpoint"] = resume_checkpoint

        atp_result = None
        atp_error = None
        atp_anchor_rows = []
        if atp_requests:
            if payload.dry_run:
                atp_result = {
                    "status": "dry_run",
                    "count": len(atp_requests),
                    "rounds_requested": round_count,
                }
            else:
                try:
                    rounds = []
                    for round_index in range(start_round, round_count):
                        batch_reqs = []
                        for item in atp_requests:
                            if not isinstance(item, dict):
                                raise ValueError("atp_requests entries must be objects")
                            if item.get("tenant_id") is None:
                                item = dict(item)
                                item["tenant_id"] = tenant_id
                            batch_reqs.append(ATPReplRequest(**item))
                        batch = ATPReplBatchRequest(requests=batch_reqs)
                        atp_resp = await svc.atp_repl_batch(batch)
                        batch_payload = atp_resp.model_dump()
                        rounds.append(
                            {
                                "round": round_index,
                                "result": batch_payload,
                            }
                        )
                        atp_anchor_rows.extend(
                            summarize_batch_result(batch_payload, round_index=round_index)
                        )
                        checkpoint_payload = {
                            "round_result_count": len(batch_payload.get("results") or []),
                            "anchor_rows_added": len(
                                [row for row in atp_anchor_rows if int(row.get("round") or -1) == round_index]
                            ),
                        }
                        write_campaign_checkpoint(
                            tenant_id=tenant_id,
                            campaign_id=record["campaign_id"],
                            round_index=round_index,
                            status="ok",
                            payload=checkpoint_payload,
                        )
                    atp_result = {
                        "status": "ok",
                        "requests_per_round": len(atp_requests),
                        "rounds_requested": round_count,
                        "rounds_executed": len(rounds),
                        "rounds": rounds,
                    }
                except Exception as exc:
                    atp_error = str(exc)
                    atp_result = {
                        "status": "error",
                        "requests_per_round": len(atp_requests),
                        "rounds_requested": round_count,
                        "rounds_executed": 0 if not atp_result else int(atp_result.get("rounds_executed") or 0),
                    }
                    write_campaign_checkpoint(
                        tenant_id=tenant_id,
                        campaign_id=record["campaign_id"],
                        round_index=max(start_round, int(atp_result.get("rounds_executed") or 0)),
                        status="error",
                        payload={"error": atp_error},
                    )

        if atp_result is not None:
            record["atp"] = atp_result
        if atp_error is not None:
            record["atp_error"] = atp_error
        if atp_anchor_rows and not payload.dry_run:
            anchor_path = append_anchor_records(
                tenant_id=tenant_id,
                campaign_id=record["campaign_id"],
                records=atp_anchor_rows,
            )
            record["anchors"] = {
                "path": str(anchor_path),
                "rows": len(atp_anchor_rows),
            }
            replay = write_replay_artifacts(
                tenant_id=tenant_id,
                campaign_id=record["campaign_id"],
                rows=atp_anchor_rows,
            )
            record["replay_artifacts"] = replay

        if not payload.dry_run:
            checkpoints = list_campaign_checkpoints(tenant_id, record["campaign_id"])
            record["checkpoints"] = {
                "count": len(checkpoints),
                "latest": checkpoints[-1] if checkpoints else None,
            }

        if payload.dry_run:
            return EvoLakeRunCampaignResponse(
                status="dry_run",
                dry_run=True,
                received=record,
            )

        out_path = persist_campaign_record_scoped(record, tenant_id=tenant_id)
        record["campaign_record_path"] = str(out_path)
        return EvoLakeRunCampaignResponse(
            status="queued",
            dry_run=False,
            received=record,
        )

    @router.post(
        "/ext/evolake/tools/atp_repl",
        response_model=ATPReplResponse,
        responses={
            400: {"model": dict},
            503: {"model": dict},
        },
    )
    async def evolake_atp_repl(payload: ATPReplRequest, svc: SessionService = Depends(get_service)):
        # Thin proxy to the ATP endpoint to make the integration path explicit.
        return await svc.atp_repl(payload)

    return router


class _EvoLakeRoutes(EndpointProvider):
    def register_routes(self, app, get_service) -> None:
        app.include_router(_build_router(get_service))


class EvoLakeBridgeExtension:
    manifest = MANIFEST

    def providers(self) -> Iterable[object]:
        return [_EvoLakeRoutes()]
