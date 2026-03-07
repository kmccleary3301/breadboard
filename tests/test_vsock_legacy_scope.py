from __future__ import annotations

import re
from pathlib import Path


def test_legacy_helper_usage_is_confined_to_firecracker_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    allowed = {
        Path("breadboard/vsock_protocol.py"),
        Path("breadboard/sandbox_firecracker.py"),
    }
    offenders: list[str] = []

    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == "tests":
            continue
        text = path.read_text(encoding="utf-8")
        if "build_legacy_repl_request" in text and rel not in allowed:
            offenders.append(rel.as_posix())

    assert offenders == []


def test_legacy_cmd_payload_scope_is_atp_confined() -> None:
    root = Path(__file__).resolve().parents[1]
    allowed = {
        Path("breadboard/vsock_protocol.py"),
        Path("breadboard/sandbox_firecracker.py"),
        Path("breadboard/lean_repl/firecracker_adapter.py"),
        Path("breadboard/lean_repl/firecracker_service.py"),
    }
    pattern = re.compile(r'\{\s*"cmd"\s*:')
    offenders: list[str] = []

    for top_level in ("breadboard", "breadboard_ext", "agentic_coder_prototype"):
        for path in (root / top_level).rglob("*.py"):
            rel = path.relative_to(root)
            text = path.read_text(encoding="utf-8")
            if pattern.search(text) and rel not in allowed:
                offenders.append(rel.as_posix())

    assert offenders == []
