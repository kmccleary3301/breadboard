"""Mini-SWE-agent native shell action helper.

Source mapping:
- minisweagent/environments/local.py (commit a83fcae82d2a08f0ee0c688f9d137b3566c097f8)
- minisweagent/exceptions.py: Submitted
- minisweagent/config/mini.yaml: environment overrides PAGER=cat, MANPAGER=cat,
  LESS=-R, PIP_PROGRESS_BAR=off, TQDM_DISABLE=1; default timeout 30s.

Protocol:
- Stdin:  {"tool_id":"bash","arguments":{"command":<original JSON value>}} (max 4 MiB)
- Stdout: exactly one JSON tool result
- Stderr: diagnostics on outer protocol / resource / cancellation failures

Semantics:
- Single native Mini shell action helper, not an agent loop.
- Default non-login shell, POSIX session (start_new_session=True). POSIX-only.
- Ordered command-value semantics: passes command directly to Popen(shell=True),
  preserving sequences, mappings (iterated in insertion order), empty string/sequence,
  and catching non-string/non-iterable TypeErrors into returncode -1.
- Inherited admitted environment plus exact five Mini overrides.
- Merged stdout/stderr decoded as UTF-8 with replacement and universal newlines.
- 30s timeout kills process group and drains output, raising native TimeoutExpired rendering.
- Bounded raw output (1 MiB) with explicit outer error (RawOutputLimitExceeded),
  never fabricated successful truncation.
- Cancellation (SIGINT/SIGTERM/BaseException) retires shell/session children, reaps child
  without swallowing wait failure, and propagates before helper exit.
- Submission semantics: COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT on first line with returncode == 0
  returns additional "exit" field alongside actual output fields.
"""

from __future__ import annotations

import base64
import json
import os
import select
import signal
import subprocess
import sys
import time
from types import MappingProxyType
from typing import Any, Mapping

if os.name != "posix":
    raise RuntimeError("mini_tools requires a POSIX runtime environment")

MINI_SWE_AGENT_LOCAL_ADAPTER_ID: str = "mini-swe-agent.local.v2.4.6"
MINI_TOOL_ID: str = "bash"

DEFAULT_COMMAND_TIMEOUT: int = 30
DEFAULT_RAW_OUTPUT_LIMIT: int = 1024 * 1024  # 1 MiB
MAX_REQUEST_BYTES: int = 4 * 1024 * 1024     # 4 MiB

MINI_ENVIRONMENT_OVERRIDES: MappingProxyType[str, str] = MappingProxyType({
    "PAGER": "cat",
    "MANPAGER": "cat",
    "LESS": "-R",
    "PIP_PROGRESS_BAR": "off",
    "TQDM_DISABLE": "1",
})

SUBMISSION_MARKER: str = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


class HelperCancelled(BaseException):
    """Raised on SIGINT or SIGTERM cancellation to retire children and terminate."""


class NativeToolCleanupError(BaseException):
    """Owned process retirement failed; this is not a native execution result."""


class RawOutputLimitExceeded(RuntimeError):
    """Explicit outer error raised when raw subprocess output exceeds the 1 MiB bound."""

    def __init__(self, message: str, *, raw_prefix: bytes, total_bytes: int) -> None:
        super().__init__(message)
        self.raw_prefix = raw_prefix
        self.total_bytes = total_bytes


