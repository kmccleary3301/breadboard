"""Pinned Codex client-side BM25 discovery over its deferred agent corpus."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import yaml

# spec_plan.rs:761-778 registration order; each handler's search_info string.
SEARCH_TEXTS = (
    ("spawn_agent", "spawn_agent spawn agent subagent sub-agent delegate delegation parallel work worker explorer no-apps fork model reasoning"),
    ("send_input", "send_input send message existing agent subagent follow up interrupt redirect queue target"),
    ("resume_agent", "resume_agent resume reopen closed agent subagent thread id target"),
    ("wait_agent", "wait_agent wait agent subagent status final result complete timeout targets"),
    ("close_agent", "close_agent close shutdown stop agent subagent thread status target"),
)
DEFAULT_LIMIT = 8
ROOT = Path(__file__).resolve().parents[2]


def codex_search_binary() -> Path:
    source = Path(__file__).with_suffix("")
    digest = hashlib.sha256()
    for filename in ("Cargo.toml", "Cargo.lock", "main.rs"):
        digest.update((source / filename).read_bytes())
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "breadboard" / "codex-search" / digest.hexdigest() / "release" / "breadboard-codex-search"


class CodexSearchDecodeError(ValueError):
    """A router decoding failure, before the search handler is called."""


def search_deferred_tools(args: dict) -> list[dict]:
    # ToolSearchInfo::from_spec removes output_schema, sets defer_loading on
    # each function, and coalesces only the selected ranked namespace children.
    definition = yaml.safe_load((ROOT / "implementations/tools/defs_codex_139/11_multi_agent_v1.yaml").read_text())
    namespace = definition["provider_routing"]["openai"]["responses_tool_schema"]
    functions = {tool["name"]: tool for tool in namespace["tools"]}
    binary = codex_search_binary()
    if not binary.is_file():
        raise RuntimeError("Codex search helper is not built; run `python scripts/build_codex_search.py` from the repository root.")
    result = subprocess.run([str(binary)], input=json.dumps({
        "documents": [text for _, text in SEARCH_TEXTS],
        "arguments": args,
    }).encode(), capture_output=True, check=True)
    ranked = json.loads(result.stdout)
    if isinstance(ranked, dict):
        if "decode_error" in ranked:
            raise CodexSearchDecodeError(f"failed to parse tool_search arguments: {ranked['decode_error']}")
        raise ValueError(ranked["error"])
    if not ranked:
        return []
    output = {key: deepcopy(value) for key, value in namespace.items() if key != "tools"}
    output["tools"] = []
    for index in ranked:
        tool = deepcopy(functions[SEARCH_TEXTS[index][0]])
        tool.pop("output_schema", None)
        tool["defer_loading"] = True
        output["tools"].append(tool)
    return [output]


def execute_codex_search_tool(owner, args: dict) -> dict:
    if (owner.config.get("tools") or {}).get("native_semantics") != "codex_0_139":
        raise ValueError("Codex search requires the native Codex profile")
    error = None
    try:
        tools = search_deferred_tools(args)
    except ValueError as exc:
        tools, error = [], str(exc)
    # ToolSearchOutput / error output: core/src/tools/context.rs:166-180,
    # core/src/tools/parallel.rs:188-194. Converter adds the actual call_id.
    # Router decode errors reach the same wire shape: history normalization
    # inserts the search output and removes the orphan empty-id function output
    # (core/src/context_manager/normalize.rs:41-63,160-169).
    result = {"__mvi_text_output": json.dumps({
        "status": "completed", "execution": "client", "tools": tools,
    }, separators=(",", ":"), sort_keys=True)}
    if error is not None:
        result["error"] = error
    return result
