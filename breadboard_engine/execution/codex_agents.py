"""Codex 0.139.0 collaboration and goal tools, reached only by the native gate."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import json
import random
import threading
import time
import uuid


# codex-rs/core/src/agent/agent_names.txt at a7dff904.
AGENT_NAMES = """Euclid Archimedes Ptolemy Hypatia Avicenna Averroes Aquinas
Copernicus Kepler Galileo Bacon Descartes Pascal Fermat Huygens Leibniz Newton
Halley Euler Lagrange Laplace Volta Gauss Ampere Faraday Darwin Lovelace Boole
Pasteur Maxwell Mendel Curie Planck Tesla Poincare Noether Hilbert Einstein Raman
Bohr Turing Hubble Feynman Franklin McClintock Meitner Herschel Linnaeus Wegener
Chandrasekhar Sagan Goodall Carson Carver Socrates Plato Aristotle Epicurus Cicero
Confucius Mencius Zeno Locke Hume Kant Hegel Kierkegaard Mill Nietzsche Peirce James
Dewey Russell Popper Sartre Beauvoir Arendt Rawls Singer Anscombe Parfit Kuhn Boyle
Hooke Harvey Dalton Ohm Helmholtz Gibbs Lorentz Schrodinger Heisenberg Pauli Dirac
Bernoulli Godel Nash Banach Ramanujan Erdos Jason""".split()
COMPLETION_BUDGET_REPORT = (
    "Goal achieved. Report final usage from this tool result's structured goal fields. "
    "If `goal.tokenBudget` is present, include token usage from `goal.tokensUsed` and "
    "`goal.tokenBudget`. If `goal.timeUsedSeconds` is greater than 0, summarize elapsed "
    "time in a concise, human-friendly form appropriate to the response language."
)
# Capability projection of models-manager/models.json at a7dff904.
STOCK_MODEL_INFO = json.loads(Path(__file__).with_name("codex_139_models.json").read_text())
STOCK_MODELS = tuple(STOCK_MODEL_INFO)


class AgentToolError(ValueError):
    """An error returned verbatim as function_call_output."""


def _json(value, *, sort_keys=False):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=sort_keys)


def _new_thread_id():
    # ThreadId::new uses UUIDv7 (protocol/src/thread_id.rs:18-21).
    return str(uuid.UUID(int=(int(time.time() * 1000) << 80) | (7 << 76)
                         | (random.getrandbits(12) << 64) | (2 << 62)
                         | random.getrandbits(62)))


def _required(args, key):
    if key not in args:
        raise AgentToolError(f"missing field `{key}`")
    return args[key]


def _thread_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        # uuid::Error Debug, used by multi_agents.rs:47-50.
        text = str(value)
        raw = text.removeprefix("urn:uuid:")
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
        for index, char in enumerate(raw):
            if char not in "0123456789abcdefABCDEF-":
                detail = f'Error(ParseChar {{ character: \'{char}\', index: {index + 1} }})'
                break
        else:
            if "-" not in raw:
                detail = f"Error(ParseSimpleLength {{ len: {len(raw)} }})"
            elif raw.count("-") != 4:
                detail = f"Error(ParseGroupCount {{ count: {raw.count('-') + 1} }})"
            else:
                groups = raw.split("-")
                index = next(i for i, (part, size) in enumerate(zip(groups, (8, 4, 4, 4, 12))) if len(part) != size)
                detail = f"Error(ParseGroupLength {{ group: {index}, len: {len(groups[index])}, index: {sum(len(p) + 1 for p in groups[:index])} }})"
        raise AgentToolError(f"invalid agent id {text}: {detail}") from None


def _input(owner, args):
    message, items = args.get("message"), args.get("items")
    if message is not None and items is not None:
        raise AgentToolError("Provide either message or items, but not both")
    if message is None and items is None:
        raise AgentToolError("Provide one of: message or items")
    if message is not None:
        if not message.strip():
            raise AgentToolError("Empty message can't be sent to an agent")
        return message, [{"type": "text", "text": message}]
    if not items:
        raise AgentToolError("Items can't be empty")
    texts, content = [], []
    image_index = 0
    for item in items:
        kind = _required(item, "type")
        if kind == "text":
            text = _required(item, "text")
            texts.append(text)
            content.append({"type": "text", "text": text})
        elif kind in ("image", "local_image"):
            image_index += 1
            if kind == "image":
                url = _required(item, "image_url")
            else:
                path = _required(item, "path")
                try:
                    owner._workspace_filesystem_for_persistence().read_bytes(path)
                except OSError as exc:
                    content.append({"type": "text", "text": f"Codex could not read the local image at `{path}`: {exc.strerror} (os error {exc.errno})"})
                    continue
                # UserInput resizes auto/low/high identically while preserving
                # requested wire detail (protocol/src/models.rs:1085-1100).
                image_detail = item.get("detail") or "high"
                output = owner._ray_get(owner.sandbox.codex_view_image.remote(
                    {"path": path, "detail": "original" if image_detail == "original" else "high"}, True))
                if output.get("error"):
                    content.append({"type": "text", "text": output["error"]})
                    continue
                url = output["__mvi_content_output"][0]["image_url"]
                content.append({"type": "text", "text": f'<image name=[Image #{image_index}] path="{path}">'})
            content.append({"type": "media", "kind": "codex_image_url",
                            "uri": url, "detail": item.get("detail") or "high"})
            if kind == "local_image":
                content.append({"type": "text", "text": "</image>"})
        elif kind == "skill":
            path = _required(item, "path")
            name = _required(item, "name")
            text = owner._workspace_filesystem_for_persistence().read_text(path)
            text = f"<skill>\n<name>{name}</name>\n<path>{path}</path>\n{text[:40000]}\n</skill>"
            content.append({"type": "text", "text": text})
        elif kind == "mention":
            # UserInput::Mention has no visible content (models.rs:1268).
            _required(item, "name")
            _required(item, "path")
        else:
            raise AgentToolError(f"unknown variant `{kind}`, expected one of `text`, `image`, `local_image`, `skill`, `mention`")
    return "\n".join(texts), content


def prepare_codex_input_message(owner, message):
    """Preserve the ordered UserInput contents in a native child turn."""
    content = getattr(owner, "_codex_input_content", None)
    if (owner.config.get("tools") or {}).get("native_semantics") == "codex_0_139" and content is not None:
        from ..provider.contract_messages import normalize_content

        message["content"] = normalize_content(content, role="user")


@dataclass
class Child:
    id: str
    parent_id: str
    depth: int
    nickname: str | None
    config: dict
    route: str
    conductor: object
    messages: list = field(default_factory=list)
    pending: deque = field(default_factory=deque)
    status: object = "pending_init"
    closed: bool = False
    interrupted: bool = False
    worker: threading.Thread | None = None
    compaction_state: list = field(default_factory=list)


class AgentRegistry:
    """One registry for a root conductor and all its real child conductors."""

    def __init__(self):
        self.condition = threading.Condition(threading.RLock())
        self.children = {}
        self.used_names = set()
        self.name_generation = 0

    def nickname(self, candidates):
        def suffix():
            n = self.name_generation + 1
            ending = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            return "" if n == 1 else f" the {n}{ending}"
        available = [name + suffix() for name in candidates if name + suffix() not in self.used_names]
        if not candidates:
            return None
        if not available:
            self.used_names.clear()
            self.name_generation += 1
            available = [name + suffix() for name in candidates]
        name = random.choice(available)
        self.used_names.add(name)
        return name

    def reserve(self, owner):
        limit = (owner.config.get("agents") or {}).get("max_threads", 6)
        if sum(not child.closed for child in self.children.values()) >= limit:
            raise AgentToolError("collab spawn failed: agent thread limit reached")

    def start(self, child):
        if child.worker is not None and child.worker.is_alive():
            return
        child.status = "pending_init"
        child.worker = threading.Thread(target=self.run, args=(child,), daemon=True,
                                        name=f"codex-agent-{child.id}")
        child.worker.start()

    def run(self, child):
        while True:
            with self.condition:
                if child.closed or not child.pending:
                    child.worker = None
                    return
                submission_id, prompt, media = child.pending.popleft()
                child.status = "running"
                child.interrupted = False
                self.condition.notify_all()
            try:
                context = {"session_id": child.id, "input_id": submission_id,
                           "turn_id": _new_thread_id()}
                child.conductor._codex_input_content = deepcopy(media)
                result = child.conductor.run_agentic_loop(
                    "", prompt, child.route,
                    max_steps=(child.config.get("loop") or {}).get("max_steps", 1000000),
                    stream_responses=False, tool_prompt_mode="system_once",
                    completion_sentinel=None, completion_config=child.config.get("completion"),
                    context=context,
                )
                snapshot = result["_codex_session_snapshot"]
                child.messages = snapshot["messages"]
                child.compaction_state = snapshot["compaction_state"]
                summary = result.get("completion_summary") or {}
                if summary.get("error") or result.get("error"):
                    status = {"errored": str(summary.get("error") or result["error"])}
                elif child.interrupted:
                    status = "interrupted"
                else:
                    last = next((m.get("content") for m in reversed(child.messages)
                                 if m.get("role") == "assistant" and isinstance(m.get("content"), str)
                                 and m["content"]), None)
                    status = {"completed": last}
            except Exception as exc:
                status = {"errored": str(exc)}
            with self.condition:
                child.status = "shutdown" if child.closed else status
                try:
                    _save_child(child)
                except OSError as exc:
                    child.status = {"errored": str(exc)}
                self.condition.notify_all()
                if not child.pending or child.closed:
                    child.worker = None
                    return


_REGISTRY_LOCK = threading.Lock()


def _registry(owner):
    with _REGISTRY_LOCK:
        registry = getattr(owner, "_codex_agent_registry", None)
        if registry is None:
            registry = AgentRegistry()
            owner._codex_agent_registry = registry
        return registry


def _session_id(owner):
    state = getattr(owner, "_active_session_state", None)
    return getattr(owner, "_codex_agent_id", None) or (state.get_provider_metadata("session_id") if state else None)


def _save_child(child):
    payload = {"id": child.id, "parent_id": child.parent_id, "depth": child.depth,
               "nickname": child.nickname, "config": child.config, "route": child.route,
               "messages": child.messages, "compaction_state": child.compaction_state,
               "status": child.status, "closed": child.closed}
    if not child.conductor._write_workspace_text(f".breadboard/codex/agents/{child.id}.json", _json(payload)):
        raise OSError("failed to persist agent history")


def _make_child(owner, registry, *, agent_id, parent_id, depth, nickname, config, route, messages,
                compaction_state=None):
    # Each input restores this thread's raw history and ledger together.
    # Retained Product messages alone would reset the compaction state.
    config.setdefault("resume", {}).update({
        "snapshot_path": f".breadboard/codex/agents/{agent_id}.json",
        "restore_compaction_state": True,
    })
    child_owner = type(owner)(
        workspace=owner.workspace, image=owner.image, config=config, local_mode=True,
        prompt_base_dirs=list(getattr(owner, "_prompt_base_dirs", [])),
        protected_paths=getattr(owner, "_protected_credential_paths", ()),
        admitted_workspace=Path(owner.workspace),
    )
    # Share the admitted sandbox/permission authority, not native exec sessions:
    # the actor keys each manager by the root session ID or _codex_agent_id.
    child_owner.sandbox = owner.sandbox
    child_owner._ray_get = owner._ray_get
    child_owner.permission_authority = owner.permission_authority
    child_owner.permission_broker = owner.permission_broker
    child_owner._codex_agent_registry = registry
    child_owner._codex_agent_id = agent_id
    child_owner._codex_parent_id = parent_id
    child_owner._codex_agent_depth = depth
    child_owner._codex_agent_nickname = nickname
    return Child(agent_id, parent_id, depth, nickname, config, route, child_owner, messages,
                 compaction_state=deepcopy(compaction_state or []))


def _final(status):
    return status not in ("pending_init", "running", "interrupted") if isinstance(status, str) else True


def _agent_tool(owner, name, args):
    registry = _registry(owner)
    depth = getattr(owner, "_codex_agent_depth", 0) + 1
    max_depth = (owner.config.get("agents") or {}).get("max_depth", 1)
    if name == "spawn_agent":
        prompt, media = _input(owner, args)
        if depth > max_depth:
            raise AgentToolError("Agent depth limit reached. Solve the task yourself.")
        role = (args.get("agent_type") or "").strip() or "default"
        if args.get("fork_context") and (
            bool((args.get("agent_type") or "").strip())
            or args.get("model") is not None or args.get("reasoning_effort") is not None
        ):
            raise AgentToolError("Full-history forked agents inherit the parent agent type, model, and reasoning effort; omit agent_type, model, and reasoning_effort, or spawn without a full-history fork.")
        roles = (owner.config.get("agents") or {}).get("roles", {})
        config = deepcopy(owner.config)
        config.setdefault("workspace", {})["root"] = owner.workspace
        config.pop("resume", None)
        route = args["model"] if args.get("model") is not None else (
            getattr(owner, "_current_route_id", None) or config["providers"]["default_model"]
        )
        config["providers"]["default_model"] = route
        models = config["providers"].get("models", [])
        if args.get("model") is not None and route not in STOCK_MODELS:
            raise AgentToolError(f"Unknown model `{route}` for spawn_agent. Available models: {', '.join(STOCK_MODELS)}")
        effort = args.get("reasoning_effort")
        if effort is not None and effort not in ("low", "medium", "high", "xhigh"):
            raise AgentToolError(f"Reasoning effort `{effort}` is not supported for model `{route}`. Supported reasoning efforts: low, medium, high, xhigh")
        if args.get("model") is not None and not any(model.get("id") == route for model in models):
            parent_route = getattr(owner, "_current_route_id", config["providers"]["default_model"])
            parent_model = next((model for model in models if model.get("id") == parent_route), {"adapter": "openai"})
            child_model = deepcopy(parent_model)
            child_model["id"] = route
            models.append(child_model)
            config["providers"]["models"] = models
        if args.get("model") is not None:
            # Selected stock ModelInfo, not the parent's image/truncation policy.
            # models-manager/models.json at the pinned Codex commit.
            selected_model = next(model for model in models if model["id"] == route)
            metadata = STOCK_MODEL_INFO[route]
            selected_model["supports_image_detail_original"] = metadata["supports_image_detail_original"]
            selected_model["tool_output_truncation"] = deepcopy(metadata["truncation_policy"])
            selected_model["web_search_tool_type"] = metadata["web_search_tool_type"]
        if effort is not None or args.get("service_tier") is not None or args.get("model") is not None:
            params = config.setdefault("provider_tools", {}).setdefault("openai", {}).setdefault("request_params", {})
            if effort is not None or args.get("model") is not None:
                params["reasoning"] = {"effort": effort or "medium"}
            if args.get("service_tier") is not None:
                params["service_tier"] = args["service_tier"]
        if role not in ("default", "explorer", "worker") and role not in roles:
            raise AgentToolError(f"unknown agent_type '{role}'")
        role_config = roles.get(role, {})
        config.update(deepcopy(role_config.get("config", {})))
        # models-manager/models.json exposes priority only on these two models.
        supported_tiers = ("priority",) if route in ("gpt-5.5", "gpt-5.4") else ()
        requested_tier = args.get("service_tier")
        if requested_tier is not None and requested_tier not in supported_tiers:
            supported = ", ".join(supported_tiers) or "none"
            raise AgentToolError(
                f"Service tier `{requested_tier}` is not supported for model `{route}`. Supported service tiers: {supported}"
            )
        params = ((config.get("provider_tools") or {}).get("openai") or {}).get("request_params", {})
        if params.get("service_tier") is not None and params["service_tier"] not in supported_tiers:
            params.pop("service_tier")
        state = getattr(owner, "_active_session_state", None)
        messages = []
        compaction_state = []
        if args.get("fork_context") and state:
            # Stock preserves Compacted replacement_history intact while
            # filtering subsequent ResponseItems (agent/control/spawn.rs:470-490).
            boundary = state.compaction_state.latest_boundary()
            kept_start = int(boundary.first_kept_index) if boundary is not None else 0
            messages = deepcopy([m for index, m in enumerate(state.provider_messages)
                                 if index < kept_start or m.get("role") in ("system", "developer", "user")
                                 or (m.get("role") == "assistant" and m.get("phase") == "final_answer")])
            compaction_state = state.compaction_state.to_list()
        with registry.condition:
            registry.reserve(owner)
            child = _make_child(owner, registry, agent_id=_new_thread_id(),
                                parent_id=_session_id(owner), depth=depth,
                                nickname=registry.nickname(role_config.get("nickname_candidates", AGENT_NAMES)),
                                config=config, route=route, messages=messages,
                                compaction_state=compaction_state)
            child.conductor._codex_agent_role = role
            registry.children[child.id] = child
            child.pending.append((_new_thread_id(), prompt, media))
            _save_child(child)
            registry.start(child)
        return {"agent_id": child.id, "nickname": child.nickname}
    if name == "wait_agent":
        targets = args.get("targets", [])
        if not targets:
            raise AgentToolError("agent ids must be non-empty")
        ids = [_thread_id(target) for target in targets]
        timeout = args.get("timeout_ms", 30000)
        if timeout is None:
            timeout = 30000
        if timeout <= 0:
            raise AgentToolError("timeout_ms must be greater than zero")
        deadline = time.monotonic() + min(3600000, max(10000, timeout)) / 1000
        with registry.condition:
            while True:
                statuses = {}
                for id in ids:
                    child = registry.children.get(id)
                    status = child.status if child and not child.closed else "not_found"
                    if _final(status):
                        statuses[id] = status
                if statuses:
                    return {"status": statuses, "timed_out": False}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"status": {}, "timed_out": True}
                registry.condition.wait(remaining)
    agent_id = _thread_id(_required(args, "id" if name == "resume_agent" else "target"))
    prompt_media = _input(owner, args) if name == "send_input" else None
    if name == "resume_agent" and depth > max_depth:
        raise AgentToolError("Agent depth limit reached. Solve the task yourself.")
    with registry.condition:
        child = registry.children.get(agent_id)
        if name == "resume_agent" and child is None:
            filesystem = owner._workspace_filesystem_for_persistence()
            path = f".breadboard/codex/agents/{agent_id}.json"
            if filesystem.exists(path):
                saved = json.loads(filesystem.read_text(path))
                registry.reserve(owner)
                child = _make_child(owner, registry, agent_id=agent_id,
                                    parent_id=saved["parent_id"], depth=saved["depth"],
                                    nickname=saved["nickname"], config=saved["config"],
                                    route=saved["route"], messages=saved["messages"],
                                    compaction_state=saved.get("compaction_state", []))
                registry.children[agent_id] = child
        if child is None:
            raise AgentToolError(f"agent with id {agent_id} not found")
        if name == "resume_agent":
            if child.closed:
                registry.reserve(owner)
                child.closed = False
                child.status = "pending_init"
                _save_child(child)
            return {"status": child.status}
        if child.closed:
            raise AgentToolError(f"agent with id {agent_id} not found")
        if name == "send_input":
            if args.get("interrupt"):
                child.interrupted = True
                child.conductor.request_stop()
            submission_id = _new_thread_id()
            prompt, media = prompt_media
            child.pending.append((submission_id, prompt, media))
            registry.start(child)
            return {"submission_id": submission_id}
        if name != "close_agent":
            raise AgentToolError(f"unknown tool: multi_agent_v1.{name}")
        previous = deepcopy(child.status)
        closing = [child]
        for current in closing:
            closing.extend(c for c in registry.children.values()
                           if c.parent_id == current.id and not c.closed)
        for current in closing:
            current.closed = True
            current.pending.clear()
            current.conductor.request_stop()
        registry.condition.notify_all()
    for current in closing:
        if current.worker is not None:
            current.worker.join()
        current.conductor._ray_get(
            current.conductor.sandbox.codex_close_exec_sessions.remote(current.id)
        )
        current.status = "shutdown"
        _save_child(current)
    return {"previous_status": previous}


def _goal_path(owner):
    id = _session_id(owner)
    if not id:
        raise AgentToolError("failed to read goal: thread id is unavailable")
    # Product sessions may use non-UUID admitted IDs; never use them as paths.
    return f".breadboard/codex/goals/{uuid.uuid5(uuid.NAMESPACE_URL, id)}.json"


def _read_goal(owner):
    filesystem = owner._workspace_filesystem_for_persistence()
    if filesystem is None:
        raise OSError("workspace persistence unavailable")
    path = _goal_path(owner)
    return json.loads(filesystem.read_text(path)) if filesystem.exists(path) else None


def _write_goal(owner, goal):
    if not owner._write_workspace_text(_goal_path(owner), _json(goal)):
        raise OSError("workspace persistence unavailable")


def _tokens(state):
    usage = state.get_provider_metadata("usage_totals", {}) if state else {}
    return int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))


def begin_codex_goal(owner, state):
    if (owner.config.get("tools") or {}).get("native_semantics") != "codex_0_139":
        return
    owner._codex_goal_baseline = _tokens(state)
    owner._codex_goal_clock = time.monotonic()


def sync_codex_goal(owner, state):
    if (owner.config.get("tools") or {}).get("native_semantics") != "codex_0_139":
        return
    with _registry(owner).condition:
        goal = _read_goal(owner)
        if goal is None or goal["status"] not in ("active", "budgetLimited"):
            return
        now = time.monotonic()
        tokens = _tokens(state)
        elapsed = int(now - getattr(owner, "_codex_goal_clock", now))
        delta = max(0, tokens - getattr(owner, "_codex_goal_baseline", tokens))
        if not elapsed and not delta:
            return
        goal["tokensUsed"] += delta
        goal["timeUsedSeconds"] += elapsed
        goal["updatedAt"] = int(time.time())
        budget = goal.get("tokenBudget")
        if budget is not None and goal["tokensUsed"] >= budget:
            goal["status"] = "budgetLimited"
        _write_goal(owner, goal)
        owner._codex_goal_baseline = tokens
        owner._codex_goal_clock = now - (now - getattr(owner, "_codex_goal_clock", now) - elapsed)


def _goal_tool(owner, name, args):
    with _registry(owner).condition:
        try:
            goal = _read_goal(owner)
            if name == "create_goal":
                objective = _required(args, "objective").strip()
                if not objective:
                    raise AgentToolError("goal objective must not be empty")
                if len(objective) > 4000:
                    raise AgentToolError("goal objective must be at most 4000 characters")
                budget = args.get("token_budget")
                if budget is not None and budget <= 0:
                    raise AgentToolError("goal budgets must be positive when provided")
                if goal is not None:
                    raise AgentToolError("cannot create a new goal because this thread already has a goal; use update_goal only when the existing goal is complete")
                now = int(time.time())
                goal = {"threadId": _session_id(owner), "objective": objective, "status": "active",
                        "tokensUsed": 0, "timeUsedSeconds": 0, "createdAt": now, "updatedAt": now}
                if budget is not None:
                    goal["tokenBudget"] = budget
                _write_goal(owner, goal)
                begin_codex_goal(owner, getattr(owner, "_active_session_state", None))
            elif name == "update_goal":
                status = _required(args, "status")
                if status not in ("complete", "blocked"):
                    raise AgentToolError("update_goal can only mark the existing goal complete or blocked; pause, resume, budget-limited, and usage-limited status changes are controlled by the user or system")
                if goal is None:
                    raise AgentToolError("cannot update goal because this thread has no goal")
                sync_codex_goal(owner, getattr(owner, "_active_session_state", None))
                goal = _read_goal(owner)
                if not (status == "blocked" and goal["status"] == "budgetLimited"):
                    goal["status"] = status
                goal["updatedAt"] = int(time.time())
                _write_goal(owner, goal)
        except OSError as exc:
            verb = {"get_goal": "read", "create_goal": "create", "update_goal": "update"}[name]
            raise AgentToolError(f"failed to {verb} goal: {exc}") from exc
        budget = goal.get("tokenBudget") if goal else None
        return {"goal": goal, "remainingTokens": max(0, budget - goal["tokensUsed"]) if budget is not None else None,
                "completionBudgetReport": COMPLETION_BUDGET_REPORT if name == "update_goal" and goal["status"] == "complete"
                and (budget is not None or goal["timeUsedSeconds"] > 0) else None}


def execute_codex_agent_tool(owner, name, args):
    """Return stock compact JSON or the stock RespondToModel error text."""
    try:
        value = (_agent_tool(owner, name.split(".", 1)[1], args)
                 if name.startswith("multi_agent_v1.") else _goal_tool(owner, name, args))
        return {"__mvi_text_output": _json(value, sort_keys=not name.startswith("multi_agent_v1."))}
    except AgentToolError as exc:
        return {"error": str(exc), "__mvi_text_output": str(exc)}