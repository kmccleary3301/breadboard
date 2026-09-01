from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

import pytest


ROOT = Path(__file__).resolve().parents[3]
MODULES = {
    "materialization": ROOT / "breadboard/rl/harness/materialization.py",
    "sandbox": ROOT / "breadboard/rl/harness/sandbox.py",
    "docker": ROOT / "breadboard/rl/harness/sandbox_docker.py",
}
WP7_SANDBOX_SYMBOLS = {
    "InstalledRuntime",
    "InstalledImage",
    "SandboxSecurityPolicy",
    "SandboxNetworkPolicy",
    "InstalledVerifier",
    "InstalledSandboxAuthoritySet",
    "SandboxExecutionPlan",
    "SandboxMeasurement",
    "SandboxRuntimeError",
    "SandboxPlanError",
    "MaterializationError",
    "CacheLeaseError",
    "SandboxLaunchError",
    "SandboxAttestationError",
    "WorkspaceStateError",
    "VerifierSnapshotError",
    "VerifierExecutionError",
    "SandboxFault",
    "build_sandbox_execution_plan",
    "RuntimeHandle",
    "RuntimeBackend",
    "TrustedProcessHandle",
    "TrustedProcessBackend",
    "LeaseBackedRunnerWorkspace",
    "SandboxWorkspaceLease",
    "VerifierWorkspaceLease",
    "SandboxRuntimeManager",
}


def _tree(name: str) -> ast.Module:
    return ast.parse(MODULES[name].read_text(encoding="utf-8"), filename=str(MODULES[name]))


def _qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _imports(tree: ast.AST) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            values.add(node.module or "")
    return values

FunctionOwner = ast.FunctionDef | ast.AsyncFunctionDef


def _method(tree: ast.Module, class_name: str, method_name: str) -> FunctionOwner:
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )


def _function(tree: ast.Module, name: str) -> FunctionOwner:
    return next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def _calls(owner: ast.AST, qualified_name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and _qualified_name(node.func) == qualified_name
    ]


def _references(owner: ast.AST, name: str) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id == name for node in ast.walk(owner)
    )


def _has_keyword(call: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in call.keywords)


def _has_false_keyword(call: ast.Call, name: str) -> bool:
    return any(
        keyword.arg == name
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is False
        for keyword in call.keywords
    )


def _attributes(owner: ast.AST) -> set[str]:
    return {
        qualified
        for node in ast.walk(owner)
        if isinstance(node, ast.Attribute)
        and (qualified := _qualified_name(node)) is not None
    }


def _direct_read_bytes_lines(owner: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_bytes"
    ]


def _missing_descriptor_read_operations(owner: ast.AST) -> set[str]:
    attributes = _attributes(owner)
    return {
        label
        for label, present in {
            "parent_descriptor": bool(_calls(owner, "_open_parent_descriptor")),
            "open_dir_fd": any(
                _has_keyword(call, "dir_fd") for call in _calls(owner, "os.open")
            ),
            "nofollow": "os.O_NOFOLLOW" in attributes,
            "fstat": bool(_calls(owner, "os.fstat")),
            "bounded_pread": bool(_calls(owner, "os.pread")),
        }.items()
        if not present
    }


def _missing_descriptor_write_operations(owner: ast.AST) -> set[str]:
    attributes = _attributes(owner)
    return {
        label
        for label, present in {
            "parent_descriptor": bool(_calls(owner, "_open_parent_descriptor")),
            "nofollow": "os.O_NOFOLLOW" in attributes,
            "nofollow_stat": any(
                _has_keyword(call, "dir_fd")
                and _has_false_keyword(call, "follow_symlinks")
                for call in _calls(owner, "os.stat")
            ),
            "exclusive_open": any(
                _has_keyword(call, "dir_fd") for call in _calls(owner, "os.open")
            )
            and "os.O_EXCL" in attributes,
            "descriptor_replace": any(
                _has_keyword(call, "src_dir_fd")
                and _has_keyword(call, "dst_dir_fd")
                for call in _calls(owner, "os.replace")
            ),
            "descriptor_cleanup": any(
                _has_keyword(call, "dir_fd") for call in _calls(owner, "os.unlink")
            ),
        }.items()
        if not present
    }


def _missing_descriptor_list_operations(owner: ast.AST) -> set[str]:
    return {
        label
        for label, present in {
            "parent_descriptor": bool(_calls(owner, "_open_parent_descriptor")),
            "descriptor_scandir": bool(_calls(owner, "os.scandir")),
            "nofollow_stat": any(
                _has_keyword(call, "dir_fd")
                and _has_false_keyword(call, "follow_symlinks")
                for call in _calls(owner, "os.stat")
            ),
            "directory_open": bool(_calls(owner, "_open_directory_at")),
            "identity_fstat": bool(_calls(owner, "os.fstat")),
            "bounded_output": _references(owner, "canonical_json_bytes"),
            "no_path_walk": not _calls(owner, "os.walk"),
        }.items()
        if not present
    }


