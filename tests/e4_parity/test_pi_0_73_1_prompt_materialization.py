from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

import pytest

from conformance.comparators.pi_coding_agent_0_73_1 import PiCodingAgent0731Comparator
from tests.rl.harness.test_pi_native_stream_conductor import _NativeWorkerPort


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).with_name("fixtures") / "pi_0_73_1_supplier_prompts.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
PROMPT_TEMPLATE = REPO_ROOT / "config" / "e4_targets" / "pi" / "0.73.1" / "prompts" / "system-prompt.md"
NATIVE_CONFIG = REPO_ROOT / "config" / "e4_targets" / "pi" / "0.73.1" / "native-config.json"
SUPPLIER_CWD = "/capture/workspace"
SUPPLIER_DATE = "2026-09-23"
SUPPLIER_PACKAGE_DIR = "/opt/pi/app"
EXPECTED_PROMPT_SHA256 = "9da70352aa6a7f9920dc9d741dc9697d51bbc20850cd4f6751ff928d5d368025"
PROMPT_CASES: tuple[Mapping[str, Any], ...] = tuple(FIXTURE["cases"])
_NODE_MODULES = Path(os.environ.get("PI_CODING_AGENT_NODE_MODULES", "/tmp/pi-node-0731/node_modules"))
_PINNED_PI_ENTRYPOINT = _NODE_MODULES / "@mariozechner" / "pi-coding-agent" / "dist" / "index.js"
if os.environ.get("BB_REQUIRE_PINNED_PI_NODE") == "1" and not _PINNED_PI_ENTRYPOINT.is_file():
    pytest.fail(
        f"required pinned Pi 0.73.1 node_modules root is unavailable: {_NODE_MODULES}",
    )


def _render_supplier_prompt(case: Mapping[str, Any]) -> str:
    """Mirror pi_tools_0_73_1.mjs initialize slots and native context formatting.

    The worker loads context at lines 172-173, derives package/date inputs at
    lines 184-197, and removes each exact advertisement string at lines
    198-203. The sealed prompt's six slots are rendered in that same order.
    Native ``buildSystemPrompt`` formats each context item as ``## path``, a
    blank line, content, and two trailing newlines (resource-loader output is
    the ``{path, content}`` pair consumed by lines 107-109 of the package
    implementation).
    """
    template = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    project_context = f"## {SUPPLIER_CWD}/AGENTS.md\n\n{case['agents_md']}\n\n"
    replacements = {
        "{{readme_path}}": f"{SUPPLIER_PACKAGE_DIR}/README.md",
        "{{docs_path}}": f"{SUPPLIER_PACKAGE_DIR}/docs",
        "{{examples_path}}": f"{SUPPLIER_PACKAGE_DIR}/examples",
        "{{project_context}}": project_context,
        "{{current_date}}": SUPPLIER_DATE,
        "{{cwd}}": SUPPLIER_CWD,
    }
    for slot, value in replacements.items():
        assert template.count(slot) == 1, f"prompt slot {slot!r} must occur exactly once"
        template = template.replace(slot, value)
    assert "{{" not in template
    return template


def _supplier_first_system_prompt_without_advertisement(case: Mapping[str, Any]) -> str:
    prompt = case["first_request_system_content"]
    config = json.loads(NATIVE_CONFIG.read_text(encoding="utf-8"))
    for removal in config["advertisement"]["prompt"]["remove_exact"]:
        assert prompt.count(removal) == 1
        prompt = prompt.replace(removal, "", 1)
    return prompt


@pytest.mark.parametrize("case", PROMPT_CASES, ids=lambda case: case["case"])
def test_sealed_prompt_matches_every_supplier_packet_case(case: Mapping[str, Any]) -> None:
    rendered = _render_supplier_prompt(case)
    expected = _supplier_first_system_prompt_without_advertisement(case)
    assert rendered == expected
    assert hashlib.sha256(rendered.encode("utf-8")).hexdigest() == EXPECTED_PROMPT_SHA256


async def _initialize_worker(
    workspace: Path,
    advertisement: Mapping[str, Any],
    *,
    current_date: str | None = None,
) -> Mapping[str, Any]:
    port = _NativeWorkerPort(workspace, ())
    payload: dict[str, Any] = {
        "task": "prompt materialization parity",
        "package_dir": SUPPLIER_PACKAGE_DIR,
        "advertisement": deepcopy(dict(advertisement)),
        "model_config": {"id": "model-a", "provider": "openai", "input": ["text"]},
    }
    if current_date is not None:
        payload["runtime_inputs"] = {
            "cwd": str(port.workspace),
            "home": str(port.scratch / "home"),
            "current_date": current_date,
            "package_dir": SUPPLIER_PACKAGE_DIR,
        }
    try:
        return await port.invoke_native_phase("initialize", payload, timeout_ms=10_000)
    finally:
        await port.close()


@pytest.mark.skipif(
    not (_NODE_MODULES / "@mariozechner" / "pi-coding-agent" / "dist" / "index.js").is_file(),
    reason="pinned Pi 0.73.1 node_modules root is unavailable",
)
@pytest.mark.asyncio
async def test_real_worker_prompt_matches_supplier_through_comparator(tmp_path: Path) -> None:
    case = next(case for case in PROMPT_CASES if case["case"] == "normal_workspace_episode")
    config = json.loads(NATIVE_CONFIG.read_text(encoding="utf-8"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(case["agents_md"], encoding="utf-8")
    initialized = await _initialize_worker(workspace, config["advertisement"])
    bootstrap = initialized["bootstrap"]
    assert f"\nCurrent date: {bootstrap['current_date']}\n" in initialized["system_prompt"]

    supplier_tools = deepcopy(initialized["tool_schemas"])
    native_read_description = FIXTURE["native_read_description"]
    for tool in supplier_tools:
        if tool["function"]["name"] == "read":
            tool["function"]["description"] = native_read_description
    supplier = {
        "role": "supplier",
        "messages": [{"role": "user", "content": "prompt materialization parity"}],
        "requests": [{"messages": [{"role": "system", "content": case["first_request_system_content"]}], "tools": supplier_tools}],
        "effects": {},
        "termination": {"kind": "submitted", "native_stop_reason": "stop"},
        "request_count": 1,
    }
    replay = {
        "role": "replay",
        "messages": [{"role": "user", "content": "prompt materialization parity"}],
        "requests": [{"messages": [{"role": "system", "content": initialized["system_prompt"]}], "tools": initialized["tool_schemas"]}],
        "runtime_inputs": {
            "cwd": str(workspace),
            "home": bootstrap["home"],
            "current_date": bootstrap["current_date"],
            "package_dir": SUPPLIER_PACKAGE_DIR,
        },
        "effects": {},
        "termination": {"kind": "submitted", "native_stop_reason": "stop"},
        "request_count": 1,
    }
    report = PiCodingAgent0731Comparator()({"capture": supplier, "replay": replay})
    assert report["passed"], report["assertions"][0]["detail"]


@pytest.mark.skipif(
    not (_NODE_MODULES / "@mariozechner" / "pi-coding-agent" / "dist" / "index.js").is_file(),
    reason="pinned Pi 0.73.1 node_modules root is unavailable",
)
@pytest.mark.asyncio
async def test_real_worker_rejects_prompt_date_that_differs_from_declared_input(tmp_path: Path) -> None:
    config = json.loads(NATIVE_CONFIG.read_text(encoding="utf-8"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(RuntimeError, match="current date does not match declared"):
        await _initialize_worker(workspace, config["advertisement"], current_date="1999-01-01")
