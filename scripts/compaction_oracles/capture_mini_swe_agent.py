#!/usr/bin/env python3
"""
capture_mini_swe_agent.py

Takes the source checkout path as a command-line argument:
    python scripts/compaction_oracles/capture_mini_swe_agent.py <source_checkout_path>

Executes and derives oracle cases for mini-swe-agent 2.4.6:
1. Executed: Observation character clipping rendered directly from the pinned
   config template in `<source_checkout_path>/src/minisweagent/config/mini.yaml`
   - Output < 10,000 chars: rendered unclipped
   - Output == 10,000 chars: long branch with elided_chars = 0
   - Output > 10,000 chars: clipped with 5,000 char head and 5,000 char tail
2. Source-derived: Proactive triggers (trigger.fires = False) and context overflow
   terminal handling, cited with exact file:line and verbatim source quotes.
"""

import json
import sys
from pathlib import Path
import yaml
from jinja2 import Template

if len(sys.argv) < 2:
    print("Usage: python capture_mini_swe_agent.py <source_checkout_path>", file=sys.stderr)
    sys.exit(1)

source_checkout = Path(sys.argv[1]).resolve()
if not source_checkout.exists():
    print(f"Error: source checkout path does not exist: {source_checkout}", file=sys.stderr)
    sys.exit(1)

MINI_COMMIT = "a83fcae82d2a08f0ee0c688f9d137b3566c097f8"
MINI_REPO = "https://github.com/SWE-agent/mini-swe-agent"
PRESET_ID = "mini_swe_agent@2.4.6"

TARGET_DIR = Path(__file__).resolve().parent.parent.parent / "tests/compaction/oracles" / PRESET_ID
TARGET_DIR.mkdir(parents=True, exist_ok=True)

# Load template directly from the source checkout
mini_yaml_path = source_checkout / "src/minisweagent/config/mini.yaml"
if not mini_yaml_path.exists():
    print(f"Error: {mini_yaml_path} does not exist", file=sys.stderr)
    sys.exit(1)

with open(mini_yaml_path, "r", encoding="utf-8") as f:
    config_data = yaml.safe_load(f)

observation_template_str = config_data["model"]["observation_template"]
template = Template(observation_template_str)

def render_observation(output_str: str, returncode: int = 0, exception_info: str | None = None) -> str:
    output_obj = {
        "output": output_str,
        "returncode": returncode,
        "exception_info": exception_info,
    }
    return template.render(output=output_obj)

cases = []

def emit_case(case_name: str, payload: dict):
    if payload["capture"]["kind"] == "executed":
        payload["input"].update({
            "component": "reduction",
            "stage": "observation",
            "reduction_input": {"selection": {"targets": [
                index for index, message in enumerate(payload["input"]["messages"])
                if message["role"] == "tool"
            ]}},
        })
    file_path = TARGET_DIR / f"{case_name}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    cases.append(case_name)
    kind = payload["capture"]["kind"]
    print(f"Wrote {kind} case: {case_name}")

# Case 1: Source-derived: No proactive trigger at zero occupancy
emit_case("trigger_zero_occupancy", {
    "schema": "bb.compaction_oracle_case.v1",
    "preset": PRESET_ID,
    "case": "trigger_zero_occupancy",
    "source": {
        "repo": MINI_REPO,
        "commit": MINI_COMMIT,
        "evidence": [
            "src/minisweagent/agents/default.py:130-151",
            "src/minisweagent/agents/default.py:69-72",
        ],
    },
    "capture": {
        "kind": "source_derived",
        "script": "scripts/compaction_oracles/capture_mini_swe_agent.py",
        "notes": (
            "Source-derived from src/minisweagent/agents/default.py:130-151.\n"
            "Verbatim query() loop checks:\n"
            "  if 0 < self.config.step_limit <= self.n_calls or 0 < self.config.cost_limit <= self.cost:\n"
            "      raise LimitsExceeded(...)\n"
            "  if 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time):\n"
            "      raise TimeExceeded(...)\n"
            "No context token, usage, or occupancy threshold exists in DefaultAgent; history is purely append-only (line 71: `self.messages.extend(messages)`). "
            "Therefore trigger.fires = False."
        ),
    },
    "input": {
        "messages": [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "Fix bug in repo."},
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 150,
        },
        "context_window": 128000,
        "max_input_tokens": None,
        "max_output_tokens": 2048,
        "reason": "threshold",
        "native_settings": {},
    },
    "expect": {
        "trigger": {
            "fires": False,
            "tokens": 150,
            "limit": 128000,
            "severity": "soft",
        },
    },
})

