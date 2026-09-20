"""Typed Mini semantics functions and state container for Conductor integration.

Derived from pinned mini-swe-agent 2.4.6: DefaultAgent, LitellmModel, and action parser.
Consumed by Conductor; transport and tool execution remain outside this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import time
from typing import Any

from jinja2 import StrictUndefined, Template

from breadboard.rl.harness.runners.base import thaw_json


class InterruptAgentFlow(Exception):
    """Base exception for agent interruption and state exit."""

    def __init__(self, *messages: Mapping[str, Any] | dict[str, Any]) -> None:
        self.messages: list[dict[str, Any]] = [dict(m) for m in messages]
        super().__init__(self.messages)


class FormatError(InterruptAgentFlow):
    """Raised when the model output violates tool call schema or structure."""


class LimitsExceeded(InterruptAgentFlow):
    """Raised when the agent reaches or exceeds step or cost limits."""


class TimeExceeded(LimitsExceeded):
    """Raised when the agent reaches or exceeds wall-time limit."""


@dataclass(frozen=True)
class MiniAction:
    """Parsed action extracted from a validated bash tool call."""

    command: Any
    tool_call_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"command": self.command, "tool_call_id": self.tool_call_id}



@dataclass(frozen=True)
class MiniParseResult:
    """Result of whole-batch parsing of an SDK-dumped model completion response."""

    success: bool
    actions: tuple[MiniAction, ...]
    message: Mapping[str, Any]
    is_format_error: bool = False
    is_repeated_format_error: bool = False
    error_message: str = ""
    exit_message: Mapping[str, Any] | None = None


def recursive_merge(*dictionaries: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge mappings recursively preserving key order without sorting."""
    if not dictionaries:
        return {}
    result: dict[str, Any] = {}
    for d in dictionaries:
        if d is None:
            continue
        for key, value in d.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, Mapping):
                result[key] = recursive_merge(result[key], value)
            elif isinstance(value, Mapping):
                result[key] = recursive_merge(value)
            else:
                result[key] = value
    return result


def parse_toolcall_actions(
    tool_calls: Sequence[Mapping[str, Any]],
    *,
    format_error_template: str,
    template_kwargs: Mapping[str, Any] | None = None,
) -> list[MiniAction]:
    """Perform exact whole-batch parsing of tool calls.

    Raises FormatError if tool_calls is empty, arguments cannot be decoded from
    JSON, tool name is not 'bash', or 'command' key is missing from args.
    Any error on any call in the batch immediately raises FormatError before
    any action is returned or executed.
    """
    kwargs = dict(template_kwargs or {})
    if not tool_calls:
        err_msg = (
            "No tool calls found in the response. "
            "Every response MUST include at least one tool call."
        )
        content = Template(format_error_template, undefined=StrictUndefined).render(
            error=err_msg,
            actions=[],
            has_tool_calls=False,
            **kwargs,
        )
        raise FormatError(
            {
                "role": "user",
                "content": content,
                "extra": {"interrupt_type": "FormatError"},
            }
        )

    actions: list[MiniAction] = []
    for tc in tool_calls:
        error_msg = ""
        args: Any = {}
        tool_id = tc["id"]
        fn = tc["function"]
        tool_name = fn["name"]
        arguments = fn["arguments"]

        try:
            args = json.loads(arguments)
        except Exception as e:
            error_msg = f"Error parsing tool call arguments: {e}."

        if tool_name != "bash":
            error_msg += f"Unknown tool '{tool_name}'."

        if not isinstance(args, dict) or "command" not in args:
            error_msg += "Missing 'command' argument in bash tool call."

        if error_msg:
            content = Template(format_error_template, undefined=StrictUndefined).render(
                actions=[],
                error=error_msg.strip(),
                has_tool_calls=True,
                **kwargs,
            )
            raise FormatError(
                {
                    "role": "user",
                    "content": content,
                    "extra": {"interrupt_type": "FormatError"},
                }
            )

        actions.append(MiniAction(command=args["command"], tool_call_id=tool_id))

    return actions


