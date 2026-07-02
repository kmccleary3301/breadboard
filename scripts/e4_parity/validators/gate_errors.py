from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence

GateClass = Literal["pin_stale", "semantic"]

_SHA_RE = re.compile(r"sha256:[0-9a-f]{64}")
_PATH_RE = re.compile(r"(?:^|\s)(?P<path>(?:docs|docs_tmp|artifacts|contracts|config|agentic_coder_prototype|scripts|tui_skeleton|sdk)/[^\s,;:]+)")
_EXPECTED_GOT_RE = re.compile(r"expected (?P<expected>sha256:[0-9a-f]{64}|[^,]+), got (?P<got>sha256:[0-9a-f]{64}|[^,]+)")
_NE_RE = re.compile(r"(?P<expected>sha256:[0-9a-f]{64}|[^\s]+)\s*!=\s*(?P<got>sha256:[0-9a-f]{64}|[^\s]+)")


@dataclass(frozen=True)
class BlameEntry:
    role_id: str
    path: str
    prev_sha256: str
    cur_sha256: str


@dataclass(frozen=True)
class GateError:
    code: str
    gate: str
    klass: GateClass
    subject: Mapping[str, str]
    expected: str | None
    got: str | None
    remedy: str
    blame: tuple[BlameEntry, ...] = field(default_factory=tuple)
    message: str = ""


def _subject_from_message(message: str) -> dict[str, str]:
    subject: dict[str, str] = {}
    label = message.split(":", 1)[0].strip()
    if label:
        subject["label"] = label
    path_match = _PATH_RE.search(message)
    if path_match:
        subject["path"] = path_match.group("path").rstrip("'")
    return subject


def _expected_got_from_message(message: str) -> tuple[str | None, str | None]:
    match = _EXPECTED_GOT_RE.search(message)
    if match:
        return match.group("expected").strip().rstrip("."), match.group("got").strip().rstrip(".")
    match = _NE_RE.search(message)
    if match:
        return match.group("expected").strip().rstrip("."), match.group("got").strip().rstrip(".")
    shas = _SHA_RE.findall(message)
    if "current" in message and len(shas) >= 2:
        return shas[0], shas[1]
    if len(shas) >= 2:
        return shas[0], shas[1]
    if "missing" in message:
        return "present", None
    return None, None


def classify_gate_message(message: str) -> GateClass:
    lowered = message.lower()
    if (
        "hash mismatch" in lowered
        or "sha256" in lowered and (" != " in lowered or "current" in lowered)
        or "missing sha256" in lowered
        or "must include current sha256" in lowered
        or "catalog_binding" in lowered and "hash" in lowered
    ):
        return "pin_stale"
    return "semantic"


def code_for_gate_message(message: str) -> str:
    lowered = message.lower()
    if "hash mismatch" in lowered or "sha256" in lowered and (" != " in lowered or "current" in lowered):
        return "artifact_hash_mismatch"
    if "missing sha256" in lowered or "must include current sha256" in lowered:
        return "missing_hash_pin"
    if "missing path" in lowered or "missing evidence file" in lowered or "does not exist" in lowered:
        return "artifact_missing"
    if "must be an object" in lowered or "must be a list" in lowered or "schema" in lowered:
        return "shape_invalid"
    if "duplicate" in lowered:
        return "duplicate_id"
    if "points" in lowered and ("sum" in lowered or "!=" in lowered or "match" in lowered):
        return "point_total_mismatch"
    if "not accepted" in lowered or "accepted" in lowered:
        return "acceptance_mismatch"
    if "missing" in lowered:
        return "required_value_missing"
    return "validation_failed"


def remedy_for_gate_message(message: str, klass: GateClass) -> str:
    if klass == "pin_stale":
        return "Regenerate the affected E4 evidence and rebind support claims with scripts/e4_parity/regenerate_evidence.py."
    lowered = message.lower()
    if "schema" in lowered or "must be" in lowered:
        return "Fix the malformed evidence or contract payload, then rerun the producing builder."
    if "missing" in lowered:
        return "Restore the missing required artifact or correct the reference to the intended evidence file."
    return "Fix the evidence contradiction at its source, then rerun the relevant gate."


def make_gate_error(gate: str, message: str, *, blame: Sequence[BlameEntry] = ()) -> GateError:
    klass = classify_gate_message(message)
    expected, got = _expected_got_from_message(message)
    return GateError(
        code=code_for_gate_message(message),
        gate=gate,
        klass=klass,
        subject=_subject_from_message(message),
        expected=expected,
        got=got,
        remedy=remedy_for_gate_message(message, klass),
        blame=tuple(blame),
        message=message,
    )


def gate_error_to_dict(error: GateError) -> dict[str, Any]:
    payload = asdict(error)
    payload["blame"] = [asdict(entry) for entry in error.blame]
    return payload


def render_gate_error(error: GateError) -> str:
    prefix = "[PIN_STALE]" if error.klass == "pin_stale" else "[SEMANTIC]"
    subject = ",".join(f"{key}={value}" for key, value in sorted(error.subject.items())) or "subject=unknown"
    expected_got = ""
    if error.expected is not None or error.got is not None:
        expected_got = f" expected={error.expected!r} got={error.got!r}"
    return f"{prefix} {error.code} {subject}{expected_got}; remedy={error.remedy}; message={error.message}"


def gate_errors_for(
    gate: str,
    errors: Iterable[str | GateError],
    *,
    blame: Sequence[BlameEntry] = (),
) -> list[GateError]:
    converted: list[GateError] = []
    for error in errors:
        if isinstance(error, GateError):
            converted.append(error)
        else:
            message = str(error)
            error_blame = blame if "catalog" in message.lower() and "hash" in message.lower() else ()
            converted.append(make_gate_error(gate, message, blame=error_blame))
    return converted


def apply_gate_error_envelope(report: dict[str, Any], gate: str, *, blame: Sequence[BlameEntry] = ()) -> dict[str, Any]:
    gate_errors = gate_errors_for(gate, report.get("errors", []), blame=blame)
    report["errors"] = [render_gate_error(error) for error in gate_errors]
    report["gate_errors"] = [gate_error_to_dict(error) for error in gate_errors]
    report["pin_stale_count"] = sum(1 for error in gate_errors if error.klass == "pin_stale")
    report["semantic_count"] = sum(1 for error in gate_errors if error.klass == "semantic")
    report["error_count"] = len(gate_errors)
    report["ok"] = not gate_errors
    return report


def gate_exit_code(report: Mapping[str, Any]) -> int:
    if report.get("ok"):
        return 0
    if report.get("semantic_count", 0):
        return 4
    return 3