# Case 2: Source-derived: No proactive trigger at high occupancy (127,000 / 128,000 tokens)
emit_case("trigger_high_occupancy", {
    "schema": "bb.compaction_oracle_case.v1",
    "preset": PRESET_ID,
    "case": "trigger_high_occupancy",
    "source": {
        "repo": MINI_REPO,
        "commit": MINI_COMMIT,
        "evidence": [
            "src/minisweagent/agents/default.py:130-151",
            "src/minisweagent/agents/default.py:69-72",
        ],
    },
    "capture": {
        "kind": "source_derived",
        "script": "scripts/compaction_oracles/capture_mini_swe_agent.py",
        "notes": (
            "Source-derived from src/minisweagent/agents/default.py:130-151.\n"
            "Even at 127,000 / 128,000 tokens, DefaultAgent has no proactive compaction mechanism or /compact handler.\n"
            "History remains strictly append-only, and trigger.fires = False at any occupancy."
        ),
    },
    "input": {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "High occupancy prompt"},
            {"role": "assistant", "content": "Working..."},
        ],
        "usage": {
            "input_tokens": 126000,
            "output_tokens": 1000,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 127000,
        },
        "context_window": 128000,
        "max_input_tokens": None,
        "max_output_tokens": 2048,
        "reason": "threshold",
        "native_settings": {},
    },
    "expect": {
        "trigger": {
            "fires": False,
            "tokens": 127000,
            "limit": 128000,
            "severity": "soft",
        },
    },
})

# Case 3: Source-derived: Terminal overflow failure
emit_case("overflow_terminal_failure", {
    "schema": "bb.compaction_oracle_case.v1",
    "preset": PRESET_ID,
    "case": "overflow_terminal_failure",
    "source": {
        "repo": MINI_REPO,
        "commit": MINI_COMMIT,
        "evidence": [
            "src/minisweagent/models/litellm_model.py:50-57",
            "src/minisweagent/models/utils/retry.py:19-24",
            "src/minisweagent/agents/default.py:74-84",
        ],
    },
    "capture": {
        "kind": "source_derived",
        "script": "scripts/compaction_oracles/capture_mini_swe_agent.py",
        "notes": (
            "Source-derived from LiteLLMModel and DefaultAgent exception path:\n"
            "1. src/minisweagent/models/litellm_model.py:50-57 lists `litellm.exceptions.ContextWindowExceededError` in `abort_exceptions`.\n"
            "2. src/minisweagent/models/utils/retry.py:19-24 excludes abort exceptions from retry.\n"
            "3. src/minisweagent/agents/default.py:74-84 formats terminal exit: `extra: {'exit_status': type(e).__name__}`.\n"
            "Execution aborts immediately with failure kind ContextWindowExceededError without retry or compaction."
        ),
    },
    "input": {
        "messages": [
            {"role": "user", "content": "Massive context exceeding window"},
        ],
        "usage": None,
        "context_window": 128000,
        "max_input_tokens": None,
        "max_output_tokens": 2048,
        "reason": "overflow",
        "native_settings": {},
    },
    "expect": {
        "failure": {
            "kind": "ContextWindowExceededError",
            "message": "litellm.exceptions.ContextWindowExceededError: Context window exceeded",
        },
    },
})

# Case 4: Executed: Observation under 10,000 characters (unclipped)
short_output = "Line 1: build succeeded\nLine 2: tests passed (42 tests, 0 failures)"
rendered_short = render_observation(short_output, returncode=0)
emit_case("observation_under_10k_unclipped", {
    "schema": "bb.compaction_oracle_case.v1",
    "preset": PRESET_ID,
    "case": "observation_under_10k_unclipped",
    "source": {
        "repo": MINI_REPO,
        "commit": MINI_COMMIT,
        "evidence": [
            "src/minisweagent/config/mini.yaml:112-128",
            "src/minisweagent/models/utils/actions_toolcall.py:95-108",
        ],
    },
    "capture": {
        "kind": "executed",
        "script": "scripts/compaction_oracles/capture_mini_swe_agent.py",
        "notes": (
            "Executed observation_template loaded directly from <source_checkout>/src/minisweagent/config/mini.yaml:112-128 via Jinja2.\n"
            "Verbatim threshold in template (line 113): `{%- if output.output | length < 10000 -%}`.\n"
            f"Output has length {len(short_output)} (< 10000), rendering full output unclipped in JSON envelope."
        ),
    },
    "input": {
        "messages": [
            {"role": "user", "content": "Run tests"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "bash", "arguments": '{"command":"pytest"}'}}],
            },
            {
                "role": "tool",
                "tool_call_id": "t1",
                "content": rendered_short,
            },
        ],
        "usage": None,
        "context_window": 128000,
        "max_input_tokens": None,
        "max_output_tokens": 2048,
        "reason": "manual",
        "native_settings": {},
    },
    "expect": {
        "projected_view": [
            {"role": "user", "content": "Run tests"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "bash", "arguments": '{"command":"pytest"}'}}],
            },
            {
                "role": "tool",
                "tool_call_id": "t1",
                "content": rendered_short,
            },
        ],
    },
})