def format_toolcall_observation_messages(
    *,
    actions: Sequence[MiniAction],
    outputs: Sequence[Mapping[str, Any]],
    observation_template: str,
    template_vars: Mapping[str, Any] | None = None,
    timestamp: float,
) -> list[dict[str, Any]]:
    """Format execution outputs into tool observation messages using native Jinja template."""
    not_executed = {"output": "", "returncode": -1, "exception_info": "action was not executed"}
    padded_outputs = list(outputs) + [not_executed] * max(0, len(actions) - len(outputs))
    tmpl = Template(observation_template, undefined=StrictUndefined)
    results: list[dict[str, Any]] = []

    for action, output in zip(actions, padded_outputs):
        content = tmpl.render(output=output, **(template_vars or {}))
        msg: dict[str, Any] = {
            "content": content,
            "extra": {
                "raw_output": output.get("output", ""),
                "returncode": output.get("returncode"),
                "timestamp": timestamp,
                "exception_info": output.get("exception_info"),
                **output.get("extra", {}),
            },
        }
        msg["role"] = "tool"
        msg["tool_call_id"] = action.tool_call_id
        results.append(msg)

    return results


def prepare_request_history(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Strip only source-owned extras at the model preparation phase."""
    return [{key: thaw_json(value) for key, value in message.items() if key != "extra"} for message in messages]


class MiniSemanticsState:
    """Pure source-semantics state container for Mini-SWE-agent."""

    def __init__(
        self,
        *,
        task: str,
        system_template: str,
        instance_template: str,
        observation_template: str,
        format_error_template: str,
        runtime_template_vars: Mapping[str, Any] | None = None,
        step_limit: int = 8,
        cost_limit: float = 0.0,
        wall_time_limit_seconds: int = 0,
        max_consecutive_format_errors: int = 3,
        start_time: float | None = None,
    ) -> None:
        self.task = task
        self.system_template = system_template
        self.instance_template = instance_template
        self.observation_template = observation_template
        self.format_error_template = format_error_template
        self.runtime_template_vars = dict(runtime_template_vars or {})
        self.step_limit = step_limit
        self.cost_limit = cost_limit
        self.wall_time_limit_seconds = wall_time_limit_seconds
        self.max_consecutive_format_errors = max_consecutive_format_errors
        self.start_time = start_time if start_time is not None else time.time()

        self.n_calls: int = 0
        self.cost: float = 0.0
        self.n_consecutive_format_errors: int = 0
        self.messages: list[dict[str, Any]] = []
        self.exit_status: str | None = None
        self.submission: str | None = None
        self.pending_actions: tuple[MiniAction, ...] = ()

        # Render and commit initial system and instance messages
        initial_vars = self.get_template_vars()
        sys_rendered = Template(self.system_template, undefined=StrictUndefined).render(**initial_vars)
        inst_rendered = Template(self.instance_template, undefined=StrictUndefined).render(**initial_vars)
        self.messages.append({"role": "system", "content": sys_rendered})
        self.messages.append({"role": "user", "content": inst_rendered})

    @property
    def is_exited(self) -> bool:
        return self.exit_status is not None

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Compute merged template variables with native DefaultAgent precedence."""
        agent_config = {
            "system_template": self.system_template,
            "instance_template": self.instance_template,
            "output_path": None,
            "step_limit": self.step_limit,
            "cost_limit": self.cost_limit,
            "wall_time_limit_seconds": self.wall_time_limit_seconds,
            "max_consecutive_format_errors": self.max_consecutive_format_errors,
        }
        counters = {
            "n_model_calls": self.n_calls,
            "model_cost": self.cost,
            "elapsed_seconds": int(time.time() - self.start_time),
        }
        extra = {"task": self.task}
        return recursive_merge(
            agent_config,
            self.runtime_template_vars,
            counters,
            extra,
            kwargs,
        )

    def prepare_request_history(self) -> list[dict[str, Any]]:
        """Return history for API query with 'extra' stripped and exits omitted."""
        return prepare_request_history(self.messages)

    def begin_query(self) -> int:
        """Query guard and increment.

        Checks step_limit, cost_limit, and wall_time_limit_seconds.
        If limit is reached, commits native exit entry and raises LimitsExceeded
        (or TimeExceeded). Does not increment query counter on limit stop.
        Otherwise increments and returns new n_calls.
        """
        if self.is_exited:
            exit_msg = self.messages[-1] if self.messages else {}
            raise LimitsExceeded(exit_msg)

        if (0 < self.step_limit <= self.n_calls) or (0 < self.cost_limit <= self.cost):
            exit_msg = {
                "role": "exit",
                "content": "LimitsExceeded",
                "extra": {"exit_status": "LimitsExceeded", "submission": ""},
            }
            self.messages.append(exit_msg)
            self.exit_status = "LimitsExceeded"
            self.submission = ""
            raise LimitsExceeded(exit_msg)

        elapsed = int(time.time() - self.start_time)
        if 0 < self.wall_time_limit_seconds <= elapsed:
            exit_msg = {
                "role": "exit",
                "content": "TimeExceeded",
                "extra": {"exit_status": "TimeExceeded", "submission": ""},
            }
            self.messages.append(exit_msg)
            self.exit_status = "TimeExceeded"
            self.submission = ""
            raise TimeExceeded(exit_msg)

        self.n_calls += 1
        return self.n_calls

    def parse_and_commit_response(
        self,
        response: Mapping[str, Any],
        *,
        cost: float,
        timestamp: float,
    ) -> MiniParseResult:
        """Parse whole-batch tool calls from exact SDK Chat completion JSON mapping.

        Preserves choices[0].message wholesale.
        """
        choices = response.get("choices")
        if not choices or not isinstance(choices, Sequence):
            raise ValueError("Response missing non-empty 'choices' sequence")

        choice0 = choices[0]
        finish_reason = choice0.get("finish_reason")
        raw_message = choice0.get("message")
        if not isinstance(raw_message, Mapping):
            raise ValueError("Response choices[0] missing 'message' mapping")

        tool_calls = list(raw_message.get("tool_calls") or [])

        try:
            parsed_actions = parse_toolcall_actions(
                tool_calls,
                format_error_template=self.format_error_template,
                template_kwargs={"finish_reason": finish_reason},
            )
        except FormatError as err:
            self.cost += cost
            self.n_consecutive_format_errors += 1

            feedback_msg = dict(err.messages[0])
            extra = dict(feedback_msg.get("extra", {}))
            extra["cost"] = cost
            extra["response"] = dict(response)
            feedback_msg["extra"] = extra
            self.messages.append(feedback_msg)

            is_repeated = 0 < self.max_consecutive_format_errors <= self.n_consecutive_format_errors
            exit_msg = None
            if is_repeated:
                exit_msg = {
                    "role": "exit",
                    "content": "RepeatedFormatError",
                    "extra": {
                        "exit_status": "RepeatedFormatError",
                        "submission": "",
                    },
                }
                self.messages.append(exit_msg)
                self.exit_status = "RepeatedFormatError"
                self.submission = ""

            self.pending_actions = ()
            return MiniParseResult(
                success=False,
                actions=(),
                message=feedback_msg,
                is_format_error=True,
                is_repeated_format_error=is_repeated,
                error_message=feedback_msg.get("content", ""),
                exit_message=exit_msg,
            )

        self.cost += cost
        assistant_msg = dict(raw_message)
        assistant_msg["extra"] = {
            "actions": [a.to_dict() for a in parsed_actions],
            "response": dict(response),
            "cost": cost,
            "timestamp": timestamp,
        }
        self.messages.append(assistant_msg)
        self.pending_actions = tuple(parsed_actions)
        return MiniParseResult(
            success=True,
            actions=self.pending_actions,
            message=assistant_msg,
            is_format_error=False,
            is_repeated_format_error=False,
        )

    def commit_whole_batch_observations(
        self,
        outputs: Sequence[Mapping[str, Any]],
        *,
        actions: Sequence[MiniAction] | None = None,
        timestamp: float,
    ) -> list[dict[str, Any]]:
        """Render and commit tool observation messages for the complete batch.

        Resets consecutive format error streak to 0 upon observation commit.
        """
        resolved_actions = actions if actions is not None else self.pending_actions
        tool_messages = format_toolcall_observation_messages(
            actions=resolved_actions,
            outputs=outputs,
            observation_template=self.observation_template,
            template_vars=self.get_template_vars(),
            timestamp=timestamp,
        )
        self.messages.extend(tool_messages)
        self.n_consecutive_format_errors = 0
        self.pending_actions = ()
        return tool_messages

    def commit_native_exit(
        self,
        exit_status: str,
        submission: str = "",
    ) -> dict[str, Any]:
        """Commit a native exit entry without committing observation messages.

        Content and extra follow DefaultAgent exact specifications.
        """
        content = submission if exit_status == "Submitted" else exit_status
        exit_message = {
            "role": "exit",
            "content": content,
            "extra": {
                "exit_status": exit_status,
                "submission": submission,
            },
        }
        self.messages.append(exit_message)
        self.exit_status = exit_status
        self.submission = submission
        self.pending_actions = ()
        return exit_message
