"""Named Pi prompt-footer deviations, confined to lane evidence.

Authority: pinned packages/coding-agent/src/core/system-prompt.ts:
057 dist/core/system-prompt.js:17-30,155-156 (custom footer 53-54);
073 dist/core/system-prompt.js:7-15,116-117 (custom footer 38-39).
Sidecars are independently observed lane data, never frozen native inputs.
Pi057 uses the actual launch timezone and en-US full timestamp. Pi073 uses
the local calendar formatter; native initializes in UTC and declares its date.
Projection requires named explicit admission by the caller. Raw mismatches
must remain in the lane report even when the returned bytes become equal.
No native runtime inputs or provider requests are modified by observation.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .request_bytes import decode_request_body

PINS = {
    "0.57.1": "a9cedccdde77e9d765303463d8a6cd11c58f7a7f",
    "0.73.1": "781152fc24841dc54b22284514604048ebe5e2c9",
}
SOURCE = "packages/coding-agent/src/core/system-prompt.ts#buildSystemPrompt"
_FORMAT = r"""
const [version, lower, upper] = process.argv.slice(1);
const dates = new Set();
const options = {
  weekday: 'long', year: 'numeric', month: 'long',
  day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  timeZoneName: 'short'
};
for (let ms = Math.floor(Number(lower)/1000)*1000; ms <= Number(upper); ms += 1000) {
  const now = new Date(ms);
  if (version === '0.57.1') dates.add(now.toLocaleString('en-US', options));
  else {
    dates.add(`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`);
  }
}
console.log(JSON.stringify([...dates]));
"""


def begin_pi_environment_observation(
    version: str, workdir: Path, *, native: bool,
) -> dict[str, Any]:
    """Observe real Node cwd and timezone before launching/building a prompt.

    Stock default process.cwd resolves Darwin /var aliases; native explicitly
    admits str(workdir). Native SDK initialize declares UTC locally (057 runtime
    initialize; 073 runtime initialize); do not change the launched environment.
    """
    if version not in PINS:
        raise ValueError("Unsupported Pi environment guard version")
    facts = json.loads(subprocess.check_output(
        ["node", "-e", "console.log(JSON.stringify({cwd:process.cwd(),timezone:Intl.DateTimeFormat().resolvedOptions().timeZone}))"],
        cwd=workdir, env=os.environ.copy(), text=True,
    ))
    return {
        "version": version, "source_commit": PINS[version], "source": SOURCE,
        "cwd": str(workdir) if native else facts["cwd"],
        "timezone": "UTC" if native else facts["timezone"],
        "inherited_TZ": os.environ.get("TZ"),
        "timezone_authority": "native SDK initialize TZ=UTC" if native else "launch Node Intl resolvedOptions",
        "launch_epoch_ms": time.time_ns() // 1_000_000,
    }


def finish_pi_environment_observation(observation: dict[str, Any]) -> None:
    """Close independently measured launch/build bounds, not body-derived time."""
    observation["prompt_build_upper_epoch_ms"] = time.time_ns() // 1_000_000


def _admitted_dates(observation: dict[str, Any]) -> set[str]:
    lower = observation["launch_epoch_ms"]
    upper = observation["prompt_build_upper_epoch_ms"]
    if (type(lower) is not int or type(upper) is not int or lower > upper
            or upper - lower > 3_600_000):
        raise ValueError("Invalid independently observed Pi prompt-build bounds")
    dates = set(json.loads(subprocess.check_output(
        ["node", "-e", _FORMAT, observation["version"], str(lower), str(upper)],
        text=True, env={**os.environ, "TZ": observation["timezone"]},
    )))
    declared = observation.get("declared_current_date")
    if declared is not None:
        if observation["version"] != "0.73.1" or declared not in dates:
            raise ValueError("Pi073 admitted current_date is outside observed UTC bounds")
        dates = {declared}
    return dates


def project_pi_environment(
    raw: bytes, observation: dict[str, Any], *, index: int, side: str,
) -> tuple[bytes, list[dict[str, Any]]]:
    """Project only unique first-system final-footer date/cwd string spans.

    Raises ValueError on ambiguous/malformed/unadmitted evidence. Return records
    retain request index/raw digest, pinned authority, environment facts, exact
    byte offsets and original/projected wire bytes (hex) and decoded values.
    """
    version = observation.get("version")
    if version not in PINS or observation.get("source_commit") != PINS[version] or observation.get("source") != SOURCE:
        raise ValueError("Pi environment observation is not pinned source evidence")
    body = decode_request_body(raw)
    if not isinstance(body, dict):
        raise ValueError("Pi request must be a JSON object")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
        raise ValueError("Missing Pi first system slot")
    first = messages[0]
    content = first.get("content")
    if first.get("role") != "system" or not isinstance(content, str):
        raise ValueError("Pi environment guard requires first system string")
    from breadboard.rl.harness.native_stream_profiles import PI_SUMMARIZATION_SYSTEM_PROMPT
    if content == PI_SUMMARIZATION_SYSTEM_PROMPT:
        return raw, []
    if not content.startswith("You are an expert coding assistant operating inside pi, a coding agent harness."):
        raise ValueError("Pi environment guard requires the pinned default prompt")
    date_label = "Current date and time: " if version == "0.57.1" else "Current date: "
    cwd_label = "Current working directory: "
    if content.count(date_label) != 1 or content.count(cwd_label) != 1:
        raise ValueError("Pi environment footer must be unique")
    lines = content.split("\n")
    if len(lines) < 3 or not lines[-2].startswith(date_label) or not lines[-1].startswith(cwd_label):
        raise ValueError("Pi environment fields are not final footer slots")
    date = lines[-2][len(date_label):]
    cwd = lines[-1][len(cwd_label):]
    expected_cwd = observation["cwd"]
    if version == "0.73.1":
        expected_cwd = expected_cwd.replace("\\", "/")
    if cwd != expected_cwd or date not in _admitted_dates(observation):
        raise ValueError("Pi footer date/cwd is not independently admitted")
    # Require an exact unambiguous UTF8 JSON string carrier. Do not reserialize
    # the full system content: alternate escaping elsewhere remains strict.
    carrier = json.dumps(content, ensure_ascii=False).encode("utf-8")
    if raw.count(carrier) != 1:
        raise ValueError("Pi first system UTF8 JSON string carrier is ambiguous")
    start = raw.index(carrier)
    edits = []
    for field, value, label in (("date", date, date_label), ("cwd", cwd, cwd_label)):
        original = json.dumps(label + value, ensure_ascii=False).encode("utf-8")[1:-1]
        replacement = json.dumps(label + "<pi-source-" + field + ">", ensure_ascii=False).encode("utf-8")[1:-1]
        if carrier.count(original) != 1:
            raise ValueError("Pi footer wire span is ambiguous")
        offset = start + carrier.index(original) + len(label.encode("utf-8"))
        wire_value = original[len(label.encode("utf-8")):]
        projected = replacement[len(label.encode("utf-8")):]
        edits.append((offset, offset + len(wire_value), projected, {
            "name": ("Pi057PromptFooter" if version == "0.57.1" else "Pi073PromptFooter") + field.title(),
            "field": field, "index": index, "side": side,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "source_commit": PINS[version], "source": SOURCE,
            "formatter_citation": "dist/core/system-prompt.js:17-30,155-156" if version == "0.57.1" else "dist/core/system-prompt.js:7-15,116-117",
            "path": "$/messages/0/content", "environment": dict(observation),
            "start": offset, "end": offset + len(wire_value),
            "original": value, "projected": "<pi-source-" + field + ">",
            "original_bytes": wire_value.hex(), "projected_bytes": projected.hex(),
        }))
    projected_raw = raw
    for a, b, replacement, _ in sorted(edits, reverse=True):
        projected_raw = projected_raw[:a] + replacement + projected_raw[b:]
    return projected_raw, [edit[3] for edit in edits]