def _wp7_sandbox_nodes() -> Iterable[ast.AST]:
    for node in _tree("sandbox").body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in WP7_SANDBOX_SYMBOLS:
            yield node


@pytest.mark.parametrize("module", ["materialization", "docker"])
def test_new_wp7_modules_have_no_legacy_profile_policy_service_or_driver_dependency(
    module: str,
) -> None:
    forbidden = {
        "breadboard.rl.harness.profiles",
        "breadboard.rl.harness.policy",
        "breadboard.rl.harness.service",
        "breadboard.sandbox_driver",
        "breadboard.sandbox_factory",
        "breadboard.sandbox",
        "breadboard.sandbox_docker",
    }

    imported = _imports(_tree(module))

    assert imported.isdisjoint(forbidden), sorted(imported & forbidden)


def test_plan_driven_sandbox_symbols_cannot_reach_legacy_ray_or_fallback_authority() -> None:
    forbidden_names = {
        "create_sandbox",
        "SandboxLaunchSpec",
        "SandboxLease",
        "SandboxLeaseManager",
        "_ensure_ray",
        "_ray_get",
        "ray",
        "tempfile",
        "profiles",
        "policy",
        "service",
    }
    violations: list[tuple[str, int, str]] = []
    for owner in _wp7_sandbox_nodes():
        for node in ast.walk(owner):
            if isinstance(node, (ast.Name, ast.Attribute)):
                name = _qualified_name(node)
                if name is not None and (
                    name in forbidden_names
                    or name.split(".", 1)[0] in forbidden_names
                ):
                    violations.append((owner.name, node.lineno, name))

    assert violations == []


@pytest.mark.parametrize("module", ["materialization", "docker"])
def test_wp7_new_modules_do_not_consult_ambient_environment_or_process_globals(
    module: str,
) -> None:
    forbidden_calls = {
        "os.getenv",
        "os.putenv",
        "os.unsetenv",
        "Path.cwd",
        "Path.home",
        "tempfile.gettempdir",
        "subprocess.run",
        "subprocess.Popen",
    }
    forbidden_attributes = {"os.environ"}
    calls: list[tuple[int, str]] = []
    attributes: list[tuple[int, str]] = []
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Call):
            name = _qualified_name(node.func)
            if name in forbidden_calls:
                calls.append((node.lineno, name))
        elif isinstance(node, ast.Attribute):
            name = _qualified_name(node)
            if name in forbidden_attributes:
                attributes.append((node.lineno, name))

    assert calls == []
    assert attributes == []


def test_plan_driven_sandbox_symbols_do_not_read_or_mutate_ambient_environment() -> None:
    violations: list[tuple[str, int, str]] = []
    for owner in _wp7_sandbox_nodes():
        for node in ast.walk(owner):
            if isinstance(node, ast.Attribute):
                name = _qualified_name(node)
                if name == "os.environ":
                    violations.append((owner.name, node.lineno, name))
            elif isinstance(node, ast.Call):
                name = _qualified_name(node.func)
                if name in {"os.getenv", "os.putenv", "os.unsetenv"}:
                    violations.append((owner.name, node.lineno, name))

    assert violations == []


@pytest.mark.parametrize("module", ["materialization", "docker"])
def test_wp7_new_modules_define_no_mutable_module_registry(module: str) -> None:
    mutable_globals: list[tuple[int, str]] = []
    for node in _tree(module).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, (ast.Dict, ast.List, ast.Set)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                mutable_globals.extend(
                    (node.lineno, name)
                    for target in targets
                    if (name := _qualified_name(target) or "<unknown>") != "__all__"
                )

    assert mutable_globals == []


def test_workspace_descriptor_helpers_preserve_no_follow_bounded_io_authority() -> None:
    tree = _tree("sandbox")
    open_directory = _function(tree, "_open_directory_at")
    open_parent = _function(tree, "_open_parent_descriptor")
    bounded_read = _function(tree, "_bounded_regular_read")
    atomic_write = _function(tree, "_atomic_regular_write")
    descriptor_list = _function(tree, "_descriptor_list")

    directory_attributes = _attributes(open_directory)
    assert any(
        _has_keyword(call, "dir_fd") for call in _calls(open_directory, "os.open")
    )
    assert any(
        _has_keyword(call, "dir_fd") for call in _calls(open_directory, "os.mkdir")
    )
    assert {"os.O_DIRECTORY", "os.O_NOFOLLOW"} <= directory_attributes

    parent_attributes = _attributes(open_parent)
    assert _calls(open_parent, "os.open")
    assert _calls(open_parent, "_open_directory_at")
    assert {"os.O_DIRECTORY", "os.O_NOFOLLOW"} <= parent_attributes

    assert _missing_descriptor_read_operations(bounded_read) == set()
    assert _missing_descriptor_write_operations(atomic_write) == set()
    assert _missing_descriptor_list_operations(descriptor_list) == set()


