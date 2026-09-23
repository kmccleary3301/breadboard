from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKET_CASES_ROOT = Path("/tmp/pipkt/packet/cases")
PROMPT_TEMPLATE = REPO_ROOT / "config" / "e4_targets" / "pi" / "0.73.1" / "prompts" / "system-prompt.md"
NATIVE_CONFIG = REPO_ROOT / "config" / "e4_targets" / "pi" / "0.73.1" / "native-config.json"
SUPPLIER_CWD = "/capture/workspace"
SUPPLIER_DATE = "2026-09-23"
SUPPLIER_PACKAGE_DIR = "/opt/pi/app"
EXPECTED_PROMPT_SHA256 = "9da70352aa6a7f9920dc9d741dc9697d51bbc20850cd4f6751ff928d5d368025"
PACKET_CASES = tuple(sorted(path for path in PACKET_CASES_ROOT.iterdir() if path.is_dir()))


def _render_supplier_prompt(case_dir: Path) -> str:
    """Mirror pi_tools_0_73_1.mjs initialize slots and native context formatting.

    The worker loads context at lines 172-173, derives package/date inputs at
    lines 184-197, and removes each exact advertisement string at lines
    198-203.  The sealed prompt's six slots are rendered in that same order.
    Native ``buildSystemPrompt`` formats each context item as ``## path``, a
    blank line, content, and two trailing newlines (resource-loader output is
    the ``{path, content}`` pair consumed by lines 107-109 of the package
    implementation).
    """
    template = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    context_content = (case_dir / "workspace" / "AGENTS.md").read_text(encoding="utf-8")
    project_context = f"## {SUPPLIER_CWD}/AGENTS.md\n\n{context_content}\n\n"
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


def _supplier_first_system_prompt_without_advertisement(case_dir: Path) -> str:
    transcript = case_dir / "receiver" / "http-transcript.jsonl"
    first_request = json.loads(transcript.read_text(encoding="utf-8").splitlines()[0])["body"]
    system_messages = [message for message in first_request["messages"] if message.get("role") == "system"]
    assert len(system_messages) == 1
    prompt = system_messages[0]["content"]
    config = json.loads(NATIVE_CONFIG.read_text(encoding="utf-8"))
    for removal in config["advertisement"]["prompt"]["remove_exact"]:
        assert prompt.count(removal) == 1
        prompt = prompt.replace(removal, "", 1)
    return prompt


@pytest.mark.parametrize("case_dir", PACKET_CASES, ids=lambda path: path.name)
def test_sealed_prompt_matches_every_supplier_packet_case(case_dir: Path) -> None:
    assert PACKET_CASES, f"no supplier packet cases found under {PACKET_CASES_ROOT}"
    rendered = _render_supplier_prompt(case_dir)
    expected = _supplier_first_system_prompt_without_advertisement(case_dir)
    assert rendered == expected
    assert hashlib.sha256(rendered.encode("utf-8")).hexdigest() == EXPECTED_PROMPT_SHA256
