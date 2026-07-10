"""Stage contracts for honest lane execution (Phase 20 WS-H).

Migration-compatible with the live adapter signature (SCOUT_FACTS S7):
    capture(lane_def, inventory_lane, *, promote_accepted=..., out_dir=...)
Legacy comparators exposing ``compare(*args, **kwargs)`` are wrapped by run_lane
with a keyword-forwarding shim; do NOT rewrite adapters in bulk (blast radius).
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict


class ReusedInput(TypedDict):
    path: str        # repo-relative path of the stored artifact being reused
    sha256: str      # ^sha256:[0-9a-f]{64}$ — digest at reuse time (provenance)


class StageReport(TypedDict):
    stage: Literal["capture", "normalize", "replay", "compare", "claim"]
    outcome: Literal[
        "executed_pass",          # stage ran; gates green; report_ref REQUIRED
        "executed_fail",          # stage ran (or should have run) and did not pass; detail REQUIRED
        "reused_stored_result",   # author-declared reuse of checked-in artifacts; manifest_rule + reused_inputs REQUIRED
        "disabled_by_manifest",   # author-declared off for this lane; manifest_rule REQUIRED
        "not_applicable",         # lane kind structurally lacks this stage; manifest_rule points at /kind
    ]
    manifest_rule: str | None     # JSON Pointer into the lane manifest authorizing reuse/disable/NA (e.g. "/replay/mode")
    reused_inputs: list[ReusedInput] | None   # provenance digests for reused_stored_result
    report_ref: str | None        # repo-relative stage artifact; REQUIRED for executed_*
    lock_sha256: str | None       # lane-lock digest this run executed against; None only for unmigrated legacy lanes
    detail: str                   # human-readable summary; REQUIRED non-empty for executed_fail


def check_stage_report(report: StageReport, manifest: Mapping[str, Any]) -> list[str]:
    """Generic, table-driven honesty check — the ONLY validator for these rules.

    Returns error strings; empty means honest. Rules:
      1. executed_pass/executed_fail  -> report_ref required (fail: detail required too).
      2. reused_stored_result        -> manifest_rule must RESOLVE in the manifest to one of:
                                        /replay/mode == "stored", /capture/strategy in
                                        {"replay_dump","runtime_records"};
                                        reused_inputs must be non-empty with valid digests.
                                        (normalize NEVER reuses: identity mode is an EXECUTED
                                        no-op translator emitting executed_pass — see WS-H H2.)
      3. disabled_by_manifest        -> manifest_rule must resolve and its value must be false/"off".
      4. not_applicable              -> manifest_rule must be "/kind" and the lane kind's stage table
                                        (STAGES_BY_KIND below) must omit this stage.
      5. Anything else is dishonest: report it.
    """
    errors: list[str] = []
    missing = object()

    def resolve(pointer: Any) -> Any:
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            return missing
        value: Any = manifest
        for raw_part in pointer[1:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, Mapping) and part in value:
                value = value[part]
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                return missing
        return value

    def executed() -> None:
        if not isinstance(report.get("report_ref"), str) or not report["report_ref"].strip():
            errors.append(f"{report.get('stage', '<unknown>')} {report.get('outcome')} requires report_ref")
        if report.get("outcome") == "executed_fail" and (
            not isinstance(report.get("detail"), str) or not report["detail"].strip()
        ):
            errors.append(f"{report.get('stage', '<unknown>')} executed_fail requires non-empty detail")

    def reused() -> None:
        stage = report.get("stage")
        pointer = report.get("manifest_rule")
        value = resolve(pointer)
        authorized = (
            (stage == "replay" and pointer == "/replay/mode" and value == "stored")
            or (
                stage == "capture"
                and pointer == "/capture/strategy"
                and isinstance(value, str)
                and value in {"replay_dump", "runtime_records"}
            )
        )
        if stage == "normalize":
            errors.append("normalize NEVER reuses stored results; identity normalize must execute")
        elif not authorized:
            errors.append(f"{stage} reused_stored_result manifest_rule does not resolve to an authorized declaration")
        reused_inputs = report.get("reused_inputs")
        if not isinstance(reused_inputs, list) or not reused_inputs:
            errors.append(f"{stage} reused_stored_result requires non-empty reused_inputs")
            return
        valid_hex = frozenset("0123456789abcdef")
        for index, reused_input in enumerate(reused_inputs):
            if not isinstance(reused_input, Mapping):
                errors.append(f"reused_inputs[{index}] must be a mapping")
                continue
            path = reused_input.get("path")
            if not isinstance(path, str) or not path or Path(path).is_absolute():
                errors.append(f"reused_inputs[{index}].path must be a non-empty repo-relative path")
            digest = reused_input.get("sha256")
            if (
                not isinstance(digest, str)
                or len(digest) != 71
                or not digest.startswith("sha256:")
                or any(character not in valid_hex for character in digest[7:])
            ):
                errors.append(f"reused_inputs[{index}].sha256 must match ^sha256:[0-9a-f]{{64}}$")

    def disabled() -> None:
        pointer = report.get("manifest_rule")
        value = resolve(pointer)
        if value is missing:
            errors.append("disabled_by_manifest manifest_rule must resolve in the manifest")
        elif not (value is False or value == "off"):
            errors.append("disabled_by_manifest manifest_rule must resolve to false or 'off'")

    def not_applicable() -> None:
        stage = report.get("stage")
        pointer = report.get("manifest_rule")
        kind = resolve(pointer)
        if pointer != "/kind":
            errors.append("not_applicable manifest_rule must be /kind")
            return
        stages = STAGES_BY_KIND.get(kind) if isinstance(kind, str) else None
        if stages is None:
            errors.append(f"not_applicable lane kind {kind!r} is not declared in STAGES_BY_KIND")
        elif stage in stages:
            errors.append(f"{stage} is applicable to lane kind {kind!r}")

    validators = {
        "executed_pass": executed,
        "executed_fail": executed,
        "reused_stored_result": reused,
        "disabled_by_manifest": disabled,
        "not_applicable": not_applicable,
    }
    validator = validators.get(report.get("outcome"))
    if validator is None:
        errors.append(f"dishonest stage outcome {report.get('outcome')!r}")
    else:
        validator()
    return errors


STAGES_BY_KIND: dict[str, tuple[str, ...]] = {
    "target_support": ("capture", "normalize", "replay", "compare", "claim"),
    "self_runtime": ("capture", "normalize", "replay", "compare", "claim"),
    "probe": ("capture", "claim"),
}


class CaptureAdapter(Protocol):
    def capture(
        self,
        lane_def: Mapping[str, Any],
        inventory_lane: Mapping[str, Any] | None = None,
        *,
        promote_accepted: bool = False,
        out_dir: Path | None = None,
    ) -> Mapping[str, Any]: ...


class Translator(Protocol):
    def translate(self, payload: Any, *, config: Mapping[str, Any] | None = None) -> Any: ...


class ReplayProvider(Protocol):
    # DEFERRED (Phase 20 scope: stored replay ONLY). No replay provider,
    # registry, or executed-replay path exists in the tree (verified at plan
    # time). This Protocol is committed as the reserved seam ONLY; nothing may
    # implement or dispatch to it this campaign. Introducing executed replay
    # requires a SPEC_AMENDMENTS.md entry with a concrete provider + tests.
    def replay(
        self,
        *,
        lane_def: Mapping[str, Any],
        capture_report: Mapping[str, Any],
        out_dir: Path,
    ) -> Mapping[str, Any]: ...


class Comparator(Protocol):
    # GROUNDED IN THE LIVE SYSTEM (do not redesign): the governed registry
    # `conformance/comparators/registry.json` maps to entrypoints
    # `conformance.comparators.<module>:compare`, whose contract is defined by
    # `conformance/comparators/protocol.py` (ComparatorInput): ONE mapping
    # containing decoded `capture`, `replay`, `scope`, and the artifact-path
    # map for every declared input_role; returns the comparator report mapping.
    # run_lane (WS-H H4) constructs that ComparatorInput exactly per
    # protocol.py, invokes the registry entrypoint, validates and writes the
    # returned report. Adapter wrappers exposing legacy `compare(*args,
    # **kwargs)` are wrapped by a keyword-forwarding shim in run_lane.
    def compare(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...