def test_public_workspace_and_verifier_io_cannot_bypass_descriptor_helpers() -> None:
    tree = _tree("sandbox")
    owners = {
        "LeaseBackedRunnerWorkspace.read_text": (
            _method(tree, "LeaseBackedRunnerWorkspace", "read_text"),
            "_bounded_regular_read",
        ),
        "LeaseBackedRunnerWorkspace.write_text": (
            _method(tree, "LeaseBackedRunnerWorkspace", "write_text"),
            "_atomic_regular_write",
        ),
        "VerifierWorkspaceLease._execute_active": (
            _method(tree, "VerifierWorkspaceLease", "_execute_active"),
            "_bounded_regular_read",
        ),
        "LeaseBackedRunnerWorkspace.list_files": (
            _method(tree, "LeaseBackedRunnerWorkspace", "list_files"),
            "_descriptor_list",
        ),
    }
    violations: list[tuple[str, str, tuple[int, ...]]] = []
    for name, (owner, helper) in owners.items():
        direct_reads = tuple(_direct_read_bytes_lines(owner))
        if not _references(owner, helper) or direct_reads:
            violations.append((name, helper, direct_reads))

    assert violations == []


def test_security_file_readers_never_use_unbounded_path_read_bytes() -> None:
    security_read_owners = (
        ("materialization", "FilesystemMaterializationStore", "_read_record"),
        ("materialization", "FilesystemMaterializationStore", "_verify_tree"),
        ("materialization", "FilesystemMaterializationStore", "_materialize_locked"),
        ("materialization", "FilesystemMaterializationStore", "seal_snapshot"),
        ("sandbox", "SandboxRuntimeManager", "_read_lease_record"),
        ("docker", "DockerSandboxBackend", "_security_profile"),
    )
    violations = [
        (module, class_name, method_name, line)
        for module, class_name, method_name in security_read_owners
        for line in _direct_read_bytes_lines(
            _method(_tree(module), class_name, method_name)
        )
    ]

    assert violations == []


def test_descriptor_guard_predicates_reject_synthetic_path_based_io() -> None:
    unsafe = ast.parse(
        """
def unsafe_reader(root, logical_path):
    return (root / logical_path).read_bytes()

def unsafe_writer(root, logical_path, payload):
    (root / logical_path).write_bytes(payload)

def unsafe_lister(root, logical_path):
    return list(os.walk(root / logical_path))
"""
    )
    reader = _function(unsafe, "unsafe_reader")
    writer = _function(unsafe, "unsafe_writer")
    lister = _function(unsafe, "unsafe_lister")

    assert _direct_read_bytes_lines(reader) == [3]
    assert _missing_descriptor_read_operations(reader) == {
        "parent_descriptor",
        "open_dir_fd",
        "nofollow",
        "fstat",
        "bounded_pread",
    }
    assert _missing_descriptor_write_operations(writer) == {
        "parent_descriptor",
        "nofollow",
        "nofollow_stat",
        "exclusive_open",
        "descriptor_replace",
        "descriptor_cleanup",
    }
    assert _missing_descriptor_list_operations(lister) == {
        "parent_descriptor",
        "descriptor_scandir",
        "nofollow_stat",
        "directory_open",
        "identity_fstat",
        "bounded_output",
        "no_path_walk",
    }


