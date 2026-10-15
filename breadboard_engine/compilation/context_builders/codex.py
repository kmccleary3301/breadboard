"""Pinned Codex a7dff904 startup context, using admitted replay environment facts.

Sources: core/src/session/mod.rs:2752-2977; context/environment_context.rs;
core/src/installation_id.rs. Prompt assets are copied from the pinned source,
not recordings. UUIDv7 matches protocol/src/thread_id.rs:17-21.
"""
from __future__ import annotations

from datetime import date
import fcntl
import os
from pathlib import Path
import pwd
import secrets
import time
import tomllib
import uuid

from .codex_skills import load_skills, render_skills

_ASSETS = Path(__file__).parent / "assets/codex_0_139"


def new_session_id() -> str:
    milliseconds = time.time_ns() // 1_000_000
    value = (milliseconds << 80) | (7 << 76) | (secrets.randbits(12) << 64) | (2 << 62) | secrets.randbits(62)
    return str(uuid.UUID(int=value))


def admit_environment(workspace: str) -> dict[str, str]:
    """Capture host facts at admission; the context renderer never reads clocks."""
    localtime = Path("/etc/localtime").resolve().as_posix()
    timezone = None
    for component in ("/zoneinfo/", "/zoneinfo.default/"):
        if component in localtime:
            timezone = localtime.split(component, 1)[1]
            break
    if timezone is None and Path("/etc/timezone").is_file():
        timezone = Path("/etc/timezone").read_text().strip()
    environment = {"cwd": str(Path(workspace).resolve()), "shell": Path(pwd.getpwuid(os.getuid()).pw_shell).name,
                   "current_date": date.today().isoformat()}
    if timezone is not None:
        environment["timezone"] = timezone
    return environment


def resolve_installation_id(home: Path) -> str:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "installation_id"
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        path.chmod(0o644)
        handle.seek(0)
        value = handle.read().strip()
        try:
            return str(uuid.UUID(value))
        except ValueError:
            value = str(uuid.uuid4())
            handle.seek(0)
            handle.truncate()
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
            return value


def environment_message(environment: dict[str, str], workspace: str) -> dict:
    # environment_context.rs:144-155,177-189,541-589: only workspace roots
    # are XML-escaped by push_text_element; cwd/date/shell are literal.
    from html import escape
    root = escape(workspace, quote=False)
    lines = ["<environment_context>", f"  <cwd>{environment['cwd']}</cwd>",
             f"  <shell>{environment['shell']}</shell>"]
    for name in ("current_date", "timezone"):
        if name in environment:
            lines.append(f"  <{name}>{environment[name]}</{name}>")
    lines.append(f'  <filesystem><workspace_roots><root>{root}</root></workspace_roots>'
                 '<permission_profile type="disabled"><file_system type="unrestricted" /></permission_profile></filesystem>')
    lines.append("</environment_context>")
    return {"role": "user", "content": [{"type": "text", "text": "\n".join(lines)}]}


def _skill_roots(home: Path, workspace: Path) -> list[tuple[Path, int]]:
    # loader.rs:287-359 root precedence: repo, deprecated user, .agents user,
    # bundled system, admin. Hidden .system is never scanned as a user skill.
    roots = []
    for parent in (workspace, *workspace.parents):
        roots.append((parent / ".agents/skills", 2))
        roots.append((parent / ".codex/skills", 2))
        if (parent / ".git").exists():
            break
    roots.extend([(home / "skills", 3), (Path.home() / ".agents/skills", 3),
                  (home / "skills/.system", 0), (Path("/etc/codex/skills"), 1)])
    return roots


def build_context(owner, state, run_context, *, fresh, user_message):
    configured = owner.config["prompts"]["initial_context"]
    environment = run_context.get("native_environment")
    if environment is None:
        environment = state.get_provider_metadata("initial_context_environment")
    if environment is None:
        environment = getattr(owner, "_codex_environment", None)
    if not isinstance(environment, dict) or not all(isinstance(environment.get(key), str) for key in ("cwd", "shell")):
        raise ValueError("Codex startup requires an admitted native_environment with cwd and shell")
    workspace = str(Path(owner.workspace).resolve())
    if environment["cwd"] != workspace:
        raise ValueError("Admitted Codex cwd differs from the execution workspace")
    owner._codex_environment = dict(environment)
    state.set_provider_metadata("initial_context_environment", dict(environment))
    home = Path(os.environ["CODEX_HOME"]) if "CODEX_HOME" in os.environ else Path.home() / ".codex"
    state.set_provider_metadata("installation_id", resolve_installation_id(home))
    if not fresh:
        return []
    base = _ASSETS.joinpath("gpt55_instructions.md").read_text()
    with state.context_mutation():
        state.messages[0]["content"] = base
        state.provider_messages[0]["content"] = base
    permission = (_ASSETS.joinpath("danger_full_access.md").read_text().strip().replace("{{network_access}}", "enabled") +
                  "\n" + _ASSETS.joinpath("approval_never.md").read_text().strip())
    sections = ["<permissions instructions>\n" + permission + "\n</permissions instructions>"]
    sections.extend(configured.get("developer_instructions", []))
    native_config = tomllib.loads((home / "config.toml").read_text()) if (home / "config.toml").is_file() else {}
    skill_config = native_config.get("skills") or {}
    if skill_config.get("include_instructions", True):
        disabled = {Path(item["path"]).expanduser().resolve() for item in skill_config.get("config", []) if item.get("enabled") is False}
        roots = _skill_roots(home, Path(workspace))
        window = owner.compaction_controller.resolve_context_window(conductor=owner, session_state=state)
        skills = render_skills(load_skills(roots, disabled), roots, window)
        if skills is not None:
            sections.append(skills)
    return [{"role": "developer", "content": [{"type": "text", "text": section} for section in sections]},
            environment_message(environment, workspace)]