def _kill_group(pid: int) -> None:
    """Kill process group with SIGKILL. Only ProcessLookupError is ignored for absent groups."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _decode_universal_newlines(data: bytes) -> str:
    """Decode UTF-8 with replacement and apply universal newline translation (native text=True)."""
    text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _run_mini_subprocess(
    command: Any,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    raw_output_limit: int,
) -> subprocess.CompletedProcess[str]:
    """Bound the source communicate() behavior without dropping descendant output."""
    process = subprocess.Popen(
        command, shell=True, cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    chunks: list[bytes] = []
    total_bytes = 0
    timed_out = False

    def retire() -> None:
        try:
            _kill_group(process.pid)
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeToolCleanupError(f"Mini process group did not retire: {exc}") from exc

    assert process.stdout is not None
    try:
        stdout_fd = process.stdout.fileno()
        os.set_blocking(stdout_fd, False)
        poller = select.poll()
        poller.register(stdout_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
        deadline = time.monotonic() + timeout
        eof = False
        while not (eof and process.poll() is not None):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if timed_out:
                    raise NativeToolCleanupError("Mini output pipe did not close after group termination")
                timed_out = True
                retire()
                deadline = time.monotonic() + 1
                remaining = 1
            # Both EOF and process exit are required: a shell may exit while its
            # child still owns stdout, or close stdout before finishing itself.
            events = poller.poll(min(50, max(1, int(remaining * 1000))))
            if events and not eof:
                try:
                    chunk = os.read(stdout_fd, 65536)
                except (BlockingIOError, InterruptedError):
                    continue
                if not chunk:
                    eof = True
                    poller.unregister(stdout_fd)
                    continue
                total_bytes += len(chunk)
                if total_bytes > raw_output_limit:
                    prefix = (b"".join(chunks) + chunk)[:raw_output_limit]
                    raise RawOutputLimitExceeded(
                        f"raw output exceeded limit of {raw_output_limit} bytes",
                        raw_prefix=prefix, total_bytes=total_bytes,
                    )
                chunks.append(chunk)
        text = _decode_universal_newlines(b"".join(chunks))
        if timed_out:
            raise subprocess.TimeoutExpired(command, timeout, output=text)
        return subprocess.CompletedProcess(command, process.returncode, stdout=text)
    except subprocess.TimeoutExpired:
        raise
    except BaseException:
        retire()
        raise
    finally:
        process.stdout.close()


def check_finished(output: dict[str, Any]) -> dict[str, Any] | None:
    """Return native exit mapping if output indicates task completion, else None.

    Source-faithful to minisweagent.environments.local.LocalEnvironment._check_finished.
    """
    lines = output.get("output", "").lstrip().splitlines(keepends=True)
    if lines and lines[0].strip() == SUBMISSION_MARKER and output.get("returncode") == 0:
        submission = "".join(lines[1:])
        return {
            "role": "exit",
            "content": submission,
            "extra": {"exit_status": "Submitted", "submission": submission},
        }
    return None


def execute_mini_shell_action(
    action: Mapping[str, Any] | Any,
    cwd: str = "",
    *,
    timeout: int | None = None,
    raw_output_limit: int | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute a single native Mini shell action and return the result dictionary.

    Returns keys:
    - 'output': str (merged stdout/stderr text, UTF-8 replacement, universal newlines)
    - 'returncode': int
    - 'exception_info': str
    - 'extra': dict (present only if an execution exception occurred)
    - 'exit': dict (present only if task was Submitted)

    Raises RawOutputLimitExceeded if output exceeds raw_output_limit (default 1 MiB).
    Cancellation (BaseException) is never swallowed as an ordinary execution error.
    """
    if isinstance(action, Mapping):
        command = action.get("command", "")
    else:
        command = action

    effective_cwd = cwd or os.getcwd()
    effective_timeout = timeout if timeout is not None else DEFAULT_COMMAND_TIMEOUT
    effective_limit = raw_output_limit if raw_output_limit is not None else DEFAULT_RAW_OUTPUT_LIMIT

    base_env = dict(env) if env is not None else dict(os.environ)
    merged_env = base_env | dict(MINI_ENVIRONMENT_OVERRIDES)

    try:
        result = _run_mini_subprocess(
            command,
            effective_cwd,
            merged_env,
            effective_timeout,
            effective_limit,
        )
        output: dict[str, Any] = {
            "output": result.stdout,
            "returncode": result.returncode,
            "exception_info": "",
        }
    except (RawOutputLimitExceeded, HelperCancelled, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raw_output = getattr(exc, "output", None)
        raw_output_str = (
            _decode_universal_newlines(raw_output)
            if isinstance(raw_output, bytes)
            else (raw_output or "")
        )
        output = {
            "output": raw_output_str,
            "returncode": -1,
            "exception_info": f"An error occurred while executing the command: {exc}",
            "extra": {"exception_type": type(exc).__name__, "exception": str(exc)},
        }

    exit_payload = check_finished(output)
    if exit_payload is not None:
        output["exit"] = exit_payload

    return output


def main() -> int:
    """CLI entrypoint for InstalledToolAdapter protocol.

    Reads a 4 MiB envelope containing tool_id, arguments, and the owned source environment.
    Writes stdout JSON: one result object.
    Traps SIGINT and SIGTERM, raising HelperCancelled to kill child group and reap before exit.
    """
    def _sig_handler(signum: int, frame: Any) -> None:
        raise HelperCancelled(f"helper cancelled by signal {signum}")

    old_sigint = signal.signal(signal.SIGINT, _sig_handler)
    old_sigterm = signal.signal(signal.SIGTERM, _sig_handler)

    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = sys.stdin.buffer.read(min(65536, MAX_REQUEST_BYTES - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_REQUEST_BYTES:
                sys.stderr.write(f"mini-tools: request stdin exceeds {MAX_REQUEST_BYTES} bytes\n")
                return 1
            chunks.append(chunk)

        raw_input = b"".join(chunks)
        if not raw_input:
            sys.stderr.write("mini-tools: request stdin is empty\n")
            return 1

        try:
            request = json.loads(raw_input.decode("utf-8"))
        except Exception as exc:
            sys.stderr.write(f"mini-tools: request is not valid JSON: {exc}\n")
            return 1

        if not isinstance(request, dict) or set(request) != {"tool_id", "arguments", "environment"}:
            sys.stderr.write("mini-tools: request must contain tool_id, arguments, and environment\n")
            return 1

        tool_id = request.get("tool_id")
        if tool_id != MINI_TOOL_ID:
            sys.stderr.write(f"mini-tools: unknown tool_id {tool_id!r}, expected {MINI_TOOL_ID!r}\n")
            return 1

        arguments = request.get("arguments")
        if not isinstance(arguments, dict):
            sys.stderr.write("mini-tools: arguments must be a JSON object\n")
            return 1

        environment = request["environment"]
        if not isinstance(environment, dict) or any(
            not key or "=" in key or "\x00" in key
            or type(value) is not str or "\x00" in value
            for key, value in environment.items()
        ):
            sys.stderr.write("mini-tools: environment must contain valid string bindings\n")
            return 1

        result = execute_mini_shell_action(arguments, env=environment)
        output_bytes = json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n"
        sys.stdout.buffer.write(output_bytes)
        sys.stdout.buffer.flush()
        return 0

    except RawOutputLimitExceeded as exc:
        failure = {
            "outer_error": "raw_output_limit_exceeded",
            "raw_prefix_base64": base64.b64encode(exc.raw_prefix).decode("ascii"),
            "examined_bytes": exc.total_bytes,
        }
        sys.stdout.buffer.write(json.dumps(failure).encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
        sys.stderr.write(
            f"mini-tools: output limit exceeded: {exc} (examined {exc.total_bytes} bytes)\n"
        )
        return 1
    except (HelperCancelled, KeyboardInterrupt) as exc:
        sys.stderr.write(f"mini-tools: cancelled: {exc}\n")
        return 130
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)


if __name__ == "__main__":
    sys.exit(main())