def test_trusted_process_launch_uses_pinned_fd_and_cannot_resume_before_recording() -> None:
    tree = _tree("sandbox")
    run = _method(tree, "TrustedProcessHandle", "_run_pinned_argv")
    creators = [
        node
        for node in ast.walk(run)
        if isinstance(node, ast.Call)
        and _qualified_name(node.func) == "asyncio.create_subprocess_exec"
    ]
    assert len(creators) == 1
    creator = creators[0]
    keywords = {keyword.arg: keyword.value for keyword in creator.keywords}
    assert {"executable", "pass_fds"} <= keywords.keys()
    assert _qualified_name(keywords["executable"]) == "self._executable.proc_fd_path"
    pass_fds = ast.unparse(keywords["pass_fds"])
    assert "self._executable" in pass_fds
    assert "self._command_executable" in pass_fds
    assert "self._workspace_fd" in pass_fds
    assert "write_fd" not in pass_fds
    assert _qualified_name(keywords["executable"]) != "self.plan.runtime.executable_path"

    calls = [
        (node.lineno, _qualified_name(node.func))
        for node in ast.walk(run)
        if isinstance(node, ast.Call)
    ]
    create_line = min(
        line for line, name in calls if name == "asyncio.create_subprocess_exec"
    )
    barrier_line = min(
        line for line, name in calls if name == "process.stdout.readexactly"
    )
    stopped_line = min(line for line, name in calls if name == "self._proc_fields")
    recorder_line = min(line for line, name in calls if name == "recorder")
    resume_line = min(line for line, name in calls if name == "os.kill")
    drain_line = min(
        line for line, name in calls if name == "self._cleanup_process_shielded"
    )
    assert create_line < barrier_line < stopped_line < recorder_line < resume_line
    assert create_line < drain_line

    bootstrap = next(
        node.value.value
        for node in ast.walk(run)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "bootstrap"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )
    literals = bootstrap
    assert "printf B" in literals
    assert "kill -STOP $$" in literals
    assert 'exec "$@"' in literals


def test_all_trusted_process_effect_paths_delegate_to_identity_gated_run_argv() -> None:
    tree = _tree("sandbox")
    handle = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "TrustedProcessHandle"
    )
    process_creators = [
        (owner.name, node.lineno)
        for owner in handle.body
        if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and _qualified_name(node.func) == "asyncio.create_subprocess_exec"
    ]
    assert [owner for owner, _ in process_creators] == ["_run_pinned_argv"]

    run_shell = _method(tree, "TrustedProcessHandle", "run_shell")
    shell_delegates = _calls(run_shell, "self._run_pinned_argv")
    assert len(shell_delegates) == 1
    shell_delegate = shell_delegates[0]
    assert len(shell_delegate.args) == 1
    workload_argv = shell_delegate.args[0]
    assert isinstance(workload_argv, ast.Tuple)
    assert len(workload_argv.elts) == 3
    assert _qualified_name(workload_argv.elts[0]) == "self._executable.proc_fd_path"
    assert isinstance(workload_argv.elts[1], ast.Constant)
    assert workload_argv.elts[1].value == "-lc"
    assert _qualified_name(workload_argv.elts[2]) == "command"
    assert all(
        _qualified_name(node) != "self.plan.runtime.executable_path"
        for node in ast.walk(run_shell)
    )

    manager = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SandboxRuntimeManager"
    )
    direct_runtime_calls = [
        (owner.name, node.lineno)
        for owner in manager.body
        if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"run_shell", "run_argv"}
    ]
    assert direct_runtime_calls
    assert all(
        isinstance(node.func, ast.Attribute) and node.func.attr == "run_argv"
        for owner in manager.body
        if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"run_shell", "run_argv"}
    )


def test_descriptor_mount_preflight_precedes_every_create_effect() -> None:
    launch = _method(_tree("docker"), "DockerSandboxBackend", "launch")
    calls = [
        (node.lineno, _qualified_name(node.func))
        for node in ast.walk(launch)
        if isinstance(node, ast.Call)
    ]
    descriptor_line = min(
        line for line, name in calls if name == "_openat2_beneath"
    )
    stage_line = min(
        line for line, name in calls if name == "self.mount_stager.stage"
    )
    preflight_line = min(
        line for line, name in calls if name == "self.adapter.preflight"
    )
    gate_line = min(
        line for line, name in calls if name == "_require_daemon_runtime_binding"
    )
    prepare_line = min(
        line for line, name in calls if name == "self.adapter.prepare"
    )
    publish_line = min(
        line for line, name in calls if name == "context.publish_prepared_identity"
    )
    start_line = min(line for line, name in calls if name == "self.adapter.start")
    assert (
        descriptor_line < preflight_line < gate_line < stage_line
        < prepare_line < publish_line < start_line
    )


def test_docker_backend_publishes_prepared_identity_before_start_or_exposure() -> None:
    launch = _method(_tree("docker"), "DockerSandboxBackend", "launch")
    ordered_calls = [
        (node.lineno, _qualified_name(node.func))
        for node in ast.walk(launch)
        if isinstance(node, ast.Call)
    ]

    prepare_line = min(
        line for line, name in ordered_calls if name == "self.adapter.prepare"
    )
    publish_line = min(
        line
        for line, name in ordered_calls
        if name == "context.publish_prepared_identity"
    )
    start_line = min(
        line for line, name in ordered_calls if name == "self.adapter.start"
    )
    inspect_line = min(
        line for line, name in ordered_calls if name == "self.adapter.inspect"
    )
    assert prepare_line < publish_line < start_line < inspect_line
    assert _references(launch, "RuntimePreparedIdentity")
