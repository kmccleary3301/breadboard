"""Codex rust-v0.139.0 unified-exec behavior, selected by catalog configuration.

References are relative to codex-rs at a7dff904308535e965aee87680c1fc5ef1d19eec.
The process manager runs inside the workspace sandbox actor, not the conductor.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import pwd
import random
import subprocess
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from ..security import ProcessIsolationUnavailable, build_restricted_process_command


def text_output(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"error" if error else "ok": text if error else True, "__mvi_text_output": text}


def truncate_output(text: str, max_tokens: int) -> str:
    """utils/output-truncation/src/lib.rs:12-25; utils/string/src/truncate.rs:38-152."""
    raw = text.encode("utf-8")
    budget = max_tokens * 4
    if len(raw) <= budget:
        return text
    left = budget // 2
    right = budget - left
    prefix = raw[:left].decode("utf-8", errors="ignore")
    suffix = raw[len(raw) - right:].decode("utf-8", errors="ignore") if right else ""
    removed_tokens = (len(raw) - budget + 3) // 4
    # Rust str::lines recognizes LF and CRLF, not Python's other Unicode separators.
    lines = text.split("\n")
    line_count = len(lines) - int(text.endswith("\n"))
    return f"Total output lines: {line_count}\n\n{prefix}…{removed_tokens} tokens truncated…{suffix}"


def format_exec_output(
    raw: bytes, *, chunk_id: str, wall_time: float, exit_code: int | None,
    session_id: int | None, max_output_tokens: int | None = None,
    policy_tokens: int = 10_000,
) -> str:
    """core/src/tools/context.rs:399-434. Counts UTF-8 bytes, not model tokens."""
    text = raw.decode("utf-8", errors="replace")
    sections = []
    if chunk_id:
        sections.append(f"Chunk ID: {chunk_id}")
    sections.append(f"Wall time: {wall_time:.4f} seconds")
    if exit_code is not None:
        sections.append(f"Process exited with code {exit_code}")
    if session_id is not None:
        sections.append(f"Process running with session ID {session_id}")
    sections.append(f"Original token count: {(len(text.encode('utf-8')) + 3) // 4}")
    sections.extend(("Output:", truncate_output(text, min(
        10_000 if max_output_tokens is None else max_output_tokens, policy_tokens,
    ))))
    return "\n".join(sections)


@dataclass
class _Process:
    process: subprocess.Popen
    tty: bool
    reader: Any
    writer_fd: int | None
    last_used: float
    output: bytearray = field(default_factory=bytearray)
    closed: bool = False
    reader_closed: bool = False
    reader_thread: threading.Thread | None = None
    reader_cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Condition = field(default_factory=threading.Condition)

    def read_background(self) -> None:
        # core/src/unified_exec/head_tail_buffer.rs:60-129 retains a 1 MiB head/tail.
        # select() is limited to FD_SETSIZE (1024), so use the platform selector
        # (kqueue/epoll), which accepts any descriptor a long-lived host has open.
        import selectors

        descriptor = self.reader if self.tty else self.reader.fileno()
        selector = selectors.DefaultSelector()
        try:
            selector.register(descriptor, selectors.EVENT_READ)
            while not self.reader_cancel.is_set():
                if not selector.select(0.025) or self.reader_cancel.is_set():
                    continue
                try:
                    chunk = os.read(descriptor, 65536)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    if self.tty and exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                with self.lock:
                    self.output.extend(chunk)
                    if len(self.output) > 1024 * 1024:
                        del self.output[512 * 1024:len(self.output) - 512 * 1024]
                    self.lock.notify_all()
        finally:
            selector.close()
            self.close()
            with self.lock:
                self.closed = True
                self.lock.notify_all()

    def close(self) -> None:
        with self.lock:
            if self.reader_closed:
                return
            self.reader_closed = True
            if self.tty:
                os.close(self.reader)
            else:
                self.reader.close()

    def terminate(self) -> None:
        if self.process.poll() is None or not self.closed:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.process.wait()
        self.reader_cancel.set()
        if self.reader_thread is not None:
            self.reader_thread.join(timeout=0.1)
            if not self.reader_thread.is_alive():
                self.close()
        else:
            self.close()


def shell_argv(command: str, provided: str | None, login: bool) -> list[str]:
    """shell-command/src/shell_detect.rs:39-58,135-163,255-294; core/src/shell.rs:30-55."""
    import shutil
    import sys

    try:
        user_shell = pwd.getpwuid(os.getuid()).pw_shell
    except KeyError:
        user_shell = None
    known = {"zsh", "bash", "sh", "pwsh", "powershell", "cmd"}

    def kind(path: str | None) -> str | None:
        if not path:
            return None
        while path not in known:
            name = Path(path).stem
            if name == path:
                return None
            path = name
        return "pwsh" if path == "powershell" else path

    def resolve(name: str, model_path: str | None = None) -> str | None:
        if model_path and Path(model_path).is_file():
            return model_path
        if kind(user_shell) == name and Path(user_shell).is_file():
            return user_shell
        binary = shutil.which(name)
        if binary:
            return binary
        fallbacks = {"zsh": ["/bin/zsh"], "bash": ["/bin/bash", "/usr/bin/bash"],
                     "sh": ["/bin/sh", "/usr/bin/sh"]}
        return next((p for p in fallbacks.get(name, []) if Path(p).is_file()), None)

    if provided is not None:
        shell_kind = kind(provided)
        shell = resolve(shell_kind, provided) if shell_kind else None
    else:
        names = [kind(user_shell), *(["zsh", "bash"] if sys.platform == "darwin" else ["bash", "zsh"])]
        shell_kind, shell = "sh", None
        for name in names:
            if name and (candidate := resolve(name)):
                shell_kind, shell = name, candidate
                break
    if shell is None:
        shell_kind, shell = "sh", "/bin/sh"
    if shell_kind in {"pwsh", "powershell"}:
        return [shell, *([] if login else ["-NoProfile"]), "-Command", command]
    if shell_kind == "cmd":
        return [shell, "/c", command]
    return [shell, "-lc" if login else "-c", command]


class CodexExecManager:
    def __init__(
        self, workspace: str, *, truncation_policy: dict[str, Any],
        clock: Callable[[], float] = time.monotonic,
        process_id: Callable[[], int] | None = None,
        chunk_id: Callable[[], str] | None = None,
        protected_paths: tuple[str | os.PathLike[str], ...] = (),
    ) -> None:
        self.workspace = Path(workspace)
        self.protected_paths = protected_paths
        self.truncation_policy = truncation_policy
        self.clock = clock
        self.process_id = process_id if process_id is not None else (
            lambda: random.SystemRandom().randrange(1000, 100000)
        )
        self.chunk_id = chunk_id or (lambda: f"{random.SystemRandom().randrange(16 ** 6):06x}")
        self.sessions: dict[int, _Process] = {}
        self.reserved_process_ids: set[int] = set()
        self.process_id_allocation_count = 0
        self.process_id_draw_count = 0
        self.process_id_lock = threading.Lock()
        import atexit

        atexit.register(self.close)

    def set_process_id_generator(self, generator: Callable[[], int]) -> None:
        with self.process_id_lock:
            if self.process_id_draw_count:
                raise RuntimeError("Process ID generator must be installed before the first allocation")
            if not callable(generator):
                raise TypeError("Process ID generator must be callable")
            self.process_id = generator

    def process_id_generator_state(self) -> dict[str, int]:
        with self.process_id_lock:
            return {"allocation_count": self.process_id_allocation_count,
                    "draw_count": self.process_id_draw_count}

    def allocate_process_id(self) -> int:
        # process_manager.rs:332-355: production uniform range, collision redraw.
        with self.process_id_lock:
            while True:
                self.process_id_draw_count += 1
                process_id = self.process_id()
                if not isinstance(process_id, int) or isinstance(process_id, bool) or not 1000 <= process_id < 100000:
                    raise ValueError("Process ID generator must return an integer in [1000, 100000)")
                if process_id in self.reserved_process_ids:
                    continue
                self.reserved_process_ids.add(process_id)
                self.process_id_allocation_count += 1
                return process_id

    def release_process_id(self, process_id: int) -> None:
        with self.process_id_lock:
            self.reserved_process_ids.discard(process_id)

    def _collect(self, entry: _Process, yield_ms: int) -> tuple[bytes, float]:
        # process_manager.rs:1082-1168 waits for exit/output closure, not first output.
        start = self.clock()
        deadline = start + yield_ms / 1000
        output = bytearray()
        post_exit_deadline = None
        with entry.lock:
            while True:
                output.extend(entry.output)
                entry.output.clear()
                exited = entry.process.poll() is not None
                if exited and entry.closed:
                    break
                remaining = deadline - self.clock()
                if remaining <= 0:
                    break
                if exited:
                    if post_exit_deadline is None:
                        post_exit_deadline = self.clock() + min(remaining, 0.05)
                    remaining = min(remaining, post_exit_deadline - self.clock())
                    if remaining <= 0:
                        break
                entry.lock.wait(min(remaining, 0.05))
        return bytes(output), self.clock() - start

    def _result(self, session_id: int, entry: _Process, args: dict, yield_ms: int) -> dict:
        raw, wall_time = self._collect(entry, yield_ms)
        code = entry.process.poll()
        # codex-utils-pty's exit status maps signal termination to -1.
        if code is not None and code < 0:
            code = -1
        continuing = session_id if code is None else None
        if continuing is None:
            self.sessions.pop(session_id)
            entry.terminate()
            self.release_process_id(session_id)
        else:
            entry.last_used = self.clock()
        output = format_exec_output(
            raw, chunk_id=self.chunk_id(), wall_time=wall_time, exit_code=code,
            session_id=continuing, max_output_tokens=args.get("max_output_tokens"),
            policy_tokens=policy_token_budget(self.truncation_policy),
        )
        return {"stdout": raw.decode("utf-8", errors="replace"), "exit": code,
                "session_id": continuing, "__mvi_text_output": output}

    def exec_command(self, args: dict[str, Any], thread_id: str | None = None) -> dict:
        # exec_command.rs:146 allocates before shell launch or patch interception.
        session_id = self.allocate_process_id()
        try:
            return self._exec_command(args, thread_id, session_id)
        finally:
            if session_id not in self.sessions:
                self.release_process_id(session_id)

    def _exec_command(self, args: dict[str, Any], thread_id: str | None, session_id: int) -> dict:
        argv = shell_argv(args["cmd"], args.get("shell"), args.get("login", True))
        cwd = self.workspace / (args.get("workdir") or "")
        # protocol/src/config_types.rs:233-241 defaults to All and skips excludes.
        env = dict(os.environ)
        # process_manager.rs:60-71.
        env.update(NO_COLOR="1", TERM="dumb", LANG="C.UTF-8", LC_CTYPE="C.UTF-8",
                   LC_ALL="C.UTF-8", COLORTERM="", PAGER="cat", GIT_PAGER="cat",
                   GH_PAGER="cat", CODEX_CI="1")
        if thread_id is not None:
            env["CODEX_THREAD_ID"] = thread_id
        if args.get("sandbox_permissions", "use_default") == "require_escalated":
            # exec_command.rs:186-201; stock codex exec's --ask-for-approval never.
            return text_output("approval policy is Never; reject command — you cannot ask for escalated permissions if the approval policy is Never", error=True)
        patch = intercept_patch(argv, cwd, self.truncation_policy, args.get("max_output_tokens"),
                                workspace=self.workspace, protected_paths=self.protected_paths)
        if patch["intercepted"]:
            return text_output(patch["output"], error=patch["error"])
        try:
            isolated_argv, env = build_restricted_process_command(
                argv, workspace=self.workspace, shell=False,
                environment=env, protected_paths=self.protected_paths,
            )
        except ProcessIsolationUnavailable:
            return text_output("sandbox command rejected: filesystem isolation is unavailable", error=True)
        tty = args.get("tty", False)
        master = slave = None
        try:
            if tty:
                import fcntl
                import pty
                import struct
                import termios

                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
                process = subprocess.Popen(
                    isolated_argv, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
                    start_new_session=True, preexec_fn=lambda: fcntl.ioctl(slave, termios.TIOCSCTTY, 0),
                )
                os.close(slave)
                slave = None
                reader, writer = master, master
            else:
                process = subprocess.Popen(isolated_argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                reader, writer = process.stdout, None
        except OSError as exc:
            if master is not None:
                os.close(master)
            if slave is not None:
                os.close(slave)
            failure = intercept_patch(argv, cwd, self.truncation_policy, args.get("max_output_tokens"),
                                      workspace=self.workspace, protected_paths=self.protected_paths,
                                      spawn_error=exc.errno)
            return text_output(failure["output"], error=True)
        # process_manager.rs:1207-1251 protects the eight most recently used sessions.
        if len(self.sessions) >= 64:
            protected = set(sorted(self.sessions, key=lambda i: self.sessions[i].last_used, reverse=True)[:8])
            eligible = sorted((i for i in self.sessions if i not in protected), key=lambda i: self.sessions[i].last_used)
            prune = next((i for i in eligible if self.sessions[i].process.poll() is not None), eligible[0])
            removed = self.sessions.pop(prune)
            removed.terminate()
            self.release_process_id(prune)
        entry = _Process(process, tty, reader, writer, self.clock())
        self.sessions[session_id] = entry
        entry.reader_thread = threading.Thread(target=entry.read_background, daemon=True)
        entry.reader_thread.start()
        return self._result(session_id, entry, args, max(250, min(args.get("yield_time_ms", 10000), 30000)))

    def write_stdin(self, args: dict[str, Any]) -> dict:
        session_id = args["session_id"]
        entry = self.sessions.get(session_id)
        if entry is None:
            return text_output(f"write_stdin failed: Unknown process id {session_id}", error=True)
        chars = args.get("chars", "")
        if chars:
            if not entry.tty:
                return text_output("write_stdin failed: stdin is closed for this session; rerun exec_command with tty=true to keep stdin open", error=True)
            try:
                os.write(entry.writer_fd, chars.encode("utf-8"))
            except OSError:
                # process_manager.rs:628-638 only suppresses write failure after exit.
                if entry.process.poll() is None:
                    return text_output("write_stdin failed: failed to write to stdin", error=True)
            else:
                time.sleep(0.1)
        yield_ms = args.get("yield_time_ms", 250)
        yield_ms = max(250 if chars else 5000, min(yield_ms, 30000 if chars else 300000))
        return self._result(session_id, entry, args, yield_ms)

    def close(self) -> None:
        for entry in self.sessions.values():
            entry.terminate()
        self.sessions.clear()
        with self.process_id_lock:
            self.reserved_process_ids.clear()


def model_spec(owner: Any) -> dict[str, Any]:
    # model_info.rs:91 provides model policy; protocol.rs:2965-2972 converts bytes.
    providers = owner.config.get("providers") or {}
    state = getattr(owner, "_active_session_state", None)
    route = getattr(owner, "_current_route_id", None)
    if route is None and state is not None:
        route = state.get_provider_metadata("route_id")
    if route is None:
        route = providers["default_model"]
    model = next(spec for spec in providers["models"] if spec["id"] == route)
    return model


def policy_token_budget(policy: dict[str, Any]) -> int:
    # protocol/src/protocol.rs:2965-2972: preserve mode until a token cap is needed.
    if policy["mode"] == "bytes":
        return (policy["limit"] + 3) // 4
    if policy["mode"] == "tokens":
        return policy["limit"]
    raise ValueError(f"Unsupported tool output truncation mode: {policy['mode']}")


def execute_codex_tool(owner: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if (
        (owner.config.get("tools") or {}).get("native_semantics") == "codex_0_139"
        and (name == "tool_search" or name.startswith("multi_agent_v1."))
        and getattr(owner, "_codex_agent_depth", 0) >= (owner.config.get("agents") or {}).get("max_depth", 1)
    ):
        # spec_plan.rs:304-312,698,849-856 omits these handlers at the v1
        # depth limit, not merely their advertised schemas.
        if name == "tool_search":
            return text_output(json.dumps({
                "status": "completed", "execution": "client", "tools": [],
            }, separators=(",", ":"), sort_keys=True))
        # Registry unsupported message uses ToolName's Display, which joins
        # the namespace and name without a separator (tool_name.rs:37-42).
        return text_output(f"unsupported call: {name.replace('.', '', 1)}", error=True)
    if name in {"exec_command", "write_stdin"}:
        state = getattr(owner, "_active_session_state", None)
        thread_id = state.get_provider_metadata("session_id") if state is not None else None
        if thread_id is None:
            thread_id = getattr(owner, "_codex_agent_id", None)
        policy = model_spec(owner)["tool_output_truncation"]
        if name == "exec_command":
            return owner._ray_get(owner.sandbox.codex_exec_command.remote(args, thread_id, policy))
        return owner._ray_get(owner.sandbox.codex_write_stdin.remote(args, thread_id, policy))
    if name == "apply_patch":
        return owner._ray_get(owner.sandbox.codex_apply_patch.remote(
            args, model_spec(owner)["tool_output_truncation"]))
    if name == "tool_search":
        from .codex_search import execute_codex_search_tool

        return execute_codex_search_tool(owner, args)
    if name == "update_plan":
        # core/src/tools/handlers/plan.rs:22,85-90. Stock emits every decoded plan.
        owner._codex_update_plan_state = args
        return text_output("Plan updated")
    if name == "request_user_input":
        # request_user_input_spec.rs:85-95: stock exec's collaboration mode is Default.
        return text_output("request_user_input is unavailable in Default mode", error=True)
    if name == "view_image":
        # tools/src/image_detail.rs:6-8; protocol/openai_models.rs:380-381.
        supports_original = model_spec(owner).get("supports_image_detail_original", False)
        return owner._ray_get(owner.sandbox.codex_view_image.remote(args, supports_original))
    if name.startswith("multi_agent_v1.") or name in {"get_goal", "create_goal", "update_goal"}:
        from .codex_agents import execute_codex_agent_tool

        return execute_codex_agent_tool(owner, name, args)
    # tools/router.rs routes unknown names without treating shell aliases as commands.
    return text_output(f"unsupported call: {name}", error=True)

def codex_patch_binary() -> Path:
    import hashlib

    source = Path(__file__).with_name("codex_patch")
    digest = hashlib.sha256()
    for name in ("Cargo.toml", "Cargo.lock", "main.rs"):
        digest.update((source / name).read_bytes())
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "breadboard" / "codex-patch" / digest.hexdigest() / "release" / "breadboard-codex-patch"


def intercept_patch(argv: list[str], cwd: Path, truncation_policy: dict[str, Any], max_output_tokens: int | None,
                    *, workspace: Path | None = None, protected_paths: tuple[str | os.PathLike[str], ...] = (),
                    spawn_error: int | None = None, direct_patch: str | None = None) -> dict[str, Any]:
    # Stock core/tools/handlers/apply_patch.rs:509-611 and pinned stock crate.
    import json

    binary = codex_patch_binary()
    if not binary.is_file():
        return {"intercepted": True, "error": True,
                "output": "Codex patch helper is not built; run `python scripts/build_codex_patch.py` from the repository root."}
    try:
        isolated_argv, environment = build_restricted_process_command(
            [str(binary)], workspace=workspace if workspace is not None else cwd,
            shell=False, environment=os.environ, protected_paths=protected_paths,
        )
    except ProcessIsolationUnavailable:
        return {"intercepted": True, "error": True,
                "output": "sandbox command rejected: filesystem isolation is unavailable"}
    completed = subprocess.run(
        isolated_argv, env=environment, input=json.dumps({"argv": argv, "cwd": str(cwd),
         "truncation_policy": truncation_policy, "max_output_tokens": max_output_tokens,
         "spawn_error": spawn_error, "direct_patch": direct_patch}), text=True, capture_output=True, check=True,
    )
    return json.loads(completed.stdout)


def codex_image_binary() -> Path:
    import hashlib

    source = Path(__file__).with_name("codex_image")
    digest = hashlib.sha256()
    for name in ("Cargo.toml", "Cargo.lock", "main.rs"):
        digest.update((source / name).read_bytes())
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "breadboard" / "codex-image" / digest.hexdigest() / "release" / "breadboard-codex-image"


def view_image(workspace: str, args: dict[str, Any], *, supports_original_detail: bool = False,
               protected_paths: tuple[str | os.PathLike[str], ...] = ()) -> dict[str, Any]:
    """view_image.rs:120-191,223-237; utils/image/src/lib.rs:55-195."""
    import base64

    detail = args.get("detail")
    if detail not in (None, "high", "original"):
        return text_output(
            "view_image.detail only supports `high` or `original`; omit `detail` for default high resized behavior, "
            f"got `{detail}`", error=True,
        )
    path = Path(os.path.abspath(Path(workspace) / args["path"]))
    binary = codex_image_binary()
    if not binary.is_file():
        return text_output(
            "Codex image helper is not built; run `python scripts/build_codex_image.py` from the repository root.",
            error=True,
        )
    original = supports_original_detail and detail == "original"
    try:
        command, environment = build_restricted_process_command(
            [str(binary), str(path), *(["original"] if original else [])],
            workspace=workspace, shell=False, environment=os.environ,
            protected_paths=protected_paths,
        )
    except ProcessIsolationUnavailable:
        return text_output("sandbox command rejected: filesystem isolation is unavailable", error=True)
    result = subprocess.run(command, env=environment, capture_output=True)
    if result.returncode:
        return text_output(result.stderr.decode("utf-8"), error=True)
    mime, payload = result.stdout.split(b"\n", 1)
    image_url = f"data:{mime.decode('ascii')};base64,{base64.b64encode(payload).decode('ascii')}"
    return {"ok": True, "__mvi_native_responses_output": True, "__mvi_content_output": [
        {"type": "input_image", "image_url": image_url, "detail": "original" if original else "high"},
    ]}