# Case 5: Executed: Observation at exactly 10,000 characters (boundary test)
exact_10k_output = "a" * 10000
rendered_10k = render_observation(exact_10k_output, returncode=0)
emit_case("observation_at_10k_elided_zero", {
    "schema": "bb.compaction_oracle_case.v1",
    "preset": PRESET_ID,
    "case": "observation_at_10k_elided_zero",
    "source": {
        "repo": MINI_REPO,
        "commit": MINI_COMMIT,
        "evidence": [
            "src/minisweagent/config/mini.yaml:113,119-128",
        ],
    },
    "capture": {
        "kind": "executed",
        "script": "scripts/compaction_oracles/capture_mini_swe_agent.py",
        "notes": (
            "Executed observation_template loaded from <source_checkout>/src/minisweagent/config/mini.yaml:113,119-128.\n"
            "Verbatim boundary logic: `{%- if output.output | length < 10000 -%}` false at exactly 10,000 chars.\n"
            "Executes lines 122-125:\n"
            "  \"output_head\": {{ output.output[:5000] | tojson }},\n"
            "  \"output_tail\": {{ output.output[-5000:] | tojson }},\n"
            "  \"elided_chars\": {{ output.output | length - 10000 }},\n"
            "  \"warning\": \"Output too long.\"\n"
            "Produces output_head (5,000), output_tail (5,000), elided_chars = 0, warning = 'Output too long.'"
        ),
    },
    "input": {
        "messages": [
            {
                "role": "tool",
                "tool_call_id": "t1",
                "content": json.dumps({"returncode": 0, "output": exact_10k_output, "exception_info": None}),
            },
        ],
        "usage": None,
        "context_window": 128000,
        "max_input_tokens": None,
        "max_output_tokens": 2048,
        "reason": "manual",
        "native_settings": {},
    },
    "expect": {
        "edits": [
            {
                "index": 0,
                "message": {
                    "role": "tool",
                    "tool_call_id": "t1",
                    "content": rendered_10k,
                },
            },
        ],
    },
})

# Case 6: Executed: Observation over 10,000 characters (15,000 chars clipped)
head_part = "H" * 5000
middle_part = "M" * 5000
tail_part = "T" * 5000
long_output = head_part + middle_part + tail_part # 15,000
rendered_long = render_observation(long_output, returncode=0)

emit_case("observation_over_10k_clipped", {
    "schema": "bb.compaction_oracle_case.v1",
    "preset": PRESET_ID,
    "case": "observation_over_10k_clipped",
    "source": {
        "repo": MINI_REPO,
        "commit": MINI_COMMIT,
        "evidence": [
            "src/minisweagent/config/mini.yaml:119-128",
            "src/minisweagent/models/utils/actions_toolcall.py:95-108",
        ],
    },
    "capture": {
        "kind": "executed",
        "script": "scripts/compaction_oracles/capture_mini_swe_agent.py",
        "notes": (
            "Executed observation_template from <source_checkout>/src/minisweagent/config/mini.yaml:119-128.\n"
            "At 15,000 characters, renders output_head (5,000 'H'), output_tail (5,000 'T'), elided_chars = 5000, warning = 'Output too long.'"
        ),
    },
    "input": {
        "messages": [
            {
                "role": "tool",
                "tool_call_id": "t1",
                "content": json.dumps({"returncode": 0, "output": long_output, "exception_info": None}),
            },
        ],
        "usage": None,
        "context_window": 128000,
        "max_input_tokens": None,
        "max_output_tokens": 2048,
        "reason": "manual",
        "native_settings": {},
    },
    "expect": {
        "edits": [
            {
                "index": 0,
                "message": {
                    "role": "tool",
                    "tool_call_id": "t1",
                    "content": rendered_long,
                },
            },
        ],
    },
})

print(f"mini-swe-agent case generation complete: {len(cases)} cases.")
