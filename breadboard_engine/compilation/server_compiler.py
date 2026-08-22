"""Deterministic, manifest-only agent configuration compiler.

This module deliberately has no path-based entrypoint.  The only byte authority is
``ManifestReader`` and every non-root read is matched to one exact closure edge.
The resulting object is data IR; it neither admits nor instantiates capabilities.
"""
from __future__ import annotations


import json
import enum
import math
import re
import unicodedata
import sys
import types
from decimal import Decimal, InvalidOperation
from fnmatch import fnmatchcase
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Final

import yaml
from yaml.events import AliasEvent, MappingEndEvent, MappingStartEvent, SequenceEndEvent, SequenceStartEvent, ScalarEvent
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AnchorToken, AliasToken, TagToken
import jinja2
from jinja2 import StrictUndefined, meta, nodes
from jinja2.sandbox import SandboxedEnvironment

from agentic_coder_prototype.compilation.bundle import ManifestReader
from agentic_coder_prototype.compilation import contracts as _contracts_module
from agentic_coder_prototype.compilation.contracts import (
    AGENT_CONFIG_SCHEMA_ID,
    CANONICALIZER_ID,
    COMPILED_CONFIG_MANIFEST_SCHEMA_ID,
    COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
    COMPILER_INPUT_SCHEMA_ID,
    CONFIG_NODE_ID_SCHEMA_ID,
    JCS_SAFE_INTEGER_MAX,
    JCS_SAFE_INTEGER_MIN,
    PROMPT_VARIANT_ID_SCHEMA_ID,
    SERVER_CONFIG_COMPILER_ID,
    V1_SHADOW_TRANSLATOR_ID,
    BundleError,
    BundleIntegrityError,
    BundleLimitError,
    CanonicalJSONError,
    CompileDiagnostics,
    CompileErrorCode,
    CompileInputIdentity,
    CompileOptions,
    CompileStage,
    CompiledConfig,
    CompiledConfigManifest,
    CompilerIdentity,
    ConfigCompileError,
    DefaultRecord,
    DependencyClosureManifest,
    DependencyEdge,
    FieldProvenance,
    LossRecord,
    NoticeRecord,
    ProvenanceContribution,
    SourceDependency,
    UndeclaredMemberError,
    bytes_sha256,
    canonical_json_bytes,
    canonical_sha256,
)

COMPILER_VERSION: Final = "1.1.0"
BUILTIN_TOOL_RENDERER_ID: Final = "breadboard.tool-catalog.v1"
V1_MAPPING_TABLE: Final[dict[str, str]] = {
    "/model": "/providers/default_model",
    "/prompt/mode": "/prompts/tool_prompt_mode",
    "/tools/defs_dir": "/tools/registry/paths/0",
    "/tools/enabled": "/tools/registry/include",
    "/max_iterations": "/loop/max_iterations",
    "/resume": "/long_running/resume",
    "/claude": "/provider_tools/anthropic",
    "/logging": "/observability/logging",
}
V1_MAPPING_TABLE_DIGEST: Final = canonical_sha256(
    {"schema": "bb.v1-shadow-mapping.v1", "mapping": V1_MAPPING_TABLE}
)


_JSON_NUMBER_RE = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z"
)
_YAML_EXOTIC_RE = re.compile(
    r"(?:yes|no|on|off|y|n|\.nan|[-+]?\.inf|[-+]?0x[0-9a-f]+|"
    r"[-+]?0o[0-7]+|[-+]?0b[01]+|[-+]?0[0-9]+|"
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[tT ][^ ]*)?|"
    r"[-+]?[0-9][0-9_]*|[-+]?[0-9]+:[0-9:]+)\Z",
    re.IGNORECASE,
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

# Fixed compiler-policy bounds are part of the semantic ABI, not caller knobs.
MAX_DOCUMENT_BYTES: Final = 8 * 1024 * 1024
MAX_DOCUMENT_DEPTH: Final = 128
MAX_DOCUMENT_NODES: Final = 100_000
MAX_MERGE_DEPTH: Final = 64
MAX_MERGE_NODES: Final = 200_000
MAX_MERGED_BYTES: Final = 16 * 1024 * 1024
MAX_PROMPT_TEMPLATE_BYTES: Final = 64 * 1024
MAX_PROMPT_TEMPLATE_NODES: Final = 2_048
MAX_PROMPT_RENDER_CONTEXT_BYTES: Final = 512 * 1024
MAX_PROMPT_RENDER_OUTPUT_BYTES: Final = 1024 * 1024
MAX_PROMPT_RENDER_TOOLS: Final = 128

_TOP_LEVEL_FIELDS: Final = {
    "version", "extends", "profile", "workspace", "providers",
    "provider_tools", "prompts", "tools", "tool_packs", "tool_bindings",
    "terminal_sessions", "modes", "loop", "turn_strategy", "features",
    "completion", "concurrency", "permissions", "guardrails",
    "enhanced_tools", "plugins", "multi_agent", "task_tool", "replay",
    "long_running", "resume", "logging", "telemetry", "sampling",
    "optimizer_mutable_pointers", "sandbox", "setup",
    "_v1_translation",
}

# Closed keys for families whose values drive source resolution or core IR.
_FAMILY_FIELDS: Final[dict[str, set[str]]] = {
    "profile": {"name", "description", "version", "metadata"},
    "workspace": {"root", "mirror", "sandbox", "driver", "options", "mounts", "network", "image", "resources"},
    "providers": {"default_model", "models", "routing", "provider_tools"},
    "prompts": {"tool_prompt_mode", "environment", "synthesis", "tool_prompt_synthesis", "packs", "injection", "dialects", "dedupe", "templates", "tool_catalog"},
    "tools": {"registry", "overlays", "aliases", "dialects", "mark_task_complete", "bindings", "packs"},
    "plugins": {"enabled", "manifest_refs", "trust_requests", "untrusted_hook_tools"},
    "guardrails": {"include", "definitions", "guards", "overrides", "plan_bootstrap"},
    "multi_agent": {"enabled", "team_config", "team", "coordination", "bus", "workspace_sharing", "event_log_path", "max_concurrent_agents", "scheduler", "async", "spawn_tool"},
    "task_tool": {"id", "description_template_path", "description_template", "subagents", "render_context"},
    "sampling": {"temperature"},
}

_DEFAULT_OBJECT_FAMILIES: Final = (
    "turn_strategy", "features", "completion", "concurrency", "permissions",
    "enhanced_tools", "replay", "long_running", "terminal_sessions",
)
_PROVIDER_PARAM_FIELDS: Final[dict[str, set[str]]] = {
    "openai": {"temperature", "top_p", "max_tokens", "max_output_tokens", "seed", "stop", "stream", "timeout_ms", "parallel_tool_calls", "response_format", "tool_choice", "reasoning_effort", "verbosity", "frequency_penalty", "presence_penalty"},
    "openai_responses": {"temperature", "top_p", "max_output_tokens", "seed", "stream", "timeout_ms", "parallel_tool_calls", "response_format", "tool_choice", "reasoning", "reasoning_effort", "verbosity"},
    "responses": {"temperature", "top_p", "max_output_tokens", "seed", "stream", "timeout_ms", "parallel_tool_calls", "response_format", "tool_choice", "reasoning", "reasoning_effort", "verbosity"},
    "anthropic": {"temperature", "top_p", "top_k", "max_tokens", "max_output_tokens", "stop_sequences", "stream", "timeout_ms", "tool_choice"},
    "test": {"temperature", "top_p", "max_tokens", "max_output_tokens", "seed", "stream", "timeout_ms"},
}

# Schema and semantic policy versions are explicit identity inputs. Executable
# compiler authority is derived separately from the live in-memory code objects.
_CONFIG_SCHEMA_DESCRIPTOR: Final = {
    "schema_id": AGENT_CONFIG_SCHEMA_ID,
    "version": 2,
    "strict_scalars": "json-data-model-v1",
    "merge": "breadboard-recursive-merge-v1",
    "top_level_fields": sorted(_TOP_LEVEL_FIELDS),
    "family_fields": {
        family: sorted(fields) for family, fields in sorted(_FAMILY_FIELDS.items())
    },
    "provider_parameter_fields": {
        adapter: sorted(fields)
        for adapter, fields in sorted(_PROVIDER_PARAM_FIELDS.items())
    },
    "v1_mapping_digest": V1_MAPPING_TABLE_DIGEST,
}
_MANIFEST_SCHEMA_DESCRIPTOR: Final = {
    "schema_id": COMPILED_CONFIG_MANIFEST_SCHEMA_ID,
    "version": 1,
    "semantic_schema": COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
    "compiler_input_schema": COMPILER_INPUT_SCHEMA_ID,
    "config_node_schema": CONFIG_NODE_ID_SCHEMA_ID,
    "prompt_variant_schema": PROMPT_VARIANT_ID_SCHEMA_ID,
}
_COMPILER_POLICY_DESCRIPTOR: Final = {
    "compiler_id": SERVER_CONFIG_COMPILER_ID,
    "version": COMPILER_VERSION,
    "pipeline": "manifest-only-closure-exact-v3",
    "parser": "strict-json-yaml-bounded-v2",
    "merge": "breadboard-recursive-merge-bounded-v2",
    "provenance": "field-provenance-complete-v2",
    "semantic_identity": "source-independent-content-v2",
    "authority_policy": "closed-recursive-authority-v2",
    "v1_translator": V1_SHADOW_TRANSLATOR_ID,
    "prompt_renderer": "breadboard.prompt-assembly.v1",
    "tool_renderer": BUILTIN_TOOL_RENDERER_ID,
    "template_renderer": "jinja2-sandboxed-allowlist-v1",
    "resource_limits": {
        "max_document_bytes": MAX_DOCUMENT_BYTES,
        "max_document_depth": MAX_DOCUMENT_DEPTH,
        "max_document_nodes": MAX_DOCUMENT_NODES,
        "max_merge_depth": MAX_MERGE_DEPTH,
        "max_merge_nodes": MAX_MERGE_NODES,
        "max_merged_bytes": MAX_MERGED_BYTES,
    },
    "config_schema": _CONFIG_SCHEMA_DESCRIPTOR,
    "manifest_schema": _MANIFEST_SCHEMA_DESCRIPTOR,
}
CONFIG_SCHEMA_DIGEST: Final = canonical_sha256(_CONFIG_SCHEMA_DESCRIPTOR)
MANIFEST_SCHEMA_DIGEST: Final = canonical_sha256(_MANIFEST_SCHEMA_DESCRIPTOR)
# COMPILER_CODE_DIGEST is assigned after all compiler functions are defined.


def _reject_embedded_authority(value: Any, pointer: str) -> None:
    forbidden_exact = {
        "url", "uri", "base_url", "endpoint", "address", "host", "port",
        "socket", "file", "filename", "filepath", "path", "api_key", "token",
        "headers", "authorization", "auth", "credential", "secret", "password",
        "command", "cmd", "shell", "argv", "args", "exec", "executable",
        "module", "import", "environment", "env", "fallback", "fallbacks",
        "fallback_chain", "fallback_chains",
    }
    forbidden_fragments = (
        "credential", "authorization", "api_key", "access_token", "secret",
        "password", "socket", "address", "command", "executable", "fallback",
    )
    if type(value) is dict:
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_")
            if normalized in forbidden_exact or any(part in normalized for part in forbidden_fragments):
                raise _error(
                    CompileStage.SCHEMA,
                    CompileErrorCode.FORBIDDEN_AUTHORITY,
                    instance_pointer=_pointer(pointer, key),
                    details={"field": key},
                )
            _reject_embedded_authority(item, _pointer(pointer, key))
    elif type(value) is list:
        for index, item in enumerate(value):
            _reject_embedded_authority(item, _pointer(pointer, index))
    elif type(value) is str and (
        "://" in value
        or value.startswith(("${", "$", "env:", "file:", "~/", "/", "\\\\", "ssh:"))
        or re.match(r"^[A-Za-z]:[\\/]", value) is not None
        or re.search(r"(?:^|[;&|])\s*(?:sh|bash|zsh|cmd|powershell)(?:\s|$)|\s-c(?:\s|$)", value, re.IGNORECASE) is not None
    ):
        raise _error(
            CompileStage.SCHEMA,
            CompileErrorCode.FORBIDDEN_AUTHORITY,
            instance_pointer=pointer,
        )




@dataclass(frozen=True, slots=True)
class _ParsedDocument:
    logical_path: str
    value: dict[str, Any]


@dataclass
class _ReadLedger:
    reader: ManifestReader
    closure: DependencyClosureManifest

    def __post_init__(self) -> None:
        self._members = {member.logical_path: member for member in self.closure.members}
        self._edge_index: dict[tuple[str, str, str], list[DependencyEdge]] = {}
        for edge in self.closure.edges:
            self._edge_index.setdefault((edge.from_path, edge.kind, edge.raw_ref), []).append(edge)
        self._used_edges: set[tuple[str, str, int, str, str]] = set()
        self._payloads: dict[str, bytes] = {}
        self._dependencies: dict[tuple[str, str, str, str], SourceDependency] = {}

    @staticmethod
    def _edge_key(edge: DependencyEdge) -> tuple[str, str, int, str, str]:
        return (edge.from_path, edge.kind, edge.ordinal, edge.raw_ref, edge.logical_path)

    def _read(self, logical_path: str) -> bytes:
        if logical_path in self._payloads:
            return self._payloads[logical_path]
        try:
            payload = self.reader.read_bytes(logical_path)
        except UndeclaredMemberError as exc:
            raise _error(
                CompileStage.READER_INTEGRITY,
                CompileErrorCode.SOURCE_UNDECLARED,
                logical_path=logical_path,
            ) from exc
        except BundleLimitError as exc:
            raise _error(
                CompileStage.READER_INTEGRITY,
                CompileErrorCode.SOURCE_LIMIT_EXCEEDED,
                logical_path=logical_path,
            ) from exc
        except (BundleIntegrityError, BundleError, KeyError, FileNotFoundError) as exc:
            raise _error(
                CompileStage.READER_INTEGRITY,
                CompileErrorCode.SOURCE_INTEGRITY,
                logical_path=logical_path,
            ) from exc
        member = self._members.get(logical_path)
        if (
            member is None
            or type(payload) is not bytes
            or len(payload) != member.size_bytes
            or bytes_sha256(payload) != member.blob_digest
        ):
            raise _error(
                CompileStage.READER_INTEGRITY,
                CompileErrorCode.SOURCE_INTEGRITY,
                logical_path=logical_path,
                details={"reason": "reader_closure_mismatch"},
            )
        self._payloads[logical_path] = payload
        return payload

    def read_root(self) -> bytes:
        path = self.closure.root_entrypoint
        member = self._members[path]
        payload = self._read(path)
        dep = SourceDependency(
            dependency_kind="config_entrypoint",
            from_logical_path=None,
            raw_reference=None,
            logical_path=path,
            blob_digest=member.blob_digest,
            size_bytes=member.size_bytes,
            media_type=member.media_type,
        )
        self._dependencies[dep.sort_key] = dep
        return payload

    def resolve_one(
        self, from_path: str, kind: str, raw_ref: str
    ) -> tuple[str, bytes, SourceDependency]:
        matches = sorted(
            self._edge_index.get((from_path, kind, raw_ref), []),
            key=lambda item: (item.ordinal, item.logical_path),
        )
        if not matches:
            raise _error(
                CompileStage.DEPENDENCY_RESOLUTION,
                CompileErrorCode.REFERENCE_UNDECLARED,
                logical_path=from_path,
                dependency_kind=kind,
                raw_reference=raw_ref,
            )
        targets = {edge.logical_path for edge in matches}
        if len(targets) != 1:
            raise _error(
                CompileStage.DEPENDENCY_RESOLUTION,
                CompileErrorCode.REFERENCE_AMBIGUOUS,
                logical_path=from_path,
                dependency_kind=kind,
                raw_reference=raw_ref,
                related_logical_paths=tuple(sorted(targets)),
            )
        edge = next(
            (
                candidate
                for candidate in matches
                if self._edge_key(candidate) not in self._used_edges
            ),
            None,
        )
        if edge is None:
            raise _error(
                CompileStage.DEPENDENCY_RESOLUTION,
                CompileErrorCode.REFERENCE_UNDECLARED,
                logical_path=from_path,
                dependency_kind=kind,
                raw_reference=raw_ref,
                details={"reason": "reference_occurrence_has_no_unused_edge"},
            )
        payload = self._read(edge.logical_path)
        edge_key = self._edge_key(edge)
        if edge_key not in self._used_edges:
            self._used_edges.add(edge_key)
            self._record_edge(edge)
        return edge.logical_path, payload, self._dependency_from_edge(edge)

    def resolve_directory(
        self, from_path: str, kind: str, raw_ref: str
    ) -> tuple[tuple[str, bytes, SourceDependency], ...]:
        matches = self._edge_index.get((from_path, kind, raw_ref), [])
        if not matches:
            raise _error(
                CompileStage.REFERENCE_RESOLUTION,
                CompileErrorCode.REFERENCE_UNDECLARED,
                logical_path=from_path,
                dependency_kind=kind,
                raw_reference=raw_ref,
            )
        result: list[tuple[str, bytes, SourceDependency]] = []
        for edge in sorted(matches, key=lambda item: (item.ordinal, item.logical_path)):
            key = self._edge_key(edge)
            if key in self._used_edges:
                continue
            payload = self._read(edge.logical_path)
            self._used_edges.add(key)
            self._record_edge(edge)
            result.append(
                (edge.logical_path, payload, self._dependency_from_edge(edge))
            )
        return tuple(result)

    def _dependency_from_edge(self, edge: DependencyEdge) -> SourceDependency:
        member = self._members[edge.logical_path]
        return SourceDependency(
            dependency_kind=edge.kind,
            from_logical_path=edge.from_path,
            raw_reference=edge.raw_ref,
            logical_path=edge.logical_path,
            blob_digest=member.blob_digest,
            size_bytes=member.size_bytes,
            media_type=member.media_type,
        )

    def _record_edge(self, edge: DependencyEdge) -> None:
        dep = self._dependency_from_edge(edge)
        self._dependencies[dep.sort_key] = dep

    def dependency_for(self, logical_path: str) -> SourceDependency:
        values = [dep for dep in self._dependencies.values() if dep.logical_path == logical_path]
        if not values:
            member = self._members[logical_path]
            return SourceDependency(
                dependency_kind="config_entrypoint",
                from_logical_path=None,
                raw_reference=None,
                logical_path=logical_path,
                blob_digest=member.blob_digest,
                size_bytes=member.size_bytes,
                media_type=member.media_type,
            )
        return sorted(values, key=lambda item: item.sort_key)[0]

    def finish(self) -> tuple[SourceDependency, ...]:
        unused = [
            edge.logical_path
            for edge in self.closure.edges
            if self._edge_key(edge) not in self._used_edges
        ]
        if unused:
            raise _error(
                CompileStage.DEPENDENCY_RESOLUTION,
                CompileErrorCode.CLOSURE_MISMATCH,
                related_logical_paths=tuple(sorted(set(unused))),
                details={"reason": "unconsumed_dependency_edges"},
            )
        represented = {dep.logical_path for dep in self._dependencies.values()}
        missing = sorted(set(self._members) - represented)
        if missing:
            raise _error(
                CompileStage.DEPENDENCY_RESOLUTION,
                CompileErrorCode.CLOSURE_MISMATCH,
                related_logical_paths=tuple(missing),
                details={"reason": "unconsumed_closure_members"},
            )
        return tuple(sorted(self._dependencies.values(), key=lambda item: item.sort_key))


def _error(
    stage: CompileStage,
    code: CompileErrorCode,
    *,
    logical_path: str | None = None,
    instance_pointer: str | None = None,
    dependency_kind: str | None = None,
    raw_reference: str | None = None,
    related_logical_paths: tuple[str, ...] = (),
    details: Mapping[str, Any] | None = None,
) -> ConfigCompileError:
    return ConfigCompileError(
        stage=stage,
        code=code,
        logical_path=logical_path,
        instance_pointer=instance_pointer,
        dependency_kind=dependency_kind,
        raw_reference=raw_reference,
        related_logical_paths=tuple(sorted(set(related_logical_paths))),
        details=dict(details or {}),
    )

def _implementation_value(value: Any) -> Any:
    if isinstance(value, types.CodeType):
        return _code_object(value)
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if type(value) is bytes:
        return {"bytes_hex": value.hex()}
    if isinstance(value, re.Pattern):
        return {"regex": value.pattern, "flags": value.flags}
    if isinstance(value, enum.Enum):
        return {
            "enum": f"{type(value).__module__}.{type(value).__qualname__}",
            "member": value.name,
            "value": _implementation_value(value.value),
        }
    if isinstance(value, enum.EnumMeta):
        return {
            "enum": f"{value.__module__}.{value.__qualname__}",
            "members": {
                name: _implementation_value(member.value)
                for name, member in sorted(value.__members__.items())
            },
        }
    if isinstance(value, types.FunctionType):
        return {
            "function": f"{value.__module__}.{value.__qualname__}",
            "code": _code_object(value.__code__),
            "defaults": _implementation_value(value.__defaults__),
            "kwdefaults": _implementation_value(value.__kwdefaults__),
            "closure": [_implementation_value(cell.cell_contents) for cell in (value.__closure__ or ())],
        }
    if isinstance(value, type):
        return {"type": f"{value.__module__}.{value.__qualname__}"}
    if isinstance(value, types.ModuleType):
        result: dict[str, Any] = {"module": value.__name__}
        version = getattr(value, "__version__", None)
        if type(version) is str:
            result["version"] = version
        return result
    if isinstance(value, Mapping):
        return {str(key): _implementation_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if type(value) in {tuple, list}:
        return [_implementation_value(item) for item in value]
    if type(value) in {set, frozenset}:
        items = [_implementation_value(item) for item in value]
        return sorted(items, key=canonical_json_bytes)
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}"}


def _code_constant(value: Any) -> Any:
    return _implementation_value(value)


def _code_object(code: types.CodeType) -> dict[str, Any]:
    return {
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "flags": code.co_flags,
        "bytecode": code.co_code.hex(),
        "constants": [_code_constant(item) for item in code.co_consts],
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
    }


def _code_names(code: types.CodeType) -> set[str]:
    names = set(code.co_names)
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            names.update(_code_names(constant))
    return names


def _function_inventory(function: types.FunctionType) -> dict[str, Any]:
    return _implementation_value(function)


def _module_code_inventory(namespace: Mapping[str, Any], module_name: str) -> dict[str, Any]:
    inventory: dict[str, Any] = {}
    referenced_globals: set[str] = set()

    def class_methods(value: type[Any]) -> dict[str, Any]:
        methods: dict[str, Any] = {}
        for method_name, method in sorted(vars(value).items()):
            function = method.__func__ if isinstance(method, (staticmethod, classmethod)) else method
            if isinstance(function, types.FunctionType):
                methods[method_name] = _function_inventory(function)
                referenced_globals.update(_code_names(function.__code__))
            elif isinstance(method, property):
                methods[method_name] = {accessor: _function_inventory(accessor_function) for accessor, accessor_function in (("get", method.fget), ("set", method.fset), ("delete", method.fdel)) if accessor_function is not None}
                for accessor_function in (method.fget, method.fset, method.fdel):
                    if accessor_function is not None:
                        referenced_globals.update(_code_names(accessor_function.__code__))
        return methods

    for name, value in sorted(namespace.items()):
        if isinstance(value, types.FunctionType) and value.__module__ == module_name:
            inventory[name] = _function_inventory(value)
            referenced_globals.update(_code_names(value.__code__))
        elif isinstance(value, enum.EnumMeta) and value.__module__ == module_name:
            inventory[name] = {"definition": _implementation_value(value), "methods": class_methods(value)}
        elif isinstance(value, type) and value.__module__ == module_name:
            methods = class_methods(value)
            if methods:
                inventory[name] = methods
    excluded = {"COMPILER_CODE_DIGEST", "CONFIG_SCHEMA_DIGEST", "MANIFEST_SCHEMA_DIGEST"}
    inventory["semantic_globals"] = {
        name: _implementation_value(namespace[name])
        for name in sorted(referenced_globals)
        if name in namespace and name not in excluded and not isinstance(namespace[name], types.FunctionType) and (not isinstance(namespace[name], type) or isinstance(namespace[name], enum.EnumMeta))
    }
    return inventory


def _compiler_implementation_digest() -> str:
    """Hash the loaded implementation without source, path, or ambient reads."""

    preimage = {
        "schema": "bb.compiler-implementation.v1",
        "policy": _COMPILER_POLICY_DESCRIPTOR,
        "dependencies": {
            "python_bytecode_abi": list(sys.version_info[:2]),
            "pyyaml": yaml.__version__,
            "jinja2": jinja2.__version__,
        },
        "modules": {
            __name__: _module_code_inventory(globals(), __name__),
            _contracts_module.__name__: _module_code_inventory(
                vars(_contracts_module), _contracts_module.__name__
            ),
        },
    }
    return canonical_sha256(preimage)


def _check_tree_budget(
    value: Any,
    *,
    logical_path: str,
    stage: CompileStage,
    max_depth: int,
    max_nodes: int,
) -> None:
    pending: list[tuple[Any, int]] = [(value, 0)]
    nodes_seen = 0
    while pending:
        current, depth = pending.pop()
        nodes_seen += 1
        if nodes_seen > max_nodes or depth > max_depth:
            raise _error(
                stage,
                CompileErrorCode.RESOURCE_LIMIT_EXCEEDED,
                logical_path=logical_path,
                details={"max_depth": max_depth, "max_nodes": max_nodes},
            )
        if type(current) is dict:
            pending.extend((item, depth + 1) for item in current.values())
        elif type(current) is list:
            pending.extend((item, depth + 1) for item in current)


def _check_yaml_node_budget(node: Node, *, logical_path: str) -> None:
    pending: list[tuple[Node, int]] = [(node, 0)]
    nodes_seen = 0
    while pending:
        current, depth = pending.pop()
        nodes_seen += 1
        if nodes_seen > MAX_DOCUMENT_NODES or depth > MAX_DOCUMENT_DEPTH:
            raise _error(
                CompileStage.PARSE,
                CompileErrorCode.RESOURCE_LIMIT_EXCEEDED,
                logical_path=logical_path,
                details={"max_depth": MAX_DOCUMENT_DEPTH, "max_nodes": MAX_DOCUMENT_NODES},
            )
        if isinstance(current, SequenceNode):
            pending.extend((item, depth + 1) for item in current.value)
        elif isinstance(current, MappingNode):
            for key, item in current.value:
                pending.append((item, depth + 1))
                pending.append((key, depth + 1))


def _check_merged_budget(value: dict[str, Any], *, logical_path: str) -> None:
    _check_tree_budget(
        value,
        logical_path=logical_path,
        stage=CompileStage.MERGE,
        max_depth=MAX_DOCUMENT_DEPTH,
        max_nodes=MAX_MERGE_NODES,
    )
    try:
        encoded_size = len(canonical_json_bytes(value))
    except (CanonicalJSONError, RecursionError) as exc:
        raise _error(
            CompileStage.MERGE,
            CompileErrorCode.RESOURCE_LIMIT_EXCEEDED,
            logical_path=logical_path,
        ) from exc
    if encoded_size > MAX_MERGED_BYTES:
        raise _error(
            CompileStage.MERGE,
            CompileErrorCode.RESOURCE_LIMIT_EXCEEDED,
            logical_path=logical_path,
            details={"max_merged_bytes": MAX_MERGED_BYTES},
        )


def _pointer(parent: str, key: str | int) -> str:
    token = str(key).replace("~", "~0").replace("/", "~1")
    return f"{parent}/{token}" if parent else f"/{token}"


def _validate_json_value(value: Any, *, logical_path: str, pointer: str = "") -> None:
    if value is None or type(value) in {bool, str}:
        return
    if type(value) is int:
        if not JCS_SAFE_INTEGER_MIN <= value <= JCS_SAFE_INTEGER_MAX:
            raise _error(
                CompileStage.PARSE,
                CompileErrorCode.NUMBER_OUT_OF_RANGE,
                logical_path=logical_path,
                instance_pointer=pointer or "",
            )
        return
    if type(value) is float:
        if not math.isfinite(value) or (value == 0.0 and math.copysign(1.0, value) < 0):
            raise _error(
                CompileStage.PARSE,
                CompileErrorCode.NUMBER_OUT_OF_RANGE,
                logical_path=logical_path,
                instance_pointer=pointer or "",
            )
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_json_value(item, logical_path=logical_path, pointer=_pointer(pointer, index))
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise _error(
                    CompileStage.PARSE,
                    CompileErrorCode.UNSUPPORTED_YAML_SCALAR,
                    logical_path=logical_path,
                    instance_pointer=pointer or "",
                    details={"reason": "non_string_mapping_key"},
                )
            _validate_json_value(item, logical_path=logical_path, pointer=_pointer(pointer, key))
        return
    raise _error(
        CompileStage.PARSE,
        CompileErrorCode.UNSUPPORTED_YAML_SCALAR,
        logical_path=logical_path,
        instance_pointer=pointer or "",
        details={"type": type(value).__name__},
    )


def _decimal_number(value: str, *, logical_path: str, pointer: str) -> float:
    try:
        decimal_value = Decimal(value)
        number = float(decimal_value)
    except (InvalidOperation, OverflowError, ValueError) as exc:
        raise _error(
            CompileStage.PARSE,
            CompileErrorCode.NUMBER_OUT_OF_RANGE,
            logical_path=logical_path,
            instance_pointer=pointer,
        ) from exc
    if (
        not math.isfinite(number)
        or (number == 0.0 and value.startswith("-"))
        or Decimal(str(number)) != decimal_value
    ):
        raise _error(
            CompileStage.PARSE,
            CompileErrorCode.NUMBER_OUT_OF_RANGE,
            logical_path=logical_path,
            instance_pointer=pointer,
        )
    return number


def _convert_json_decimals(value: Any, *, logical_path: str, pointer: str = "") -> Any:
    if isinstance(value, Decimal):
        return _decimal_number(str(value), logical_path=logical_path, pointer=pointer)
    if type(value) is list:
        return [
            _convert_json_decimals(item, logical_path=logical_path, pointer=_pointer(pointer, index))
            for index, item in enumerate(value)
        ]
    if type(value) is dict:
        return {
            key: _convert_json_decimals(item, logical_path=logical_path, pointer=_pointer(pointer, key))
            for key, item in value.items()
        }
    return value


def _plain_scalar(value: str, *, logical_path: str, pointer: str) -> Any:
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if _JSON_NUMBER_RE.fullmatch(value):
        if "." not in value and "e" not in value.lower():
            number = int(value)
            if not JCS_SAFE_INTEGER_MIN <= number <= JCS_SAFE_INTEGER_MAX:
                raise _error(
                    CompileStage.PARSE,
                    CompileErrorCode.NUMBER_OUT_OF_RANGE,
                    logical_path=logical_path,
                    instance_pointer=pointer,
                )
            return number
        return _decimal_number(value, logical_path=logical_path, pointer=pointer)
    if _YAML_EXOTIC_RE.fullmatch(value):
        raise _error(
            CompileStage.PARSE,
            CompileErrorCode.UNSUPPORTED_YAML_SCALAR,
            logical_path=logical_path,
            instance_pointer=pointer,
            details={"scalar": value},
        )
    return value


def _node_to_json(node: Node, *, logical_path: str, pointer: str = "") -> Any:
    if isinstance(node, ScalarNode):
        if node.tag not in {"tag:yaml.org,2002:str"}:
            raise _error(CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_TAG, logical_path=logical_path, instance_pointer=pointer)
        if node.style is not None:
            return node.value
        return _plain_scalar(node.value, logical_path=logical_path, pointer=pointer)
    if isinstance(node, SequenceNode):
        return [_node_to_json(item, logical_path=logical_path, pointer=_pointer(pointer, index)) for index, item in enumerate(node.value)]
    if isinstance(node, MappingNode):
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = _node_to_json(key_node, logical_path=logical_path, pointer=pointer)
            if type(key) is not str:
                raise _error(CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_SCALAR, logical_path=logical_path, instance_pointer=pointer, details={"reason": "non_string_mapping_key"})
            child_pointer = _pointer(pointer, key)
            if key == "<<":
                raise _error(CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_TAG, logical_path=logical_path, instance_pointer=child_pointer, details={"reason": "yaml_merge_key"})
            if key in result:
                raise _error(CompileStage.PARSE, CompileErrorCode.DUPLICATE_MAPPING_KEY, logical_path=logical_path, instance_pointer=child_pointer, details={"key": key})
            result[key] = _node_to_json(value_node, logical_path=logical_path, pointer=child_pointer)
        return result
    raise _error(CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_TAG, logical_path=logical_path, instance_pointer=pointer)


def _check_json_token_budget(text: str, *, logical_path: str) -> None:
    depth = -1
    nodes_seen = 0
    index = 0
    previous_significant = ""
    while index < len(text):
        scalar_value = False
        character = text[index]
        if character == '"':
            index += 1
            escaped = False
            while index < len(text):
                current = text[index]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    break
                index += 1
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead >= len(text) or text[lookahead] != ":":
                nodes_seen += 1
                scalar_value = True
        elif character in "[{":
            depth += 1
            nodes_seen += 1
        elif character in "]}":
            depth -= 1
        elif character in "-0123456789tfn" and previous_significant in {"", "[", "{", ",", ":"}:
            nodes_seen += 1
            scalar_value = True
        if depth > MAX_DOCUMENT_DEPTH or (scalar_value and depth >= MAX_DOCUMENT_DEPTH) or nodes_seen > MAX_DOCUMENT_NODES:
            raise _error(CompileStage.PARSE, CompileErrorCode.RESOURCE_LIMIT_EXCEEDED, logical_path=logical_path, details={"max_depth": MAX_DOCUMENT_DEPTH, "max_nodes": MAX_DOCUMENT_NODES})
        if not character.isspace():
            previous_significant = character
        index += 1


def strict_parse_payload(
    payload: bytes,
    *,
    logical_path: str,
    media_type: str = "application/yaml",
) -> dict[str, Any]:
    """Parse one reader-provided payload into the constrained JSON data model."""

    if type(payload) is not bytes or len(payload) > MAX_DOCUMENT_BYTES:
        raise _error(
            CompileStage.PARSE,
            CompileErrorCode.RESOURCE_LIMIT_EXCEEDED,
            logical_path=logical_path,
            details={"max_document_bytes": MAX_DOCUMENT_BYTES},
        )
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _error(
            CompileStage.PARSE,
            CompileErrorCode.UTF8_INVALID,
            logical_path=logical_path,
        ) from exc
    if text.startswith("\ufeff"):
        raise _error(
            CompileStage.PARSE,
            CompileErrorCode.UTF8_INVALID,
            logical_path=logical_path,
            details={"reason": "bom_forbidden"},
        )
    if media_type == "application/json":
        duplicate_key: str | None = None

        def object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            nonlocal duplicate_key
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result and duplicate_key is None:
                    duplicate_key = key
                result[key] = item
            return result

        _check_json_token_budget(text, logical_path=logical_path)
        try:
            value = json.loads(
                text,
                object_pairs_hook=object_from_pairs,
                parse_float=Decimal,
                parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
            )
        except RecursionError as exc:
            raise _error(CompileStage.PARSE, CompileErrorCode.RESOURCE_LIMIT_EXCEEDED, logical_path=logical_path) from exc
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise _error(CompileStage.PARSE, CompileErrorCode.INVALID_JSON, logical_path=logical_path) from exc
        if duplicate_key is not None:
            raise _error(CompileStage.PARSE, CompileErrorCode.DUPLICATE_MAPPING_KEY, logical_path=logical_path, details={"key": duplicate_key})
        _check_tree_budget(value, logical_path=logical_path, stage=CompileStage.PARSE, max_depth=MAX_DOCUMENT_DEPTH, max_nodes=MAX_DOCUMENT_NODES)
        value = _convert_json_decimals(value, logical_path=logical_path)
    elif media_type in {"application/yaml", "application/x-yaml", "text/yaml"}:
        try:
            for token in yaml.scan(text, Loader=yaml.BaseLoader):
                if isinstance(token, (AnchorToken, AliasToken)):
                    raise _error(
                        CompileStage.PARSE,
                        CompileErrorCode.UNSUPPORTED_YAML_TAG,
                        logical_path=logical_path,
                        details={"reason": "anchors_and_aliases_forbidden"},
                    )
                if isinstance(token, TagToken):
                    raise _error(
                        CompileStage.PARSE,
                        CompileErrorCode.UNSUPPORTED_YAML_TAG,
                        logical_path=logical_path,
                    )
            event_depth = 0
            event_nodes = 0
            for event in yaml.parse(text, Loader=yaml.BaseLoader):
                if isinstance(event, AliasEvent):
                    raise _error(CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_TAG, logical_path=logical_path)
                if isinstance(event, (MappingStartEvent, SequenceStartEvent)):
                    event_depth += 1
                    event_nodes += 1
                elif isinstance(event, (MappingEndEvent, SequenceEndEvent)):
                    event_depth = max(0, event_depth - 1)
                elif isinstance(event, ScalarEvent):
                    event_nodes += 1
                if event_depth > MAX_DOCUMENT_DEPTH or event_nodes > MAX_DOCUMENT_NODES:
                    raise _error(CompileStage.PARSE, CompileErrorCode.RESOURCE_LIMIT_EXCEEDED, logical_path=logical_path, details={"max_depth": MAX_DOCUMENT_DEPTH, "max_nodes": MAX_DOCUMENT_NODES})
            node = yaml.compose(text, Loader=yaml.BaseLoader)
        except ConfigCompileError:
            raise
        except RecursionError as exc:
            raise _error(
                CompileStage.PARSE,
                CompileErrorCode.RESOURCE_LIMIT_EXCEEDED,
                logical_path=logical_path,
            ) from exc
        except yaml.YAMLError as exc:
            raise _error(
                CompileStage.PARSE,
                CompileErrorCode.UNSUPPORTED_YAML_SCALAR,
                logical_path=logical_path,
            ) from exc
        if node is None:
            value = {}
        else:
            _check_yaml_node_budget(node, logical_path=logical_path)
            value = _node_to_json(node, logical_path=logical_path)
    else:
        raise _error(
            CompileStage.PARSE,
            CompileErrorCode.UNSUPPORTED_YAML_SCALAR,
            logical_path=logical_path,
            details={"media_type": media_type, "reason": "unsupported_media_type"},
        )
    if type(value) is not dict:
        raise _error(
            CompileStage.PARSE,
            CompileErrorCode.DOCUMENT_NOT_MAPPING,
            logical_path=logical_path,
        )
    _validate_json_value(value, logical_path=logical_path)
    return value


def _source_contribution(
    logical_path: str,
    blob_digest: str,
    source_pointer: str,
    precedence_index: int,
    action: str,
    *,
    shadowed: bool = False,
) -> ProvenanceContribution:
    return ProvenanceContribution(
        origin_kind="source",
        logical_path=logical_path,
        blob_digest=blob_digest,
        source_pointer=source_pointer,
        dependency_kind=None,
        precedence_index=precedence_index,
        action=action,
        shadowed=shadowed,
    )


def _walk_leaf_pointers(value: Any, pointer: str = "") -> list[str]:
    if type(value) is dict and value:
        result: list[str] = []
        for key, item in value.items():
            result.extend(_walk_leaf_pointers(item, _pointer(pointer, key)))
        return result
    if type(value) is list and value:
        result = []
        for index, item in enumerate(value):
            result.extend(_walk_leaf_pointers(item, _pointer(pointer, index)))
        return result
    return [pointer or ""]


def deep_merge_with_provenance(
    base: dict[str, Any],
    override: dict[str, Any],
    *,
    logical_path: str,
    blob_digest: str,
    precedence_index: int,
    provenance: dict[str, list[ProvenanceContribution]],
    pointer: str = "",
) -> dict[str, Any]:
    """Apply compiler merge ABI v1 and retain every field contributor."""

    out = deepcopy(base)
    for key, value in override.items():
        target = _pointer(pointer, key)
        source_pointer = target
        if key not in out:
            out[key] = deepcopy(value)
            for leaf in _walk_leaf_pointers(value, target):
                provenance.setdefault(leaf, []).append(
                    _source_contribution(
                        logical_path, blob_digest, leaf, precedence_index, "set"
                    )
                )
            continue
        prior = out[key]
        if type(prior) is dict and type(value) is dict:
            if not value:
                provenance.setdefault(target, []).append(
                    _source_contribution(
                        logical_path, blob_digest, source_pointer,
                        precedence_index, "merge_noop", shadowed=True
                    )
                )
                continue
            out[key] = deep_merge_with_provenance(
                prior,
                value,
                logical_path=logical_path,
                blob_digest=blob_digest,
                precedence_index=precedence_index,
                provenance=provenance,
                pointer=target,
            )
            continue
        for prior_pointer in tuple(provenance):
            if prior_pointer == target or prior_pointer.startswith(target + "/"):
                provenance[prior_pointer] = _shadow_contributions(provenance[prior_pointer])
        for leaf in _walk_leaf_pointers(value, target):
            existing = provenance.get(leaf, [])
            provenance[leaf] = [
                ProvenanceContribution(
                    origin_kind=item.origin_kind,
                    logical_path=item.logical_path,
                    blob_digest=item.blob_digest,
                    source_pointer=item.source_pointer,
                    dependency_kind=item.dependency_kind,
                    precedence_index=item.precedence_index,
                    action=item.action,
                    shadowed=True,
                )
                for item in existing
            ]
            provenance[leaf].append(
                _source_contribution(
                    logical_path, blob_digest, leaf, precedence_index, "replace"
                )
            )
        out[key] = deepcopy(value)
    return out


def _deep_merge_values(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge already-resolved mappings without manufacturing provenance."""

    result = deepcopy(base)
    for key, value in override.items():
        if key in result and type(result[key]) is dict and type(value) is dict:
            if value:
                result[key] = _deep_merge_values(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _shadow_contributions(
    values: Sequence[ProvenanceContribution],
) -> list[ProvenanceContribution]:
    return [
        ProvenanceContribution(
            origin_kind=item.origin_kind,
            logical_path=item.logical_path,
            blob_digest=item.blob_digest,
            source_pointer=item.source_pointer,
            dependency_kind=item.dependency_kind,
            precedence_index=item.precedence_index,
            action=item.action,
            shadowed=True,
        )
        for item in values
    ]


def _document_media_type(closure: DependencyClosureManifest, logical_path: str) -> str:
    return next(member.media_type for member in closure.members if member.logical_path == logical_path)


def _resolve_inheritance(
    ledger: _ReadLedger,
    *,
    root_path: str | None = None,
    root_payload: bytes | None = None,
) -> tuple[dict[str, Any], dict[str, list[ProvenanceContribution]]]:
    memo: dict[str, tuple[dict[str, Any], dict[str, list[ProvenanceContribution]]]] = {}
    active: list[str] = []
    precedence = 0

    def resolve(path: str, payload: bytes | None = None) -> tuple[dict[str, Any], dict[str, list[ProvenanceContribution]]]:
        nonlocal precedence
        if path in memo:
            document, provenance = memo[path]
            return deepcopy(document), {key: list(values) for key, values in provenance.items()}
        if path in active:
            cycle = tuple(active[active.index(path):] + [path])
            raise _error(
                CompileStage.DEPENDENCY_RESOLUTION,
                CompileErrorCode.REFERENCE_CYCLE,
                logical_path=path,
                related_logical_paths=tuple(sorted(set(cycle))),
            )
        if len(active) >= min(ledger.closure.limits.max_dependency_depth, MAX_MERGE_DEPTH):
            raise _error(
                CompileStage.MERGE,
                CompileErrorCode.RESOURCE_LIMIT_EXCEEDED,
                logical_path=path,
                details={"max_merge_depth": MAX_MERGE_DEPTH},
            )
        active.append(path)
        raw = payload if payload is not None else ledger._read(path)
        doc = strict_parse_payload(
            raw,
            logical_path=path,
            media_type=_document_media_type(ledger.closure, path),
        )
        extends = doc.get("extends")
        if extends is None:
            refs: list[str] = []
        elif type(extends) is str and extends:
            refs = [extends]
        elif type(extends) is list and extends and all(type(item) is str and item for item in extends):
            refs = list(extends)
        else:
            raise _error(
                CompileStage.MERGE,
                CompileErrorCode.MERGE_TYPE_INVALID,
                logical_path=path,
                instance_pointer="/extends",
            )
        merged: dict[str, Any] = {}
        provenance: dict[str, list[ProvenanceContribution]] = {}
        for ref in refs:
            target, base_payload, _ = ledger.resolve_one(path, "extends", ref)
            base, base_provenance = resolve(target, base_payload)
            merged = _deep_merge_values(merged, base)
            for pointer, values in base_provenance.items():
                incoming_wins = any(not item.shadowed for item in values)
                if incoming_wins and pointer in provenance:
                    provenance[pointer] = _shadow_contributions(provenance[pointer])
                provenance.setdefault(pointer, []).extend(values)
        own = {key: value for key, value in doc.items() if key != "extends"}
        member = ledger._members[path]
        precedence += 1
        merged = deep_merge_with_provenance(
            merged,
            own,
            logical_path=path,
            blob_digest=member.blob_digest,
            precedence_index=precedence,
            provenance=provenance,
        )
        _check_merged_budget(merged, logical_path=path)
        active.pop()
        memo[path] = (deepcopy(merged), {key: list(values) for key, values in provenance.items()})
        return merged, provenance

    selected_root = root_path or ledger.closure.root_entrypoint
    selected_payload = ledger.read_root() if root_path is None else root_payload
    return resolve(selected_root, selected_payload)


def _require_object(value: Any, pointer: str, *, allow_none: bool = False) -> dict[str, Any]:
    if allow_none and value is None:
        return {}
    if type(value) is not dict:
        raise _error(
            CompileStage.SCHEMA,
            CompileErrorCode.SCHEMA_TYPE_MISMATCH,
            instance_pointer=pointer,
            details={"expected": "object"},
        )
    return value


def _select_exclusive_carrier(
    candidates: Sequence[tuple[str, bool, Any]],
    *,
    validator: Callable[[Any], Any],
    pointer: str,
    default: Any,
    conflict_code: CompileErrorCode = CompileErrorCode.SCHEMA_INVALID_VALUE,
) -> Any:
    validated = [(label, validator(value)) for label, present, value in candidates if present]
    if len(validated) > 1:
        raise _error(
            CompileStage.SCHEMA,
            conflict_code,
            instance_pointer=pointer,
            details={"reason": "duplicate_semantic_carriers", "carriers": [label for label, _ in validated]},
        )
    return validated[0][1] if validated else deepcopy(default)


def _require_list(value: Any, pointer: str) -> list[Any]:
    if type(value) is not list:
        raise _error(
            CompileStage.SCHEMA,
            CompileErrorCode.SCHEMA_TYPE_MISMATCH,
            instance_pointer=pointer,
            details={"expected": "array"},
        )
    return value


def _require_identifier(value: Any, pointer: str) -> str:
    if type(value) is not str or not value or value != value.strip() or not _IDENTIFIER_RE.fullmatch(value):
        raise _error(
            CompileStage.SCHEMA,
            CompileErrorCode.SCHEMA_INVALID_VALUE,
            instance_pointer=pointer,
            details={"expected": "identifier"},
        )
    return value


def _require_typed_identifier(value: Any, pointer: str) -> str:
    if type(value) is not str:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer=pointer, details={"expected": "string"})
    return _require_identifier(value, pointer)


def _closed_fields(mapping: Mapping[str, Any], allowed: set[str], pointer: str) -> None:
    for key in mapping:
        if key not in allowed:
            raise _error(
                CompileStage.SCHEMA,
                CompileErrorCode.SCHEMA_UNKNOWN_FIELD,
                instance_pointer=_pointer(pointer, key),
            )


def _validate_root_schema(config: dict[str, Any], options: CompileOptions) -> None:
    _closed_fields(config, _TOP_LEVEL_FIELDS, "")
    if options.source_contract == "v2":
        if type(config.get("version")) is not int or config.get("version") != 2:
            raise _error(
                CompileStage.SCHEMA,
                CompileErrorCode.SCHEMA_VERSION_UNSUPPORTED,
                instance_pointer="/version",
            )
        required = {"workspace", "providers", "modes", "loop"}
        missing = sorted(required - set(config))
        if missing:
            raise _error(
                CompileStage.SCHEMA,
                CompileErrorCode.SCHEMA_INVALID_VALUE,
                instance_pointer=_pointer("", missing[0]),
                details={"reason": "required_family_missing"},
            )
    for family, fields in _FAMILY_FIELDS.items():
        if family in config and config[family] is not None:
            mapping = _require_object(config[family], _pointer("", family))
            _closed_fields(mapping, fields, _pointer("", family))
    object_families = (
        "provider_tools", "turn_strategy", "features", "completion",
        "concurrency", "permissions", "enhanced_tools", "replay",
        "long_running", "logging", "telemetry",
    )
    for family in object_families:
        if family in config and config[family] is not None:
            _require_object(config[family], f"/{family}")
    if "resume" in config and config["resume"] is not None:
        _require_object(config["resume"], "/resume")
    _require_list(config.get("modes", []), "/modes")
    if "tool_bindings" in config:
        _require_list(config["tool_bindings"], "/tool_bindings")
    if "setup" in config:
        _require_list(config["setup"], "/setup")
    if "optimizer_mutable_pointers" in config:
        _require_list(config["optimizer_mutable_pointers"], "/optimizer_mutable_pointers")


def _translate_v1_shadow(
    config: dict[str, Any],
    root_path: str,
) -> tuple[dict[str, Any], tuple[LossRecord, ...], tuple[NoticeRecord, ...]]:
    if config.get("version") == 2:
        raise _error(
            CompileStage.TRANSLATION,
            CompileErrorCode.V1_TRANSLATION_UNSUPPORTED,
            logical_path=root_path,
            instance_pointer="/version",
            details={"reason": "v2_document_on_v1_shadow_path"},
        )
    translated = deepcopy(config)
    translated["version"] = 2
    notices: list[NoticeRecord] = []

    def move(source: str, target_family: str, target_key: str) -> None:
        if source not in translated:
            return
        family = translated.setdefault(target_family, {})
        if type(family) is not dict or target_key in family:
            raise _error(
                CompileStage.TRANSLATION,
                CompileErrorCode.V1_TRANSLATION_UNSUPPORTED,
                logical_path=root_path,
                instance_pointer=_pointer("", source),
                details={"reason": "translation_conflict"},
            )
        family[target_key] = translated.pop(source)
        notices.append(
            NoticeRecord(
                code="v1_field_translated",
                target_pointer=f"/{target_family}/{target_key}",
                details={"source_pointer": f"/{source}"},
            )
        )

    move("model", "providers", "default_model")
    move("max_iterations", "loop", "max_iterations")
    legacy_prompt = translated.pop("prompt", None)
    if legacy_prompt is not None:
        if type(legacy_prompt) is not dict or set(legacy_prompt) != {"mode"}:
            raise _error(
                CompileStage.TRANSLATION,
                CompileErrorCode.V1_TRANSLATION_UNSUPPORTED,
                logical_path=root_path,
                instance_pointer="/prompt",
            )
        prompts = translated.setdefault("prompts", {})
        if type(prompts) is not dict or "tool_prompt_mode" in prompts:
            raise _error(
                CompileStage.TRANSLATION,
                CompileErrorCode.V1_TRANSLATION_UNSUPPORTED,
                logical_path=root_path,
                instance_pointer="/prompt/mode",
                details={"reason": "translation_conflict"},
            )
        prompts["tool_prompt_mode"] = legacy_prompt["mode"]
        notices.append(
            NoticeRecord(
                code="v1_field_translated",
                target_pointer="/prompts/tool_prompt_mode",
                details={"source_pointer": "/prompt/mode"},
            )
        )
    providers = translated.get("providers")
    if type(providers) is dict and "models" not in providers:
        model_id = providers.get("default_model")
        if type(model_id) is not str or "/" not in model_id:
            raise _error(
                CompileStage.TRANSLATION,
                CompileErrorCode.V1_TRANSLATION_UNSUPPORTED,
                logical_path=root_path,
                instance_pointer="/model",
                details={"reason": "model_provider_prefix_required"},
            )
        provider_prefix = model_id.split("/", 1)[0]
        adapter_by_prefix = {
            "openai": "openai_responses",
            "anthropic": "anthropic",
            "responses": "responses",
        }
        adapter = adapter_by_prefix.get(provider_prefix)
        if adapter is None:
            raise _error(
                CompileStage.TRANSLATION,
                CompileErrorCode.V1_TRANSLATION_UNSUPPORTED,
                logical_path=root_path,
                instance_pointer="/model",
                details={"provider_prefix": provider_prefix},
            )
        providers["models"] = [{"id": model_id, "adapter": adapter, "params": {}}]
        notices.append(
            NoticeRecord(
                code="v1_model_synthesized",
                target_pointer="/providers/models/0",
                details={"provider_prefix": provider_prefix},
            )
        )
    if "resume" in translated:
        long_running = translated.setdefault("long_running", {})
        if type(long_running) is not dict or "resume" in long_running:
            raise _error(
                CompileStage.TRANSLATION,
                CompileErrorCode.V1_TRANSLATION_UNSUPPORTED,
                logical_path=root_path,
                instance_pointer="/resume",
                details={"reason": "translation_conflict"},
            )
        long_running["resume"] = translated.pop("resume")
    if "claude" in translated:
        provider_tools = translated.setdefault("provider_tools", {})
        if type(provider_tools) is not dict or "anthropic" in provider_tools:
            raise _error(
                CompileStage.TRANSLATION,
                CompileErrorCode.V1_TRANSLATION_UNSUPPORTED,
                logical_path=root_path,
                instance_pointer="/claude",
            )
        provider_tools["anthropic"] = translated.pop("claude")
    tools = translated.get("tools")
    if type(tools) is dict:
        if "defs_dir" in tools:
            registry = tools.setdefault("registry", {})
            if type(registry) is not dict or "paths" in registry:
                raise _error(
                    CompileStage.TRANSLATION,
                    CompileErrorCode.V1_TRANSLATION_UNSUPPORTED,
                    logical_path=root_path,
                    instance_pointer="/tools/defs_dir",
                )
            registry["paths"] = [tools.pop("defs_dir")]
        if "enabled" in tools:
            registry = tools.setdefault("registry", {})
            if type(registry) is not dict or "include" in registry:
                raise _error(
                    CompileStage.TRANSLATION,
                    CompileErrorCode.V1_TRANSLATION_UNSUPPORTED,
                    logical_path=root_path,
                    instance_pointer="/tools/enabled",
                )
            enabled = tools.pop("enabled")
            if type(enabled) is list:
                registry["include"] = ["*" if item in {"all", "*.*"} else item for item in enabled]
            else:
                registry["include"] = enabled
    translated["_v1_translation"] = {
        "translator_id": V1_SHADOW_TRANSLATOR_ID,
        "mapping_table_digest": V1_MAPPING_TABLE_DIGEST,
    }
    return translated, (), tuple(notices)

def _remap_v1_provenance(
    provenance: Mapping[str, Sequence[ProvenanceContribution]],
) -> dict[str, list[ProvenanceContribution]]:
    remapped = {pointer: list(values) for pointer, values in provenance.items()}

    def translate(source: str, target: str) -> None:
        for pointer, values in provenance.items():
            if pointer != source and not pointer.startswith(source + "/"):
                continue
            target_pointer = target + pointer[len(source):]
            remapped[target_pointer] = [
                ProvenanceContribution(
                    origin_kind="v1_translation",
                    logical_path=item.logical_path,
                    blob_digest=item.blob_digest,
                    source_pointer=pointer,
                    dependency_kind=item.dependency_kind,
                    precedence_index=item.precedence_index,
                    action="translate",
                    shadowed=item.shadowed,
                )
                for item in values
            ]

    for source, target in V1_MAPPING_TABLE.items():
        translate(source, target)
    if "/model" in provenance:
        for target in ("/providers/models/0/id", "/providers/models/0/adapter"):
            translate("/model", target)
    return remapped


def translate_v1_shadow(
    merged_mapping: Mapping[str, Any],
    *,
    logical_path: str,
) -> dict[str, Any]:
    """Public pure V1 mapping-table translator; it never calls the legacy loader."""

    translated, _, _ = _translate_v1_shadow(dict(merged_mapping), logical_path)
    return translated


def _dependency_obj(dep: SourceDependency) -> dict[str, Any]:
    return dep.to_canonical_obj()

def _validate_internal_schema_refs(value: Any, pointer: str, *, logical_path: str) -> None:
    internal_ref = re.compile(r"^#/(?:[A-Za-z0-9_$.-]|~[01])+(?:/(?:[A-Za-z0-9_$.-]|~[01])+)*$")
    if type(value) is dict:
        for key, item in value.items():
            child = _pointer(pointer, key)
            if key in {"$dynamicRef", "$recursiveRef"} or (
                key == "$ref" and (type(item) is not str or internal_ref.fullmatch(item) is None)
            ):
                raise _error(
                    CompileStage.REFERENCE_RESOLUTION,
                    CompileErrorCode.TOOL_INVALID,
                    logical_path=logical_path,
                    instance_pointer=child,
                    details={"reason": "external_schema_reference_forbidden" if key == "$ref" else "unsupported_schema_reference"},
                )
            _validate_internal_schema_refs(item, child, logical_path=logical_path)
    elif type(value) is list:
        for index, item in enumerate(value):
            _validate_internal_schema_refs(item, _pointer(pointer, index), logical_path=logical_path)


def _validate_template_ast(parsed: nodes.Template, *, logical_path: str, pointer: str) -> None:
    forbidden_types = (
        nodes.Call, nodes.Getattr, nodes.Getitem, nodes.Filter, nodes.Test,
        nodes.Import, nodes.FromImport, nodes.Include, nodes.Extends, nodes.Macro,
        nodes.CallBlock, nodes.Assign, nodes.AssignBlock, nodes.Block,
        nodes.Add, nodes.Sub, nodes.Mul, nodes.Div, nodes.FloorDiv, nodes.Mod,
        nodes.Pow, nodes.Neg, nodes.Pos,
    )
    forbidden_names = {"cycler", "joiner", "namespace", "lipsum", "range", "dict"}
    ast_nodes = list(parsed.find_all(nodes.Node))
    for node in ast_nodes:
        if isinstance(node, forbidden_types) or (isinstance(node, nodes.Name) and node.name in forbidden_names):
            raise _error(CompileStage.RENDER, CompileErrorCode.PROMPT_TEMPLATE_INVALID, logical_path=logical_path, instance_pointer=pointer, details={"reason": "template_capability_forbidden", "node": type(node).__name__})
    loops = [node for node in ast_nodes if isinstance(node, nodes.For)]
    if len(ast_nodes) > MAX_PROMPT_TEMPLATE_NODES or len(loops) > 1 or any(not isinstance(loop.iter, nodes.Name) or loop.iter.name != "tools" or loop.recursive for loop in loops):
        raise _error(CompileStage.RENDER, CompileErrorCode.PROMPT_TEMPLATE_INVALID, logical_path=logical_path, instance_pointer=pointer, details={"reason": "template_resource_policy"})
    undeclared = meta.find_undeclared_variables(parsed)
    if undeclared - {"dialect_id", "detail", "tools"}:
        raise _error(CompileStage.RENDER, CompileErrorCode.PROMPT_TEMPLATE_INVALID, logical_path=logical_path, instance_pointer=pointer, details={"reason": "template_context_forbidden"})


_RUNTIME_FAMILY_FIELDS: Final[dict[str, set[str]]] = {
    "features": {"plan", "todos", "rlm", "family_capability", "nested", "null_target", "empty_target", "list_target"},
    "completion": {"confidence_threshold", "allow_zero_tool_completion", "require_tool", "max_retries", "verification"},
    "concurrency": {"max_parallel_tools", "max_parallel_agents", "serial_tool_ids"},
    "permissions": {"default", "allow", "deny", "workspace_read", "workspace_write", "network"},
    "enhanced_tools": {"enabled", "tool_ids", "strict"},
    "long_running": {"enabled", "budget", "resume", "controller", "reviewers", "reset", "recovery", "verification"},
    "terminal_sessions": {"enabled", "max_sessions", "persistence", "timeout_ms"},
    "logging": {"level", "format", "sink_slot_id"},
    "telemetry": {"enabled", "sink_slot_id", "event_types"},
}


def _closed_runtime_family(value: Any, family: str) -> dict[str, Any]:
    pointer = "/" + family
    mapping = _require_object(value, pointer)
    _closed_fields(mapping, _RUNTIME_FAMILY_FIELDS[family], pointer)
    _reject_embedded_authority(mapping, pointer)
    return deepcopy(mapping)

def _pointer_exists(value: Any, pointer: str) -> bool:
    if type(pointer) is not str or not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        return False
    current = value
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if type(current) is dict and token in current:
            current = current[token]
        elif type(current) is list and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False
    return True


def _read_source_ref(
    ledger: _ReadLedger,
    from_path: str,
    kind: str,
    value: Any,
    pointer: str,
) -> tuple[str, bytes, SourceDependency]:
    if type(value) is dict:
        _closed_fields(value, {"source"}, pointer)
        raw_ref = value.get("source")
    else:
        raw_ref = value
    if type(raw_ref) is not str or not raw_ref:
        raise _error(
            CompileStage.REFERENCE_RESOLUTION,
            CompileErrorCode.REFERENCE_INVALID,
            logical_path=from_path,
            instance_pointer=pointer,
            dependency_kind=kind,
        )
    path, payload, dependency = ledger.resolve_one(from_path, kind, raw_ref)
    return path, payload, dependency


def _compile_providers(config: dict[str, Any]) -> dict[str, Any]:
    providers = _require_object(config.get("providers", {}), "/providers")
    default_model = _require_identifier(providers.get("default_model"), "/providers/default_model")
    models_raw = _require_list(providers.get("models"), "/providers/models")
    if not models_raw:
        raise _error(
            CompileStage.SCHEMA,
            CompileErrorCode.PROVIDER_INVALID,
            instance_pointer="/providers/models",
        )
    models: list[dict[str, Any]] = []
    ids: set[str] = set()
    allowed_model = {
        "id", "adapter", "provider", "display_name", "context_length", "params",
        "routing", "metadata", "request_schema_id", "route_handle_id",
        "credential_handle_id", "policy_slot_id", "trainable_json_pointers",
    }
    def normalize_routing(value: Any, routing_pointer: str) -> dict[str, Any]:
        mapping = _require_object(value, routing_pointer)
        _closed_fields(mapping, {"fallback_model_ids", "fallback_models", "disable_native_tools_on_probe_failure", "disable_stream_on_probe_failure"}, routing_pointer)

        def validate_fallback_ids(candidate: Any) -> list[str]:
            if type(candidate) is not list or any(type(item) is not str for item in candidate) or len(set(candidate)) != len(candidate):
                raise _error(CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID, instance_pointer=routing_pointer + "/fallback_model_ids")
            _reject_embedded_authority(candidate, routing_pointer + "/fallback_model_ids")
            return list(candidate)

        fallback_ids = _select_exclusive_carrier(
            (("fallback_model_ids", "fallback_model_ids" in mapping, mapping.get("fallback_model_ids")), ("fallback_models", "fallback_models" in mapping, mapping.get("fallback_models"))),
            validator=validate_fallback_ids,
            pointer=routing_pointer + "/fallback_model_ids",
            default=[],
            conflict_code=CompileErrorCode.PROVIDER_INVALID,
        )
        result = {"fallback_model_ids": fallback_ids}
        for flag_name in ("disable_native_tools_on_probe_failure", "disable_stream_on_probe_failure"):
            flag = mapping.get(flag_name, False)
            if type(flag) is not bool:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID, instance_pointer=routing_pointer + "/" + flag_name)
            result[flag_name] = flag
        return result
    for index, raw in enumerate(models_raw):
        pointer = f"/providers/models/{index}"
        model = _require_object(raw, pointer)
        _closed_fields(model, allowed_model, pointer)
        model_id = _require_identifier(model.get("id"), pointer + "/id")
        adapter = _require_identifier(model.get("adapter"), pointer + "/adapter")
        if adapter not in {"openai", "openai_responses", "anthropic", "responses", "test"}:
            raise _error(
                CompileStage.SEMANTIC_VALIDATION,
                CompileErrorCode.PROVIDER_INVALID,
                instance_pointer=pointer + "/adapter",
                details={"adapter": adapter},
            )
        if model_id in ids:
            raise _error(
                CompileStage.SEMANTIC_VALIDATION,
                CompileErrorCode.PROVIDER_INVALID,
                instance_pointer=pointer + "/id",
                details={"reason": "duplicate_model_id"},
            )
        ids.add(model_id)
        params = _require_object(model.get("params", {}), pointer + "/params")
        _reject_embedded_authority(params, pointer + "/params")
        unknown_params = sorted(set(params) - _PROVIDER_PARAM_FIELDS[adapter])
        if unknown_params:
            raise _error(
                CompileStage.SCHEMA,
                CompileErrorCode.PROVIDER_INVALID,
                instance_pointer=pointer + "/params/" + unknown_params[0],
                details={"reason": "unknown_request_parameter"},
            )
        integer_params = {"max_tokens", "max_output_tokens", "seed", "timeout_ms", "top_k"}
        boolean_params = {"stream", "parallel_tool_calls"}
        numeric_params = {"temperature", "top_p", "frequency_penalty", "presence_penalty"}
        for param_name, param_value in params.items():
            param_pointer = pointer + "/params/" + param_name
            if param_name in integer_params and type(param_value) is not int:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID, instance_pointer=param_pointer, details={"expected": "integer"})
            if param_name in boolean_params and type(param_value) is not bool:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID, instance_pointer=param_pointer, details={"expected": "boolean"})
            if param_name in numeric_params and type(param_value) not in {int, float}:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID, instance_pointer=param_pointer, details={"expected": "number"})
        context_length = model.get("context_length")
        if context_length is not None and (type(context_length) is not int or context_length <= 0):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID, instance_pointer=pointer + "/context_length")
        metadata = _require_object(model.get("metadata", {}), pointer + "/metadata")
        _reject_embedded_authority(metadata, pointer + "/metadata")
        provider_id = _require_identifier(model.get("provider", adapter), pointer + "/provider")
        display_name = model.get("display_name", model_id)
        if type(display_name) is not str:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID, instance_pointer=pointer + "/display_name")
        route = model.get("route_handle_id")
        credential = model.get("credential_handle_id")
        for field_name, value in (("route_handle_id", route), ("credential_handle_id", credential)):
            if value is not None:
                if type(value) is not str or "://" in value or value.startswith(("${", "env:", "file:", "/", "~")):
                    raise _error(
                        CompileStage.SCHEMA,
                        CompileErrorCode.FORBIDDEN_AUTHORITY,
                        instance_pointer=pointer + "/" + field_name,
                        details={"field": field_name},
                    )
                _require_identifier(value, pointer + "/" + field_name)
        expected_schema_id = f"breadboard.provider-request.{adapter}.v1"
        request_schema_id = model.get("request_schema_id", expected_schema_id)
        if request_schema_id != expected_schema_id:
            raise _error(
                CompileStage.SCHEMA,
                CompileErrorCode.PROVIDER_INVALID,
                instance_pointer=pointer + "/request_schema_id",
                details={"expected": expected_schema_id},
            )
        request_schema_digest = canonical_sha256(
            {
                "schema_id": request_schema_id,
                "adapter_id": adapter,
                "allowed_parameters": sorted(_PROVIDER_PARAM_FIELDS[adapter]),
            }
        )
        routing = _select_exclusive_carrier(
            (("providers.models[].routing", "routing" in model, model.get("routing")), ("providers.routing", "routing" in providers, providers.get("routing"))),
            validator=lambda value: normalize_routing(value, pointer + "/routing"),
            pointer=pointer + "/routing",
            default={"fallback_model_ids": [], "disable_native_tools_on_probe_failure": False, "disable_stream_on_probe_failure": False},
            conflict_code=CompileErrorCode.PROVIDER_INVALID,
        )
        policy_slot_id = _require_identifier(model.get("policy_slot_id", f"model:{model_id}"), pointer + "/policy_slot_id")
        trainable_pointers = model.get("trainable_json_pointers", [])
        if type(trainable_pointers) is not list or any(type(item) is not str or not item.startswith("/") for item in trainable_pointers):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID, instance_pointer=pointer + "/trainable_json_pointers")
        for trainable_pointer in trainable_pointers:
            if not _pointer_exists(model, trainable_pointer):
                raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.PROVIDER_INVALID, instance_pointer=pointer + "/trainable_json_pointers", details={"reason": "pointer_target_missing", "pointer": trainable_pointer})
        models.append(
            {
                "model_id": model_id,
                "provider_id": provider_id,
                "adapter_id": adapter,
                "display_name": display_name,
                "context_length": context_length,
                "request_schema_id": request_schema_id,
                "request_schema_digest": request_schema_digest,
                "params": deepcopy(params),
                "routing": routing,
                "metadata": deepcopy(metadata),
                "policy_slot_id": policy_slot_id,
            }
        )
    if default_model not in ids:
        raise _error(
            CompileStage.SEMANTIC_VALIDATION,
            CompileErrorCode.PROVIDER_INVALID,
            instance_pointer="/providers/default_model",
            details={"reason": "unknown_default_model"},
        )
    fallback_graph = {
        model["model_id"]: tuple(model["routing"]["fallback_model_ids"])
        for model in models
    }
    for model_id, fallback_ids in fallback_graph.items():
        unknown = sorted(set(fallback_ids) - ids)
        if unknown:
            raise _error(
                CompileStage.SEMANTIC_VALIDATION,
                CompileErrorCode.PROVIDER_INVALID,
                details={"model_id": model_id, "unknown_fallback_model_id": unknown[0]},
            )
    visiting_models: set[str] = set()
    visited_models: set[str] = set()

    def visit_model(model_id: str) -> None:
        if model_id in visiting_models:
            raise _error(
                CompileStage.SEMANTIC_VALIDATION,
                CompileErrorCode.PROVIDER_FALLBACK_CYCLE,
                details={"model_id": model_id},
            )
        if model_id in visited_models:
            return
        visiting_models.add(model_id)
        for fallback_id in fallback_graph[model_id]:
            visit_model(fallback_id)
        visiting_models.remove(model_id)
        visited_models.add(model_id)

    for model_id in sorted(fallback_graph):
        visit_model(model_id)
    policy_slots = [
        {
            "slot_id": model["policy_slot_id"],
            "model_id": model["model_id"],
            "adapter_id": model["adapter_id"],
            "request_schema_id": model["request_schema_id"],
            "requested_route_handle_id": next(
                (raw.get("route_handle_id") for raw in models_raw if raw.get("id") == model["model_id"]), None
            ),
            "requested_credential_handle_id": next(
                (raw.get("credential_handle_id") for raw in models_raw if raw.get("id") == model["model_id"]), None
            ),
            "trainable_json_pointers": list(next(
                (raw.get("trainable_json_pointers", []) for raw in models_raw if raw.get("id") == model["model_id"]), []
            )),
            "binding_state": "operator_resolution_required",
        }
        for model in models
    ]
    def validate_provider_tools(value: Any) -> dict[str, Any]:
        mapping = _require_object(value, "/provider_tools")
        _reject_embedded_authority(mapping, "/provider_tools")
        for flag_name in ("use_native", "suppress_prompts", "responses_use_developer_role", "responses_stateful", "terminal_tool_protocol", "verifier_required"):
            if flag_name in mapping and type(mapping[flag_name]) is not bool:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID, instance_pointer="/provider_tools/" + flag_name)
        return deepcopy(mapping)

    provider_tools = _select_exclusive_carrier(
        (("provider_tools", "provider_tools" in config, config.get("provider_tools")), ("providers.provider_tools", "provider_tools" in providers, providers.get("provider_tools"))),
        validator=validate_provider_tools,
        pointer="/provider_tools",
        default={},
        conflict_code=CompileErrorCode.PROVIDER_INVALID,
    )
    return {
        "default_model_id": default_model,
        "models": models,
        "provider_tools": provider_tools,
        "policy_slots": policy_slots,
    }


def _parse_tool(path: str, payload: bytes, dep: SourceDependency) -> dict[str, Any]:
    raw = strict_parse_payload(payload, logical_path=path, media_type=dep.media_type)
    allowed = {
        "id", "name", "description", "type_id", "manipulations",
        "syntax_formats_supported", "preferred_formats", "parameters", "execution",
        "provider_routing", "use_cases", "performance_data", "dependencies",
    }
    _closed_fields(raw, allowed, "")
    for field_name in ("id", "name", "description", "parameters"):
        if field_name not in raw:
            raise _error(
                CompileStage.SCHEMA,
                CompileErrorCode.TOOL_INVALID,
                logical_path=path,
                instance_pointer=f"/{field_name}",
            )
    tool_id = _require_identifier(raw["id"], "/id")
    name = _require_identifier(raw["name"], "/name")
    description = raw["description"]
    if type(description) is not str:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_INVALID, logical_path=path, instance_pointer="/description")
    type_id = _require_identifier(raw.get("type_id", "python"), "/type_id")

    def string_array(field_name: str) -> list[str]:
        value = raw.get(field_name, [])
        if type(value) is not list or any(type(item) is not str for item in value):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_INVALID, logical_path=path, instance_pointer=f"/{field_name}")
        return list(value)

    params: list[dict[str, Any]] = []
    parameter_names: set[str] = set()
    for index, param_raw in enumerate(_require_list(raw["parameters"], "/parameters")):
        param_pointer = f"/parameters/{index}"
        param = _require_object(param_raw, param_pointer)
        allowed_param = {"name", "schema", "type", "description", "required", "default", "validation", "examples", "items", "properties", "enum", "additionalProperties", "oneOf", "anyOf", "allOf", "$ref"}
        _closed_fields(param, allowed_param, param_pointer)
        param_name = _require_identifier(param.get("name"), param_pointer + "/name")
        if param_name in parameter_names:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_INVALID, logical_path=path, instance_pointer=param_pointer + "/name", details={"reason": "duplicate_parameter"})
        parameter_names.add(param_name)
        description_value = param.get("description")
        required = param.get("required", False)
        if description_value is not None and type(description_value) is not str:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_INVALID, logical_path=path, instance_pointer=param_pointer + "/description")
        if type(required) is not bool:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_INVALID, logical_path=path, instance_pointer=param_pointer + "/required")
        inline_schema_fields = {"type", "items", "properties", "enum", "additionalProperties", "oneOf", "anyOf", "allOf", "$ref"}
        inline_schema = {key: deepcopy(param[key]) for key in inline_schema_fields if key in param}
        def validate_parameter_schema(value: Any) -> dict[str, Any]:
            mapping = _require_object(value, param_pointer + "/schema")
            _validate_internal_schema_refs(mapping, param_pointer + "/schema", logical_path=path)
            return deepcopy(mapping)
        schema = _select_exclusive_carrier(
            (("schema", "schema" in param, param.get("schema")), ("inline_schema", bool(inline_schema), inline_schema)),
            validator=validate_parameter_schema,
            pointer=param_pointer + "/schema",
            default={},
            conflict_code=CompileErrorCode.TOOL_INVALID,
        )
        validation = _require_object(param.get("validation", {}), param_pointer + "/validation")
        examples = param.get("examples", [])
        if type(examples) is not list:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_INVALID, logical_path=path, instance_pointer=param_pointer + "/examples")
        _validate_internal_schema_refs(schema, param_pointer + "/schema", logical_path=path)
        _reject_embedded_authority(validation, param_pointer + "/validation")
        _reject_embedded_authority(examples, param_pointer + "/examples")
        params.append(
            {
                "name": param_name,
                "schema": deepcopy(schema),
                "description": description_value,
                "required": required,
                "has_default": "default" in param,
                "default_value": deepcopy(param.get("default")),
                "validation_rules": deepcopy(validation),
                "examples": deepcopy(examples),
            }
        )
    execution = _require_object(
        raw.get("execution", {"blocking": False, "max_per_turn": None}),
        "/execution",
    )
    _closed_fields(execution, {"blocking", "max_per_turn"}, "/execution")
    blocking = execution.get("blocking", False)
    max_per_turn = execution.get("max_per_turn")
    if type(blocking) is not bool or (
        max_per_turn is not None
        and (type(max_per_turn) is not int or max_per_turn <= 0)
    ):
        raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_INVALID, logical_path=path, instance_pointer="/execution")
    provider_routing = _require_object(raw.get("provider_routing", {}), "/provider_routing")
    checked_routing: dict[str, Any] = {}
    for provider_id, raw_settings in provider_routing.items():
        _require_identifier(provider_id, f"/provider_routing/{provider_id}")
        settings = _require_object(raw_settings, f"/provider_routing/{provider_id}")
        _closed_fields(settings, {"native_primary", "function_call", "additionalProperties", "strict", "fallback_formats"}, f"/provider_routing/{provider_id}")
        for flag_name in ("native_primary", "additionalProperties", "strict"):
            if flag_name in settings and type(settings[flag_name]) is not bool:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_INVALID, logical_path=path, instance_pointer=f"/provider_routing/{provider_id}/{flag_name}")
        function_call = settings.get("function_call")
        if function_call is not None and type(function_call) is not dict:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_INVALID, logical_path=path, instance_pointer=f"/provider_routing/{provider_id}/function_call")
        if function_call is not None:
            _reject_embedded_authority(function_call, f"/provider_routing/{provider_id}/function_call")
        fallback_formats = settings.get("fallback_formats", [])
        if type(fallback_formats) is not list or any(type(item) is not str for item in fallback_formats):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_INVALID, logical_path=path, instance_pointer=f"/provider_routing/{provider_id}/fallback_formats")
        _reject_embedded_authority(fallback_formats, f"/provider_routing/{provider_id}/fallback_formats")
        checked_routing[provider_id] = deepcopy(settings)
    return {
        "tool_id": tool_id,
        "model_name": name,
        "description": description,
        "type_id": type_id,
        "parameters": params,
        "manipulations": string_array("manipulations"),
        "syntax_formats_supported": string_array("syntax_formats_supported"),
        "preferred_formats": string_array("preferred_formats"),
        "use_cases": string_array("use_cases"),
        "performance_data": deepcopy(_require_object(raw.get("performance_data", {}), "/performance_data")),
        "dependencies": string_array("dependencies"),
        "execution": {"blocking": blocking, "max_per_turn": max_per_turn},
        "provider_routing": checked_routing,
        "source_dependency": _dependency_obj(dep),
    }


def _compile_tools(
    config: dict[str, Any],
    ledger: _ReadLedger,
    root_path: str,
    origins: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    tools_cfg = _require_object(config.get("tools", {}), "/tools")
    registry = _require_object(tools_cfg.get("registry", {}), "/tools/registry")
    _closed_fields(registry, {"paths", "include", "exclude"}, "/tools/registry")
    paths = registry.get("paths", [])
    if type(paths) is not list or any(type(path) is not str or not path for path in paths):
        raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer="/tools/registry/paths")
    definitions: list[dict[str, Any]] = []
    registry_members: list[dict[str, Any]] = []
    for path_index, directory in enumerate(paths):
        declaring_path = origins.get(f"/tools/registry/paths/{path_index}", root_path)
        for path, payload, dep in ledger.resolve_directory(
            declaring_path, "tool_registry", directory
        ):
            if not path.endswith((".yaml", ".yml")):
                raise _error(CompileStage.REFERENCE_RESOLUTION, CompileErrorCode.TOOL_INVALID, logical_path=path, details={"reason": "registry_member_suffix"})
            registry_members.append(_dependency_obj(dep))
            definitions.append(_parse_tool(path, payload, dep))
    ids: set[str] = set()
    names: set[str] = set()
    for tool in definitions:
        if tool["tool_id"] in ids:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_DUPLICATE_ID, details={"tool_id": tool["tool_id"]})
        if tool["model_name"] in names:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_DUPLICATE_NAME, details={"name": tool["model_name"]})
        ids.add(tool["tool_id"])
        names.add(tool["model_name"])
    original_tokens: dict[str, list[dict[str, Any]]] = {}
    for tool in definitions:
        for token in {tool["tool_id"], tool["model_name"]}:
            original_tokens.setdefault(token, []).append(tool)
    ambiguous_original = sorted(
        token
        for token, targets in original_tokens.items()
        if len({target["tool_id"] for target in targets}) > 1
    )
    if ambiguous_original:
        raise _error(
            CompileStage.SEMANTIC_VALIDATION,
            CompileErrorCode.TOOL_DUPLICATE_NAME,
            details={"reason": "ambiguous_id_name_token", "token": ambiguous_original[0]},
        )
    by_original = {token: targets[0] for token, targets in original_tokens.items()}
    applied: list[dict[str, Any]] = []
    overlays = tools_cfg.get("overlays", [])
    if type(overlays) is not list:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_OVERLAY_INVALID, instance_pointer="/tools/overlays")
    for index, raw_overlay in enumerate(overlays):
        overlay = _require_object(raw_overlay, f"/tools/overlays/{index}")
        _closed_fields(overlay, {"rename", "descriptions", "syntax_style", "provider_preference"}, f"/tools/overlays/{index}")
        affected: set[str] = set()
        for field, target_field in (("rename", "model_name"), ("descriptions", "description")):
            values = _require_object(overlay.get(field, {}), f"/tools/overlays/{index}/{field}")
            for target, value in values.items():
                tool = by_original.get(target)
                if tool is None:
                    raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_OVERLAY_TARGET_UNKNOWN, instance_pointer=f"/tools/overlays/{index}/{field}/{target}")
                if type(value) is not str or not value:
                    raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_OVERLAY_INVALID, instance_pointer=f"/tools/overlays/{index}/{field}/{target}")
                tool[target_field] = value
                affected.add(tool["tool_id"])
        syntax = _require_object(overlay.get("syntax_style", {}), f"/tools/overlays/{index}/syntax_style")
        for target, value in syntax.items():
            tool = by_original.get(target)
            if tool is None or type(value) is not str:
                raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_OVERLAY_TARGET_UNKNOWN, instance_pointer=f"/tools/overlays/{index}/syntax_style/{target}")
            supported = list(tool["syntax_formats_supported"])
            if value not in supported:
                supported.append(value)
            tool["syntax_formats_supported"] = supported
            tool["preferred_formats"] = [value] + [item for item in tool["preferred_formats"] if item != value]
            affected.add(tool["tool_id"])
        preference = _require_object(overlay.get("provider_preference", {}), f"/tools/overlays/{index}/provider_preference")
        for target, value in preference.items():
            tool = by_original.get(target)
            if tool is None or type(value) is not dict:
                raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_OVERLAY_TARGET_UNKNOWN, instance_pointer=f"/tools/overlays/{index}/provider_preference/{target}")
            for provider_id, settings in value.items():
                if type(settings) is not dict:
                    raise _error(
                        CompileStage.SCHEMA,
                        CompileErrorCode.TOOL_OVERLAY_INVALID,
                        instance_pointer=f"/tools/overlays/{index}/provider_preference/{target}/{provider_id}",
                    )
                _closed_fields(settings, {"native_primary", "function_call", "additionalProperties", "strict", "fallback_formats"}, f"/tools/overlays/{index}/provider_preference/{target}/{provider_id}")
                function_call = settings.get("function_call")
                if function_call is not None:
                    if type(function_call) is not dict:
                        raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_OVERLAY_INVALID, instance_pointer=f"/tools/overlays/{index}/provider_preference/{target}/{provider_id}/function_call")
                    _reject_embedded_authority(function_call, f"/tools/overlays/{index}/provider_preference/{target}/{provider_id}/function_call")
                fallback_formats = settings.get("fallback_formats", [])
                if type(fallback_formats) is not list or any(type(item) is not str for item in fallback_formats):
                    raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_OVERLAY_INVALID, instance_pointer=f"/tools/overlays/{index}/provider_preference/{target}/{provider_id}/fallback_formats")
                _reject_embedded_authority(fallback_formats, f"/tools/overlays/{index}/provider_preference/{target}/{provider_id}/fallback_formats")
                for flag_name in ("native_primary", "additionalProperties", "strict"):
                    if flag_name in settings and type(settings[flag_name]) is not bool:
                        raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_OVERLAY_INVALID, instance_pointer=f"/tools/overlays/{index}/provider_preference/{target}/{provider_id}/{flag_name}")
                existing = tool["provider_routing"].get(provider_id, {})
                if type(existing) is not dict:
                    raise _error(
                        CompileStage.SCHEMA,
                        CompileErrorCode.TOOL_OVERLAY_INVALID,
                        instance_pointer=f"/tools/overlays/{index}/provider_preference/{target}/{provider_id}",
                    )
                tool["provider_routing"][provider_id] = {**existing, **deepcopy(settings)}
            affected.add(tool["tool_id"])
        applied.append({"overlay_index": index, "affected_tool_ids": sorted(affected), **deepcopy(overlay)})
    final_names = [tool["model_name"] for tool in definitions]
    if len(set(final_names)) != len(final_names):
        raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_DUPLICATE_NAME, details={"reason": "post_overlay_collision"})
    final_tokens: dict[str, list[dict[str, Any]]] = {}
    for tool in definitions:
        for token in {tool["tool_id"], tool["model_name"]}:
            final_tokens.setdefault(token, []).append(tool)
    ambiguous_final = sorted(
        token
        for token, targets in final_tokens.items()
        if len({target["tool_id"] for target in targets}) > 1
    )
    if ambiguous_final:
        raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_DUPLICATE_NAME, details={"reason": "ambiguous_id_name_token", "token": ambiguous_final[0]})
    aliases_cfg = tools_cfg.get("aliases")
    if aliases_cfg is None:
        aliases_cfg = {}
    aliases_raw = _require_object(aliases_cfg, "/tools/aliases")
    aliases: list[list[str]] = []
    by_final = {token: targets[0] for token, targets in final_tokens.items()}
    for alias, target in sorted(aliases_raw.items()):
        _require_identifier(alias, f"/tools/aliases/{alias}")
        if type(target) is not str or target not in by_final:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_ALIAS_TARGET_UNKNOWN, instance_pointer=f"/tools/aliases/{alias}")
        if alias in by_final:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_INVALID, instance_pointer=f"/tools/aliases/{alias}", details={"reason": "alias_collision"})
        aliases.append([alias, by_final[target]["tool_id"]])
    include = registry.get("include", ["*"] if definitions else [])
    exclude = registry.get("exclude", [])
    if type(include) is not list or type(exclude) is not list:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer="/tools/registry/include")
    def resolve_token(token: Any) -> list[str]:
        if token == "*":
            return [tool["tool_id"] for tool in definitions]
        if type(token) is not str or token not in by_final:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_SELECTION_UNKNOWN, details={"token": token})
        return [by_final[token]["tool_id"]]
    selected: list[str] = []
    for token in include:
        for tool_id in resolve_token(token):
            if tool_id not in selected:
                selected.append(tool_id)
    excluded = {tool_id for token in exclude for tool_id in resolve_token(token)}
    selected = [tool_id for tool_id in selected if tool_id not in excluded]
    task_enabled = bool(config.get("task_tool")) or bool(
        _require_object(config.get("multi_agent", {}), "/multi_agent").get("enabled", False)
    )
    rlm_config = _require_object(config.get("features", {}), "/features").get("rlm", {})
    rlm_enabled = type(rlm_config) is dict and rlm_config.get("enabled", False) is True
    task_tool_ids = {
        tool["tool_id"]
        for tool in definitions
        if tool["tool_id"].lower() in {"task", "run_agent", "opencode_task"}
        or tool["model_name"].lower() in {"task", "run_agent", "opencode_task"}
    }
    rlm_tool_ids = {
        tool["tool_id"]
        for tool in definitions
        if tool["tool_id"].lower().startswith("rlm")
        or tool["model_name"].lower().startswith("rlm")
    }
    if not task_enabled:
        selected = [tool_id for tool_id in selected if tool_id not in task_tool_ids]
    if not rlm_enabled:
        selected = [tool_id for tool_id in selected if tool_id not in rlm_tool_ids]
    def normalize_packs(value: Any) -> list[dict[str, Any]]:
        mapping = _require_object(value, "/tool_packs")
        _reject_embedded_authority(mapping, "/tool_packs")
        normalized: list[dict[str, Any]] = []
        for pack_id, raw_pack in sorted(mapping.items()):
            _require_identifier(pack_id, f"/tool_packs/{pack_id}")
            pack = _require_object(raw_pack, f"/tool_packs/{pack_id}")
            _closed_fields(pack, {"tools", "tool_ids", "description", "exposure", "support_status"}, f"/tool_packs/{pack_id}")

            def validate_pack_tools(candidate: Any) -> list[str]:
                if type(candidate) is not list or any(type(item) is not str or item not in by_final for item in candidate):
                    raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_SELECTION_UNKNOWN, instance_pointer=f"/tool_packs/{pack_id}/tools")
                return list(candidate)

            pack_tools = _select_exclusive_carrier(
                (("tools", "tools" in pack, pack.get("tools")), ("tool_ids", "tool_ids" in pack, pack.get("tool_ids"))),
                validator=validate_pack_tools,
                pointer=f"/tool_packs/{pack_id}/tools",
                default=[],
                conflict_code=CompileErrorCode.TOOL_SELECTION_UNKNOWN,
            )
            description = pack.get("description", "")
            exposure = pack.get("exposure", "model")
            support_status = pack.get("support_status", "declared")
            if any(type(item) is not str for item in (description, exposure, support_status)):
                raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer=f"/tool_packs/{pack_id}")
            normalized.append({"pack_id": pack_id, "description": description, "tool_ids": [by_final[item]["tool_id"] for item in pack_tools], "exposure": exposure, "support_status": support_status})
        return normalized

    packs = _select_exclusive_carrier(
        (("tool_packs", "tool_packs" in config, config.get("tool_packs")), ("tools.packs", "packs" in tools_cfg, tools_cfg.get("packs"))),
        validator=normalize_packs,
        pointer="/tool_packs",
        default=[],
        conflict_code=CompileErrorCode.TOOL_SELECTION_UNKNOWN,
    )

    binding_fields = {"id", "tool_id", "binding_kind", "execution_profile", "placement", "exposure", "support_status", "environment_selector", "fallback_binding_ids"}
    def validate_binding_carrier(value: Any) -> list[Any]:
        items = _require_list(value, "/tool_bindings")
        for index, raw_binding in enumerate(items):
            binding = _require_object(raw_binding, f"/tool_bindings/{index}")
            _closed_fields(binding, binding_fields, f"/tool_bindings/{index}")
            if "environment_selector" in binding:
                _reject_embedded_authority(binding["environment_selector"], f"/tool_bindings/{index}/environment_selector")
            if "fallback_binding_ids" in binding and (type(binding["fallback_binding_ids"]) is not list or any(type(item) is not str for item in binding["fallback_binding_ids"])):
                raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_BINDING_INVALID, instance_pointer=f"/tool_bindings/{index}/fallback_binding_ids")
        return deepcopy(items)

    bindings_raw = _select_exclusive_carrier(
        (("tool_bindings", "tool_bindings" in config, config.get("tool_bindings")), ("tools.bindings", "bindings" in tools_cfg, tools_cfg.get("bindings"))),
        validator=validate_binding_carrier,
        pointer="/tool_bindings",
        default=[],
        conflict_code=CompileErrorCode.TOOL_BINDING_INVALID,
    )
    bindings: list[dict[str, Any]] = []
    binding_ids: set[str] = set()
    for index, raw in enumerate(bindings_raw):
        binding = _require_object(raw, f"/tool_bindings/{index}")
        allowed = {"id", "tool_id", "binding_kind", "execution_profile", "placement", "exposure", "support_status", "environment_selector", "fallback_binding_ids"}
        _closed_fields(binding, allowed, f"/tool_bindings/{index}")
        environment_selector = binding.get("environment_selector")
        if environment_selector is not None:
            _reject_embedded_authority(environment_selector, f"/tool_bindings/{index}/environment_selector")
        binding_id = _require_identifier(binding.get("id"), f"/tool_bindings/{index}/id")
        tool_token = binding.get("tool_id")
        if tool_token not in by_final:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_BINDING_INVALID, instance_pointer=f"/tool_bindings/{index}/tool_id")
        if binding_id in binding_ids:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_BINDING_INVALID, details={"reason": "duplicate_binding_id"})
        binding_ids.add(binding_id)
        fallback_ids = binding.get("fallback_binding_ids", [])
        if type(fallback_ids) is not list or any(type(item) is not str for item in fallback_ids):
            raise _error(
                CompileStage.SCHEMA,
                CompileErrorCode.TOOL_BINDING_INVALID,
                instance_pointer=f"/tool_bindings/{index}/fallback_binding_ids",
            )
        bindings.append({"binding_id": binding_id, "tool_id": by_final[tool_token]["tool_id"], "binding_kind": binding.get("binding_kind", "operator"), "execution_profile": binding.get("execution_profile", "default"), "placement": binding.get("placement"), "exposure": binding.get("exposure"), "support_status": binding.get("support_status"), "environment_selector": deepcopy(binding.get("environment_selector")), "fallback_binding_ids": list(fallback_ids)})
    for binding in bindings:
        if any(item not in binding_ids for item in binding["fallback_binding_ids"]):
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_BINDING_INVALID, details={"reason": "unknown_fallback_binding"})
    fallback_graph = {
        binding["binding_id"]: tuple(binding["fallback_binding_ids"])
        for binding in bindings
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit_binding(binding_id: str) -> None:
        if binding_id in visiting:
            raise _error(
                CompileStage.SEMANTIC_VALIDATION,
                CompileErrorCode.TOOL_BINDING_CYCLE,
                details={"binding_id": binding_id},
            )
        if binding_id in visited:
            return
        visiting.add(binding_id)
        for fallback_id in fallback_graph[binding_id]:
            visit_binding(fallback_id)
        visiting.remove(binding_id)
        visited.add(binding_id)

    for binding_id in sorted(fallback_graph):
        visit_binding(binding_id)
    dialect_policy = _require_object(tools_cfg.get("dialects", {}), "/tools/dialects")
    _closed_fields(dialect_policy, {"selection", "detail"}, "/tools/dialects")
    _reject_embedded_authority(dialect_policy, "/tools/dialects")
    mark_task_complete = tools_cfg.get("mark_task_complete", False)
    if type(mark_task_complete) is not bool:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.TOOL_INVALID, instance_pointer="/tools/mark_task_complete")
    return ({"registry_members": registry_members, "definitions": definitions, "selected_tool_ids": selected, "aliases": aliases, "applied_overlays": applied, "packs": packs, "binding_requests": bindings, "dialect_policy": dialect_policy, "mark_task_complete": mark_task_complete}, by_final)


def _mode_records(config: dict[str, Any], by_tool: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    raw_modes = _require_list(config.get("modes"), "/modes")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_modes):
        mode = _require_object(raw, f"/modes/{index}")
        _closed_fields(mode, {"id", "name", "prompt", "tools_enabled", "tools_disabled", "dialects", "enabled"}, f"/modes/{index}")
        mode_id = _select_exclusive_carrier(
            (("modes[].id", "id" in mode, mode.get("id")), ("modes[].name", "name" in mode, mode.get("name"))),
            validator=lambda value: _require_typed_identifier(value, f"/modes/{index}/id"),
            pointer=f"/modes/{index}/id",
            default=None,
            conflict_code=CompileErrorCode.PROMPT_MODE_UNKNOWN,
        )
        mode_id = _require_identifier(mode_id, f"/modes/{index}/id")
        if mode_id in seen:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.PROMPT_MODE_UNKNOWN, instance_pointer=f"/modes/{index}/id", details={"reason": "duplicate_mode"})
        seen.add(mode_id)
        tools_enabled = mode.get("tools_enabled", [])
        tools_disabled = mode.get("tools_disabled", [])
        dialect_ids = mode.get("dialects", [])
        if any(type(items) is not list for items in (tools_enabled, tools_disabled, dialect_ids)):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer=f"/modes/{index}")
        enabled: list[str] = []
        for token in tools_enabled:
            if token == "*":
                enabled.extend(tool["tool_id"] for tool in by_tool.values() if tool["tool_id"] not in enabled)
            elif token in by_tool:
                enabled.append(by_tool[token]["tool_id"])
            else:
                raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_SELECTION_UNKNOWN, instance_pointer=f"/modes/{index}/tools_enabled", details={"token": token})
        disabled: list[str] = []
        for token in tools_disabled:
            if token not in by_tool:
                raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_SELECTION_UNKNOWN, instance_pointer=f"/modes/{index}/tools_disabled", details={"token": token})
            disabled.append(by_tool[token]["tool_id"])
        result.append({"mode_id": mode_id, "prompt": deepcopy(mode.get("prompt", "")), "prompt_source_id": None, "enabled_tool_ids": enabled, "disabled_tool_ids": disabled, "dialect_ids": deepcopy(mode.get("dialects", [])), "enabled": mode.get("enabled", True)})
    return result


def _compile_prompts(
    config: dict[str, Any],
    modes: list[dict[str, Any]],
    providers: dict[str, Any],
    tools: dict[str, Any],
    ledger: _ReadLedger,
    root_path: str,
    config_node_id: str,
    origins: Mapping[str, str],
    defaults: list[DefaultRecord],
) -> dict[str, Any]:
    prompt_cfg = _require_object(config.get("prompts", {}), "/prompts")
    tool_prompt_mode = prompt_cfg.get("tool_prompt_mode", "system_once")
    if "tool_prompt_mode" not in prompt_cfg:
        defaults.append(DefaultRecord(target_pointer="/prompts/tool_prompt_mode", default_code="prompt_tool_mode_system_once", value=tool_prompt_mode))
    if type(tool_prompt_mode) is not str:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer="/prompts/tool_prompt_mode")
    environment = _require_object(prompt_cfg.get("environment", {}), "/prompts/environment")
    _reject_embedded_authority(environment, "/prompts/environment")
    dedupe = prompt_cfg.get("dedupe", False)
    if type(dedupe) is not bool:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer="/prompts/dedupe")
    if "dedupe" not in prompt_cfg:
        defaults.append(DefaultRecord(target_pointer="/prompts/dedupe", default_code="prompt_dedupe_disabled", value=False))

    synthesis_cfg = _select_exclusive_carrier(
        (("prompts.synthesis", "synthesis" in prompt_cfg, prompt_cfg.get("synthesis")), ("prompts.tool_prompt_synthesis", "tool_prompt_synthesis" in prompt_cfg, prompt_cfg.get("tool_prompt_synthesis"))),
        validator=lambda value: _require_object(value, "/prompts/synthesis"),
        pointer="/prompts/synthesis",
        default={},
    )
    _closed_fields(synthesis_cfg, {"enabled", "renderer_id", "dialects", "selection", "detail"}, "/prompts/synthesis")
    synthesis_enabled = synthesis_cfg.get("enabled", True)
    if type(synthesis_enabled) is not bool:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer="/prompts/synthesis/enabled")
    if "enabled" not in synthesis_cfg:
        defaults.append(DefaultRecord(target_pointer="/prompts/synthesis/enabled", default_code="prompt_synthesis_enabled", value=True))
    renderer_id = _require_identifier(synthesis_cfg.get("renderer_id", BUILTIN_TOOL_RENDERER_ID), "/prompts/synthesis/renderer_id")
    if "renderer_id" not in synthesis_cfg:
        defaults.append(DefaultRecord(target_pointer="/prompts/synthesis/renderer_id", default_code="builtin_tool_renderer", value=renderer_id))
    template_environment = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    template_environment.globals.clear()
    template_environment.filters.clear()
    template_environment.tests.clear()

    def compile_template(template_id: str, value: Any, pointer: str) -> dict[str, Any]:
        declaring_path = origins.get(pointer, root_path)
        path, payload, dependency = _read_source_ref(ledger, declaring_path, "prompt_template", value, pointer)
        if len(payload) > MAX_PROMPT_TEMPLATE_BYTES:
            raise _error(CompileStage.RENDER, CompileErrorCode.PROMPT_TEMPLATE_INVALID, logical_path=path, instance_pointer=pointer, details={"reason": "template_input_limit", "max_bytes": MAX_PROMPT_TEMPLATE_BYTES})
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _error(CompileStage.PARSE, CompileErrorCode.UTF8_INVALID, logical_path=path) from exc
        try:
            parsed_template = template_environment.parse(text)
        except Exception as exc:
            raise _error(CompileStage.RENDER, CompileErrorCode.PROMPT_TEMPLATE_INVALID, logical_path=path, instance_pointer=pointer) from exc
        _validate_template_ast(parsed_template, logical_path=path, pointer=pointer)
        return {"template_id": template_id, "engine_id": "jinja2-sandboxed-allowlist-v1", "text": text, "text_digest": bytes_sha256(payload), "source_dependency": _dependency_obj(dependency), "required_context_keys": sorted(meta.find_undeclared_variables(parsed_template))}

    templates: list[list[Any]] = []
    top_templates = _require_object(prompt_cfg.get("templates", {}), "/prompts/templates")
    for template_id, ref in sorted(top_templates.items()):
        _require_identifier(template_id, f"/prompts/templates/{template_id}")
        templates.append([template_id, compile_template(template_id, ref, f"/prompts/templates/{template_id}")])
    synthesis_dialects = _require_object(synthesis_cfg.get("dialects", {}), "/prompts/synthesis/dialects")
    for dialect_id, raw_templates in sorted(synthesis_dialects.items()):
        _require_identifier(dialect_id, f"/prompts/synthesis/dialects/{dialect_id}")
        dialect_templates = _require_object(raw_templates, f"/prompts/synthesis/dialects/{dialect_id}")
        for variant_name, ref in sorted(dialect_templates.items()):
            template_id = f"{dialect_id}:{variant_name}"
            templates.append([template_id, compile_template(template_id, ref, f"/prompts/synthesis/dialects/{dialect_id}/{variant_name}")])
    tool_catalog_template = None
    if "tool_catalog" in prompt_cfg:
        tool_catalog_template = compile_template("tool_catalog", prompt_cfg["tool_catalog"], "/prompts/tool_catalog")
    selection = _require_object(synthesis_cfg.get("selection", {}), "/prompts/synthesis/selection")
    detail = _require_object(synthesis_cfg.get("detail", {}), "/prompts/synthesis/detail")
    _closed_fields(selection, {"by_mode", "by_model", "default"}, "/prompts/synthesis/selection")
    unknown_modes = sorted(set(selection.get("by_mode", {})) - {mode["mode_id"] for mode in modes})
    if unknown_modes:
        raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.PROMPT_MODE_UNKNOWN, instance_pointer=f"/prompts/synthesis/selection/by_mode/{unknown_modes[0]}")
    _reject_embedded_authority(selection, "/prompts/synthesis/selection")
    _reject_embedded_authority(detail, "/prompts/synthesis/detail")
    synthesis = {"enabled": synthesis_enabled, "renderer_id": "jinja2-sandboxed-allowlist-v1" if tool_catalog_template is not None else renderer_id, "templates": templates, "tool_catalog_template": tool_catalog_template, "selection": deepcopy(selection), "detail": deepcopy(detail)}

    packs_map = _require_object(prompt_cfg.get("packs", {}), "/prompts/packs")
    packs: list[dict[str, Any]] = []
    sources: dict[str, dict[str, Any]] = {}
    for pack_id, raw_pack in sorted(packs_map.items()):
        _require_identifier(pack_id, f"/prompts/packs/{pack_id}")
        pack = _require_object(raw_pack, f"/prompts/packs/{pack_id}")
        entries: list[list[Any]] = []
        for key, value in sorted(pack.items()):
            source_id = f"pack:{pack_id}:{key}"
            if type(value) is dict and set(value) == {"literal"} and type(value["literal"]) is str:
                text = value["literal"]
                source = {"source_id": source_id, "kind": "literal", "text": text, "text_digest": bytes_sha256(text.encode("utf-8")), "dependency": None}
            else:
                declaring_path = origins.get(f"/prompts/packs/{pack_id}/{key}", root_path)
                path, payload, dep = _read_source_ref(ledger, declaring_path, "prompt", value, f"/prompts/packs/{pack_id}/{key}")
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise _error(CompileStage.PARSE, CompileErrorCode.UTF8_INVALID, logical_path=path) from exc
                source = {"source_id": source_id, "kind": "member", "text": text, "text_digest": bytes_sha256(payload), "dependency": _dependency_obj(dep)}
            sources[source_id] = source
            entries.append([key, source])
        packs.append({"pack_id": pack_id, "entries": entries})
    injection = _require_object(prompt_cfg.get("injection", {}), "/prompts/injection")
    _closed_fields(injection, {"system_order", "per_turn_order"}, "/prompts/injection")
    system_order = injection.get("system_order", ["@pack(base).system"])
    per_turn_order = injection.get("per_turn_order", ["mode_specific"])
    if "system_order" not in injection:
        defaults.append(DefaultRecord(target_pointer="/prompts/injection/system_order", default_code="prompt_system_injection_order", value=system_order))
    if "per_turn_order" not in injection:
        defaults.append(DefaultRecord(target_pointer="/prompts/injection/per_turn_order", default_code="prompt_per_turn_injection_order", value=per_turn_order))
    if type(system_order) is not list or type(per_turn_order) is not list:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer="/prompts/injection")
    dialect_cfg = _require_object(prompt_cfg.get("dialects", {}), "/prompts/dialects")
    default_dialects = dialect_cfg.get("default", [])
    if type(default_dialects) is str:
        default_dialects = [default_dialects]
    if type(default_dialects) is not list or any(type(item) is not str or not item for item in default_dialects):
        raise _error(CompileStage.SCHEMA, CompileErrorCode.PROMPT_DIALECT_UNKNOWN, instance_pointer="/prompts/dialects/default")

    def pack_ref(token: str) -> str:
        match = re.fullmatch(r"@pack\(([^)]+)\)\.([A-Za-z0-9_.-]+)", token)
        if not match:
            raise _error(CompileStage.RENDER, CompileErrorCode.PROMPT_PACK_UNKNOWN, details={"token": token})
        source_id = f"pack:{match.group(1)}:{match.group(2)}"
        if source_id not in sources:
            code = CompileErrorCode.PROMPT_PACK_UNKNOWN if not any(key.startswith(f"pack:{match.group(1)}:") for key in sources) else CompileErrorCode.PROMPT_KEY_UNKNOWN
            raise _error(CompileStage.RENDER, code, details={"token": token})
        return source_id

    def dialect_array(value: Any, pointer: str) -> list[str]:
        values = [value] if type(value) is str else value
        if type(values) is not list or not values or any(type(item) is not str or not item for item in values):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.PROMPT_DIALECT_UNKNOWN, instance_pointer=pointer)
        for item in values:
            _require_identifier(item, pointer)
        return list(values)

    tool_selection = _require_object(tools.get("dialect_policy", {}).get("selection", {}), "/tools/dialects/selection")
    source_tools_cfg = _require_object(config.get("tools", {}), "/tools")
    source_tool_dialects = _require_object(source_tools_cfg.get("dialects", {}), "/tools/dialects")
    def validate_dialect_selection(value: Any) -> dict[str, Any]:
        mapping = _require_object(value, "/prompts/synthesis/selection")
        _closed_fields(mapping, {"by_mode", "by_model", "default"}, "/prompts/synthesis/selection")
        _reject_embedded_authority(mapping, "/prompts/synthesis/selection")
        return deepcopy(mapping)
    effective_selection = _select_exclusive_carrier(
        (("prompts.synthesis.selection", "selection" in synthesis_cfg, selection), ("tools.dialects.selection", "selection" in source_tool_dialects, tool_selection)),
        validator=validate_dialect_selection,
        pointer="/prompts/synthesis/selection",
        default={},
        conflict_code=CompileErrorCode.PROMPT_DIALECT_UNKNOWN,
    )

    def select_dialects(mode: dict[str, Any], model: dict[str, Any]) -> list[str]:
        by_mode = _require_object(effective_selection.get("by_mode", {}), "/prompts/synthesis/selection/by_mode")
        if mode["mode_id"] in by_mode:
            return dialect_array(by_mode[mode["mode_id"]], f"/prompts/synthesis/selection/by_mode/{mode['mode_id']}")
        by_model = _require_object(effective_selection.get("by_model", {}), "/prompts/synthesis/selection/by_model")
        for pattern, value in by_model.items():
            if type(pattern) is not str:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.PROMPT_DIALECT_UNKNOWN, instance_pointer="/prompts/synthesis/selection/by_model")
            if fnmatchcase(model["model_id"], pattern):
                return dialect_array(value, f"/prompts/synthesis/selection/by_model/{pattern}")
        if "default" in effective_selection:
            return dialect_array(effective_selection["default"], "/prompts/synthesis/selection/default")
        if mode["dialect_ids"]:
            return dialect_array(mode["dialect_ids"], f"/modes/{mode['mode_id']}/dialects")
        if default_dialects:
            return dialect_array(default_dialects, "/prompts/dialects/default")
        return ["pythonic"]

    variants: list[dict[str, Any]] = []
    for mode_index, mode in enumerate(modes):
        prompt_value = mode["prompt"]
        if type(prompt_value) is dict:
            if set(prompt_value) == {"literal"} and type(prompt_value["literal"]) is str:
                text = prompt_value["literal"]
                mode_source_id = f"mode:{mode['mode_id']}"
                sources[mode_source_id] = {"source_id": mode_source_id, "kind": "literal", "text": text, "text_digest": bytes_sha256(text.encode()), "dependency": None}
            elif set(prompt_value) == {"source"}:
                declaring_path = origins.get(f"/modes/{mode_index}/prompt", root_path)
                path, payload, dep = _read_source_ref(ledger, declaring_path, "mode_prompt", prompt_value, f"/modes/{mode_index}/prompt")
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise _error(CompileStage.PARSE, CompileErrorCode.UTF8_INVALID, logical_path=path) from exc
                mode_source_id = f"mode:{mode['mode_id']}"
                sources[mode_source_id] = {"source_id": mode_source_id, "kind": "member", "text": text, "text_digest": bytes_sha256(payload), "dependency": _dependency_obj(dep)}
            else:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer=f"/modes/{mode['mode_id']}/prompt")
        elif type(prompt_value) is str and prompt_value.startswith("@pack("):
            mode_source_id = pack_ref(prompt_value)
        elif type(prompt_value) is str:
            text = prompt_value
            mode_source_id = f"mode:{mode['mode_id']}"
            sources[mode_source_id] = {"source_id": mode_source_id, "kind": "literal", "text": text, "text_digest": bytes_sha256(text.encode()), "dependency": None}
        else:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer=f"/modes/{mode['mode_id']}/prompt")
        mode["prompt_source_id"] = mode_source_id
        selected_tool_ids = list(tools["selected_tool_ids"])
        if mode["enabled_tool_ids"]:
            selected_tool_ids = [tool_id for tool_id in mode["enabled_tool_ids"] if tool_id in tools["selected_tool_ids"]]
        selected_tool_ids = [tool_id for tool_id in selected_tool_ids if tool_id not in set(mode["disabled_tool_ids"])]
        tool_set_digest = canonical_sha256({"schema": "bb.tool-set.v1", "tool_ids": selected_tool_ids})
        definitions_by_id = {definition["tool_id"]: definition for definition in tools["definitions"]}
        selected_definitions = [definitions_by_id[tool_id] for tool_id in selected_tool_ids]
        for model in providers["models"]:
            dialects = select_dialects(mode, model)
            if tool_catalog_template is None:
                catalog_lines = ["# TOOL CATALOG"]
                for definition in selected_definitions:
                    catalog_lines.extend([f"## {definition['model_name']}", definition["description"]])
                    description = definition["description"]
                    if "\n## " in description or "# TOOL CATALOG" in description:
                        raise _error(
                            CompileStage.RENDER,
                            CompileErrorCode.PROMPT_RENDER_FAILED,
                            instance_pointer="/tools/definitions",
                            details={"reason": "catalog_delimiter_collision", "tool_id": definition["tool_id"]},
                        )
                tool_catalog_text = "\n".join(catalog_lines) if selected_tool_ids else ""
                catalog_renderer_id = BUILTIN_TOOL_RENDERER_ID
                template_source_ids: list[str] = []
            else:
                template_path = tool_catalog_template["source_dependency"]["logical_path"]
                try:
                    if len(selected_definitions) > MAX_PROMPT_RENDER_TOOLS or len(canonical_json_bytes({"dialect_id": dialects[0], "detail": detail, "tools": selected_definitions})) > MAX_PROMPT_RENDER_CONTEXT_BYTES:
                        raise _error(CompileStage.RENDER, CompileErrorCode.PROMPT_TEMPLATE_INVALID, logical_path=template_path, instance_pointer="/prompts/tool_catalog", details={"reason": "template_context_limit"})
                    chunks: list[str] = []
                    rendered_bytes = 0
                    template = template_environment.from_string(tool_catalog_template["text"])
                    for chunk in template.generate(dialect_id=dialects[0], detail=detail, tools=selected_definitions):
                        rendered_bytes += len(chunk.encode("utf-8"))
                        if rendered_bytes > MAX_PROMPT_RENDER_OUTPUT_BYTES:
                            raise _error(CompileStage.RENDER, CompileErrorCode.PROMPT_TEMPLATE_INVALID, logical_path=template_path, instance_pointer="/prompts/tool_catalog", details={"reason": "template_output_limit", "max_bytes": MAX_PROMPT_RENDER_OUTPUT_BYTES})
                        chunks.append(chunk)
                    tool_catalog_text = "".join(chunks)
                except ConfigCompileError:
                    raise
                except Exception as exc:
                    raise _error(CompileStage.RENDER, CompileErrorCode.PROMPT_TEMPLATE_INVALID, logical_path=template_path, instance_pointer="/prompts/tool_catalog", details={"reason": "template_render_denied"}) from exc
                catalog_renderer_id = "jinja2-sandboxed-allowlist-v1"
                template_source_ids = [tool_catalog_template["template_id"]]
            tool_catalog = {"text": tool_catalog_text, "text_digest": bytes_sha256(tool_catalog_text.encode("utf-8")), "renderer_id": catalog_renderer_id, "effective_tool_ids": list(selected_tool_ids), "template_source_ids": template_source_ids}
            fragments_system: list[dict[str, Any]] = []
            fragments_turn: list[dict[str, Any]] = []

            def render_order(order: list[Any], stream: str) -> tuple[str, list[dict[str, Any]]]:
                texts: list[str] = []
                fragments = fragments_system if stream == "system" else fragments_turn
                seen: set[str] = set()
                for position, token in enumerate(order):
                    if token == "mode_specific":
                        source_id = mode_source_id
                    elif type(token) is str and token.startswith("@pack("):
                        source_id = pack_ref(token)
                    else:
                        raise _error(CompileStage.RENDER, CompileErrorCode.PROMPT_RENDER_FAILED, details={"token": token})
                    source = sources[source_id]
                    fragment_text = source["text"].replace("[CACHE]", "").strip()
                    digest = bytes_sha256(fragment_text.encode())
                    included = bool(fragment_text)
                    reason = "none" if included else "empty"
                    if dedupe and digest in seen:
                        included = False
                        reason = "deduplicated"
                    if included:
                        texts.append(fragment_text)
                        seen.add(digest)
                    fragments.append({"position": position, "token": token, "source_id": source_id, "text_digest": digest, "included": included, "exclusion_reason": reason})
                return "\n\n".join(texts), fragments

            system_text, _ = render_order(system_order, "system")
            turn_text, _ = render_order(per_turn_order, "per_turn")
            variant_id = canonical_sha256({"schema": PROMPT_VARIANT_ID_SCHEMA_ID, "config_node_id": config_node_id, "mode_id": mode["mode_id"], "model_id": model["model_id"], "dialect_ids": dialects, "tool_set_digest": tool_set_digest})
            variants.append({"variant_id": variant_id, "config_node_id": config_node_id, "mode_id": mode["mode_id"], "model_id": model["model_id"], "dialect_ids": list(dialects), "effective_tool_ids": list(selected_tool_ids), "tool_set_digest": tool_set_digest, "tool_catalog": tool_catalog, "system": {"text": system_text, "text_digest": bytes_sha256(system_text.encode()), "fragments": fragments_system, "renderer_id": "breadboard.prompt-assembly.v1", "template_source_ids": []}, "per_turn": {"text": turn_text, "text_digest": bytes_sha256(turn_text.encode()), "fragments": fragments_turn, "renderer_id": "breadboard.prompt-assembly.v1", "template_source_ids": []}})
    return {"tool_prompt_mode": tool_prompt_mode, "environment": deepcopy(environment), "dedupe": dedupe, "injection": {"system_order": list(system_order), "per_turn_order": list(per_turn_order)}, "dialects": {"default": list(default_dialects)}, "synthesis": synthesis, "packs": packs, "variants": variants}


def _compile_guardrails(
    config: dict[str, Any],
    ledger: _ReadLedger,
    root_path: str,
    origins: Mapping[str, str],
) -> dict[str, Any]:
    cfg = _require_object(config.get("guardrails", {}), "/guardrails")
    definitions: list[tuple[dict[str, Any], str]] = []
    ids: set[str] = set()
    includes = cfg.get("include", [])
    if type(includes) is not list:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.GUARDRAIL_INVALID, instance_pointer="/guardrails/include")
    for index, ref in enumerate(includes):
        declaring_path = origins.get(f"/guardrails/include/{index}", root_path)
        path, payload, dep = _read_source_ref(
            ledger, declaring_path, "guardrail", ref, f"/guardrails/include/{index}"
        )
        bundle = strict_parse_payload(payload, logical_path=path, media_type=dep.media_type)
        _closed_fields(bundle, {"schema_version", "description", "guards"}, "")
        guards = _require_list(bundle.get("guards", []), "/guards")
        for guard in guards:
            if type(guard) is not dict:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.GUARDRAIL_INVALID, logical_path=path)
            definitions.append((deepcopy(guard), path))
    def validate_inline_guards(value: Any) -> list[Any]:
        guards = _require_list(value, "/guardrails/definitions")
        if any(type(guard) is not dict for guard in guards):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.GUARDRAIL_INVALID, instance_pointer="/guardrails/definitions")
        return guards

    inline = _select_exclusive_carrier(
        (("guardrails.definitions", "definitions" in cfg, cfg.get("definitions")), ("guardrails.guards", "guards" in cfg, cfg.get("guards"))),
        validator=validate_inline_guards,
        pointer="/guardrails/definitions",
        default=[],
        conflict_code=CompileErrorCode.GUARDRAIL_INVALID,
    )
    inline_field = "definitions" if "definitions" in cfg else "guards"
    definitions.extend(
        (
            deepcopy(guard),
            origins.get(f"/guardrails/{inline_field}/{inline_index}", root_path),
        )
        for inline_index, guard in enumerate(inline)
    )
    result: list[dict[str, Any]] = []
    for index, (guard, declaring_path) in enumerate(definitions):
        if type(guard) is not dict:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.GUARDRAIL_INVALID)
        _closed_fields(
            guard,
            {"id", "type", "enabled", "templates", "parameters", "enable_if", "disable_if"},
            f"/guardrails/definitions/{index}",
        )
        guard_id = _require_identifier(guard.get("id"), f"/guardrails/definitions/{index}/id")
        handler = _require_identifier(guard.get("type"), f"/guardrails/definitions/{index}/type")
        if guard_id in ids:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.GUARDRAIL_DUPLICATE_ID, details={"guardrail_id": guard_id})
        ids.add(guard_id)
        templates: list[list[Any]] = []
        template_map = _require_object(guard.get("templates", {}), f"/guardrails/definitions/{index}/templates")
        for name, ref in sorted(template_map.items()):
            path, payload, dep = _read_source_ref(
                ledger,
                declaring_path,
                "guardrail_template",
                ref,
                f"/guardrails/definitions/{index}/templates/{name}",
            )
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _error(CompileStage.PARSE, CompileErrorCode.UTF8_INVALID, logical_path=path) from exc
            if "{%" in text or "%}" in text:
                raise _error(CompileStage.RENDER, CompileErrorCode.GUARDRAIL_TEMPLATE_INVALID, logical_path=path, details={"reason": "control_blocks_unsupported"})
            variables = re.findall(r"{{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*}}", text)
            residue = re.sub(r"{{\s*[A-Za-z_][A-Za-z0-9_.]*\s*}}", "", text)
            if "{{" in residue or "}}" in residue:
                raise _error(CompileStage.RENDER, CompileErrorCode.GUARDRAIL_TEMPLATE_INVALID, logical_path=path, details={"reason": "template_syntax"})
            engine_id = "jinja2-strict-v1" if variables else "plain-text-v1"
            templates.append([name, {"template_id": f"guardrail:{guard_id}:{name}", "engine_id": engine_id, "text": text, "text_digest": bytes_sha256(payload), "source_dependency": _dependency_obj(dep), "required_context_keys": sorted(set(variables))}])
        for condition_name in ("enable_if", "disable_if"):
            condition = guard.get(condition_name)
            if condition is not None and type(condition) not in {bool, str}:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.GUARDRAIL_INVALID, instance_pointer=f"/guardrails/definitions/{index}/{condition_name}")
        enabled = guard.get("enabled", True)
        if type(enabled) is not bool:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.GUARDRAIL_INVALID, instance_pointer=f"/guardrails/definitions/{index}/enabled")
        parameters = _require_object(guard.get("parameters", {}), f"/guardrails/definitions/{index}/parameters")
        _reject_embedded_authority(parameters, f"/guardrails/definitions/{index}/parameters")
        result.append({"guardrail_id": guard_id, "handler_type_id": handler, "enabled": enabled, "templates": templates, "parameters": deepcopy(parameters), "enable_condition": deepcopy(guard.get("enable_if")), "disable_condition": deepcopy(guard.get("disable_if")), "source_dependency": _dependency_obj(ledger.dependency_for(declaring_path))})
    overrides = cfg.get("overrides", [])
    if type(overrides) is dict:
        overrides = [{"id": key, **value} for key, value in overrides.items()]
    if type(overrides) is not list:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.GUARDRAIL_INVALID, instance_pointer="/guardrails/overrides")
    by_id = {guard["guardrail_id"]: guard for guard in result}
    for override in overrides:
        if type(override) is not dict or override.get("id") not in by_id:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.GUARDRAIL_OVERRIDE_TARGET_UNKNOWN)
        _closed_fields(override, {"id", "enabled", "parameters"}, "/guardrails/overrides")
        target = by_id[override["id"]]
        if "enabled" in override:
            if type(override["enabled"]) is not bool:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.GUARDRAIL_INVALID)
            target["enabled"] = override["enabled"]
        if "parameters" in override:
            target["parameters"] = _deep_merge_values(
                target["parameters"],
                _require_object(override["parameters"], "/guardrails/overrides/parameters"),
            )
    bootstrap = cfg.get("plan_bootstrap")
    resolved_bootstrap = None
    if bootstrap is not None:
        bootstrap = _require_object(bootstrap, "/guardrails/plan_bootstrap")
        _closed_fields(bootstrap, {"seed_file", "strategy", "max_turns"}, "/guardrails/plan_bootstrap")
        resolved_bootstrap = deepcopy(bootstrap)
        if "seed_file" in bootstrap:
            declaring_path = origins.get("/guardrails/plan_bootstrap/seed_file", root_path)
            seed_path, seed_payload, seed_dep = _read_source_ref(
                ledger, declaring_path, "guardrail_seed", bootstrap["seed_file"], "/guardrails/plan_bootstrap/seed_file"
            )
            try:
                seed_text = seed_payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _error(CompileStage.PARSE, CompileErrorCode.UTF8_INVALID, logical_path=seed_path) from exc
            resolved_bootstrap["seed"] = {"text": seed_text, "text_digest": bytes_sha256(seed_payload), "source_dependency": _dependency_obj(seed_dep)}
            resolved_bootstrap.pop("seed_file", None)
    return {"definitions": result, "plan_bootstrap": resolved_bootstrap}


def _compile_plugins(
    config: dict[str, Any],
    ledger: _ReadLedger,
    root_path: str,
    origins: Mapping[str, str],
    known_tool_ids: set[str],
    defaults: list[DefaultRecord],
) -> dict[str, Any]:
    cfg = _require_object(config.get("plugins", {}), "/plugins")
    enabled = cfg.get("enabled", False)
    if "enabled" not in cfg:
        defaults.append(DefaultRecord(target_pointer="/plugins/enabled", default_code="plugins_disabled", value=False))
    if type(enabled) is not bool:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.PLUGIN_INVALID, instance_pointer="/plugins/enabled")
    refs = cfg.get("manifest_refs", [])
    if type(refs) is not list:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.PLUGIN_INVALID, instance_pointer="/plugins/manifest_refs")
    trust = _require_object(cfg.get("trust_requests", {}), "/plugins/trust_requests")
    plugins: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, ref in enumerate(refs):
        declaring_path = origins.get(f"/plugins/manifest_refs/{index}", root_path)
        path, payload, dep = _read_source_ref(ledger, declaring_path, "plugin_manifest", ref, f"/plugins/manifest_refs/{index}")
        manifest = strict_parse_payload(payload, logical_path=path, media_type=dep.media_type)
        allowed = {"id", "version", "name", "description", "permissions", "runtime", "skills"}
        _closed_fields(manifest, allowed, "")
        plugin_id = manifest.get("id")
        if type(plugin_id) is not str or not _PLUGIN_ID_RE.fullmatch(plugin_id):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.PLUGIN_INVALID, logical_path=path, instance_pointer="/id")
        if plugin_id in ids:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.PLUGIN_DUPLICATE_ID, details={"plugin_id": plugin_id})
        ids.add(plugin_id)
        trust_request = trust.get(plugin_id)
        if trust_request not in {"trusted", "untrusted"}:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.PLUGIN_TRUST_UNDECLARED, details={"plugin_id": plugin_id})
        runtime = manifest.get("runtime", {"kind": "none"})
        if type(runtime) is not dict:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.PLUGIN_INVALID, logical_path=path, instance_pointer="/runtime")
        forbidden_runtime_fields = sorted(set(runtime) & {"command", "args", "argv", "executable", "module", "import"})
        if forbidden_runtime_fields:
            raise _error(
                CompileStage.SCHEMA,
                CompileErrorCode.PLUGIN_RUNTIME_FORBIDDEN,
                logical_path=path,
                instance_pointer="/runtime/" + forbidden_runtime_fields[0],
                details={"field": forbidden_runtime_fields[0]},
            )
        _reject_embedded_authority(runtime, "/runtime")
        runtime_kind = runtime.get("kind", "none")
        if runtime_kind == "none":
            _closed_fields(runtime, {"kind"}, "/runtime")
        elif runtime_kind == "mcp":
            _closed_fields(runtime, {"kind", "server_id", "operator_binding_id", "requested_tool_ids", "requested_route_handle_ids"}, "/runtime")
            _require_identifier(runtime.get("operator_binding_id"), "/runtime/operator_binding_id")
        else:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.PLUGIN_RUNTIME_FORBIDDEN, logical_path=path, instance_pointer="/runtime/kind", details={"kind": runtime_kind})
        version = manifest.get("version", "0")
        name = manifest.get("name", plugin_id)
        description = manifest.get("description", "")
        if any(type(value) is not str for value in (version, name, description)):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.PLUGIN_INVALID, logical_path=path)
        permissions = _require_object(manifest.get("permissions", {}), "/permissions")
        _reject_embedded_authority(permissions, "/permissions")
        skill_ids: set[str] = set()
        skills: list[dict[str, Any]] = []
        for skill_index, skill_raw in enumerate(manifest.get("skills", [])):
            if type(skill_raw) is not dict:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.PLUGIN_SKILL_INVALID, logical_path=path)
            _closed_fields(skill_raw, {"id", "kind", "members"}, f"/skills/{skill_index}")
            skill_id = _require_identifier(skill_raw.get("id"), f"/skills/{skill_index}/id")
            if skill_id in skill_ids:
                raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.PLUGIN_SKILL_INVALID, logical_path=path, details={"reason": "duplicate_skill_id", "skill_id": skill_id})
            skill_ids.add(skill_id)
            kind = skill_raw.get("kind", "prompt")
            if kind not in {"prompt", "graph", "tool_schema"}:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.PLUGIN_SKILL_INVALID, logical_path=path, instance_pointer=f"/skills/{skill_index}/kind")
            refs_raw = skill_raw.get("members", [])
            if type(refs_raw) is not list:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.PLUGIN_SKILL_INVALID, logical_path=path)
            members: list[dict[str, Any]] = []
            payloads: list[Any] = []
            for member_index, ref_value in enumerate(refs_raw):
                member_path, member_payload, member_dep = _read_source_ref(ledger, path, "plugin_skill", ref_value, f"/skills/{skill_index}/members/{member_index}")
                members.append(_dependency_obj(member_dep))
                if kind == "prompt":
                    try:
                        payloads.append(member_payload.decode("utf-8"))
                    except UnicodeDecodeError as exc:
                        raise _error(CompileStage.PARSE, CompileErrorCode.UTF8_INVALID, logical_path=member_path) from exc
                else:
                    payloads.append(strict_parse_payload(member_payload, logical_path=member_path, media_type=member_dep.media_type))
                    _reject_embedded_authority(payloads[-1], f"/skills/{skill_index}/members/{member_index}")
                    if kind == "tool_schema":
                        _validate_internal_schema_refs(payloads[-1], f"/skills/{skill_index}/members/{member_index}", logical_path=member_path)
            skills.append({"skill_id": skill_id, "kind": kind, "members": members, "compiled_payload": payloads})
        mcp_requests = []
        if runtime_kind == "mcp":
            binding = runtime["operator_binding_id"]
            requested_tools = runtime.get("requested_tool_ids", [])
            requested_routes = runtime.get("requested_route_handle_ids", [])
            if type(requested_tools) is not list or type(requested_routes) is not list or any(type(item) is not str for item in [*requested_tools, *requested_routes]) or len(set(requested_tools)) != len(requested_tools) or len(set(requested_routes)) != len(requested_routes):
                raise _error(CompileStage.SCHEMA, CompileErrorCode.PLUGIN_INVALID, logical_path=path, instance_pointer="/runtime")
            unknown_requested_tools = sorted(set(requested_tools) - known_tool_ids)
            if unknown_requested_tools:
                raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.PLUGIN_INVALID, logical_path=path, instance_pointer="/runtime/requested_tool_ids", details={"unknown_tool_id": unknown_requested_tools[0]})
            server_id = _require_identifier(runtime.get("server_id", plugin_id), "/runtime/server_id")
            mcp_requests.append({"server_id": server_id, "operator_binding_id": binding, "requested_tool_ids": list(requested_tools), "requested_route_handle_ids": list(requested_routes)})
        plugins.append({"plugin_id": plugin_id, "version": version, "name": name, "description": description, "manifest_dependency": _dependency_obj(dep), "trust_request": trust_request, "requested_permissions": deepcopy(permissions), "runtime_kind": runtime_kind, "skills": skills, "mcp_requests": mcp_requests})
    unknown_trust = sorted(set(trust) - ids)
    if unknown_trust:
        raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.PLUGIN_INVALID, instance_pointer="/plugins/trust_requests/" + unknown_trust[0], details={"reason": "unknown_trust_target"})
    untrusted_hooks = cfg.get("untrusted_hook_tools", [])
    if type(untrusted_hooks) is not list or any(type(item) is not str for item in untrusted_hooks):
        raise _error(CompileStage.SCHEMA, CompileErrorCode.PLUGIN_INVALID, instance_pointer="/plugins/untrusted_hook_tools")
    unknown_hooks = sorted(set(untrusted_hooks) - known_tool_ids)
    if unknown_hooks:
        raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.PLUGIN_INVALID, instance_pointer="/plugins/untrusted_hook_tools", details={"unknown_tool_id": unknown_hooks[0]})
    return {"enabled": enabled, "plugins": sorted(plugins, key=lambda item: item["plugin_id"]), "untrusted_hook_tool_ids": list(untrusted_hooks)}


def _runtime_slot(
    logical_name: str,
    purpose: str,
    access: str = "read_write",
    persistence: str = "episode",
) -> dict[str, Any]:
    if type(logical_name) is not str:
        raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.RUNTIME_SLOT_INVALID)
    normalized = logical_name.removeprefix("./")
    components = normalized.split("/")
    invalid = (
        not normalized
        or unicodedata.normalize("NFC", normalized) != normalized
        or normalized.startswith(("/", "\\"))
        or "\\" in normalized
        or re.match(r"^[A-Za-z]:", normalized) is not None
        or any(not component or component in {".", ".."} for component in components)
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    )
    if invalid:
        raise _error(
            CompileStage.SEMANTIC_VALIDATION,
            CompileErrorCode.RUNTIME_SLOT_INVALID,
            details={"logical_name": logical_name},
        )
    return {"slot_id": f"slot:{purpose}", "purpose": purpose, "logical_name": normalized, "access": access, "media_type": None, "persistence": persistence}


def _compile_team_task(
    config: dict[str, Any],
    ledger: _ReadLedger,
    root_path: str,
    options: CompileOptions,
    defaults: list[DefaultRecord],
    origins: Mapping[str, str],
    *,
    current_node_id: str,
    allow_nested: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    team_cfg = _require_object(config.get("multi_agent", {}), "/multi_agent")
    enabled = team_cfg.get("enabled", False)
    if type(enabled) is not bool:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, instance_pointer="/multi_agent/enabled")
    team: dict[str, Any] | None = None
    nested_nodes: list[dict[str, Any]] = []
    target_nodes: dict[str, tuple[str, dict[str, Any]]] = {}
    team_runtime_slots: list[dict[str, Any]] = []

    def compile_config_node(target_path: str, target_payload: bytes) -> tuple[str, dict[str, Any]]:
        if target_path == root_path:
            return current_node_id, {}
        cached = target_nodes.get(target_path)
        if cached is not None:
            return cached
        if not allow_nested:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TEAM_CONFIG_CYCLE, logical_path=target_path)
        nested_id = _provisional_node_id(target_path)
        nested_config, nested_provenance = _resolve_inheritance(
            ledger, root_path=target_path, root_payload=target_payload
        )
        if options.source_contract == "v1_shadow":
            nested_config, _, _ = _translate_v1_shadow(nested_config, target_path)
            nested_provenance = _remap_v1_provenance(nested_provenance)
        _validate_root_schema(nested_config, options)
        nested_defaults: list[DefaultRecord] = []
        nested_semantic = _build_semantic(
            nested_config,
            ledger,
            options,
            nested_defaults,
            nested_provenance,
            current_path=target_path,
            node_id=nested_id,
            allow_nested=False,
        )
        nested_semantic_config = dict(nested_semantic.config_nodes[0]["semantic_config"])
        cached = (nested_id, nested_semantic_config)
        target_nodes[target_path] = cached
        nested_nodes.append({"node_id": nested_id, "semantic_config": nested_semantic_config})
        for record in nested_defaults:
            defaults.append(DefaultRecord(target_pointer=f"/config_nodes/{len(nested_nodes)}/semantic_config{record.target_pointer}", default_code=record.default_code, value=record.value))
        return cached

    if enabled:
        team_fields = {"team_id", "version", "agents", "edges", "scheduler", "max_concurrent_agents", "spawn_tool", "async", "coordination", "bus", "workspace_sharing", "event_log_path"}
        def validate_team_carrier(value: Any) -> Any:
            if type(value) is str:
                if not value:
                    raise _error(CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, instance_pointer="/multi_agent/team_config")
                return value
            mapping = _require_object(value, "/multi_agent/team_config")
            if set(mapping) == {"source"}:
                if type(mapping["source"]) is not str or not mapping["source"]:
                    raise _error(CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, instance_pointer="/multi_agent/team_config/source")
                return deepcopy(mapping)
            _closed_fields(mapping, team_fields, "/multi_agent/team_config")
            _reject_embedded_authority(mapping, "/multi_agent/team_config")
            return deepcopy(mapping)

        raw_team = _select_exclusive_carrier(
            (("multi_agent.team_config", "team_config" in team_cfg, team_cfg.get("team_config")), ("multi_agent.team", "team" in team_cfg, team_cfg.get("team"))),
            validator=validate_team_carrier,
            pointer="/multi_agent/team_config",
            default={},
            conflict_code=CompileErrorCode.TEAM_INVALID,
        )
        team_source_pointer = "/multi_agent/team_config" if "team_config" in team_cfg else "/multi_agent/team"
        team_source_path = origins.get(team_source_pointer, root_path)
        if (type(raw_team) is dict and set(raw_team) == {"source"}) or type(raw_team) is str:
            path, payload, dep = _read_source_ref(ledger, team_source_path, "team_config", raw_team, "/multi_agent/team_config")
            raw_team = strict_parse_payload(payload, logical_path=path, media_type=dep.media_type)
            team_source_path = path
        team_raw = _require_object(raw_team, "/multi_agent/team_config")
        _closed_fields(team_raw, team_fields, "/multi_agent/team_config")
        team_id = _require_identifier(team_raw.get("team_id"), "/multi_agent/team_config/team_id")
        version = team_raw.get("version", 1)
        if type(version) is not int or version <= 0:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, instance_pointer="/multi_agent/team_config/version")
        agents_raw = _require_list(team_raw.get("agents", []), "/multi_agent/team_config/agents")
        if not agents_raw:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TEAM_INVALID, instance_pointer="/multi_agent/team_config/agents", details={"reason": "empty_team"})
        agent_records: list[dict[str, Any]] = []
        seen_agent_ids: set[str] = set()
        any_entrypoint_declared = False
        for agent_index, raw_agent in enumerate(sorted(agents_raw, key=lambda item: item.get("id", "") if type(item) is dict else "")):
            pointer = f"/multi_agent/team_config/agents/{agent_index}"
            agent = _require_object(raw_agent, pointer)
            _closed_fields(agent, {"id", "role", "config_ref", "config_node_id", "entrypoint", "read_only", "allow_spawn", "description"}, pointer)
            agent_id = _require_identifier(agent.get("id"), pointer + "/id")
            if agent_id in seen_agent_ids:
                raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TEAM_INVALID, details={"reason": "duplicate_agent_id", "agent_id": agent_id})
            seen_agent_ids.add(agent_id)
            if "config_ref" in agent:
                agent_ref = agent["config_ref"]
                if not ((type(agent_ref) is str and agent_ref) or (type(agent_ref) is dict and set(agent_ref) == {"source"} and type(agent_ref["source"]) is str and agent_ref["source"])):
                    raise _error(CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, instance_pointer=pointer + "/config_ref")
            if "config_node_id" in agent:
                _require_identifier(agent["config_node_id"], pointer + "/config_node_id")
            _select_exclusive_carrier(
                (("config_ref", "config_ref" in agent, agent.get("config_ref")), ("config_node_id", "config_node_id" in agent, agent.get("config_node_id"))),
                validator=lambda value: value,
                pointer=pointer + "/config_ref",
                default=None,
                conflict_code=CompileErrorCode.TEAM_INVALID,
            )
            if "config_ref" in agent:
                target_path, target_payload, _ = _read_source_ref(ledger, team_source_path, "team_agent_config", agent["config_ref"], pointer + "/config_ref")
                config_node_id, _ = compile_config_node(target_path, target_payload)
            else:
                declared_node = agent.get("config_node_id", "root")
                if declared_node not in {"root", current_node_id}:
                    raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TEAM_INVALID, details={"reason": "unknown_config_node_id", "config_node_id": declared_node})
                config_node_id = current_node_id
            role = agent.get("role", "agent")
            description = agent.get("description")
            read_only = agent.get("read_only", False)
            allow_spawn_value = agent.get("allow_spawn", False)
            entrypoint = agent.get("entrypoint", False)
            if type(role) is not str or (description is not None and type(description) is not str) or type(read_only) is not bool or type(allow_spawn_value) is not bool or type(entrypoint) is not bool:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, instance_pointer=pointer)
            any_entrypoint_declared = any_entrypoint_declared or "entrypoint" in agent
            agent_records.append({"agent_id": agent_id, "role": role, "config_node_id": config_node_id, "entrypoint": entrypoint, "read_only": read_only, "allow_spawn": allow_spawn_value, "description": description})
        entrypoints = [record for record in agent_records if record["entrypoint"]]
        if not entrypoints and not any_entrypoint_declared:
            agent_records[0]["entrypoint"] = True
            defaults.append(DefaultRecord(target_pointer="/team/agents/0/entrypoint", default_code="first_team_agent_entrypoint", value=True))
            entrypoints = [agent_records[0]]
        if len(entrypoints) != 1:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TEAM_INVALID, details={"reason": "exactly_one_entrypoint_required"})

        raw_edges = team_raw.get("edges", [])
        if type(raw_edges) is not list:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, instance_pointer="/multi_agent/team_config/edges")
        resolved_edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, tuple[str, ...]]] = set()
        for edge_index, raw_edge in enumerate(raw_edges):
            pointer = f"/multi_agent/team_config/edges/{edge_index}"
            edge = _require_object(raw_edge, pointer)
            _closed_fields(edge, {"from", "to", "from_agent_id", "to_agent_id", "modes"}, pointer)
            from_id = _select_exclusive_carrier(
                (("from_agent_id", "from_agent_id" in edge, edge.get("from_agent_id")), ("from", "from" in edge, edge.get("from"))),
                validator=lambda value: _require_typed_identifier(value, pointer + "/from"),
                pointer=pointer + "/from",
                default=None,
                conflict_code=CompileErrorCode.TEAM_INVALID,
            )
            to_id = _select_exclusive_carrier(
                (("to_agent_id", "to_agent_id" in edge, edge.get("to_agent_id")), ("to", "to" in edge, edge.get("to"))),
                validator=lambda value: _require_typed_identifier(value, pointer + "/to"),
                pointer=pointer + "/to",
                default=None,
                conflict_code=CompileErrorCode.TEAM_INVALID,
            )
            from_id = _require_identifier(from_id, pointer + "/from")
            to_id = _require_identifier(to_id, pointer + "/to")
            if from_id not in seen_agent_ids or to_id not in seen_agent_ids:
                raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TEAM_INVALID, instance_pointer=pointer, details={"reason": "unknown_edge_endpoint"})
            edge_modes = edge.get("modes", ["sync"])
            if type(edge_modes) is not list or not edge_modes or any(item not in {"sync", "async"} for item in edge_modes) or len(set(edge_modes)) != len(edge_modes):
                raise _error(CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, instance_pointer=pointer + "/modes")
            edge_key = (from_id, to_id, tuple(edge_modes))
            if edge_key in seen_edges:
                raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TEAM_INVALID, instance_pointer=pointer, details={"reason": "duplicate_edge"})
            seen_edges.add(edge_key)
            resolved_edges.append({"from_agent_id": from_id, "to_agent_id": to_id, "modes": list(edge_modes)})
        def select_team_field(field: str, validator: Callable[[Any], Any], default: Any) -> Any:
            return _select_exclusive_carrier(
                (("team_config." + field, field in team_raw, team_raw.get(field)), ("multi_agent." + field, field in team_cfg, team_cfg.get(field))),
                validator=validator,
                pointer="/multi_agent/team_config/" + field,
                default=default,
                conflict_code=CompileErrorCode.TEAM_INVALID,
            )

        def validate_team_bool(value: Any) -> bool:
            if type(value) is not bool:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, instance_pointer="/multi_agent/team_config/async")
            return value

        def validate_team_limit(value: Any) -> int:
            if type(value) is not int or value <= 0:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, instance_pointer="/multi_agent/team_config/max_concurrent_agents")
            return value

        def validate_team_object(field: str) -> Callable[[Any], dict[str, Any]]:
            def validate(value: Any) -> dict[str, Any]:
                mapping = _require_object(value, "/multi_agent/team_config/" + field)
                _reject_embedded_authority(mapping, "/multi_agent/team_config/" + field)
                return deepcopy(mapping)
            return validate

        def validate_event_log(value: Any) -> Any:
            if value is not None:
                _runtime_slot(value, "multi_agent_event_log", access="append")
            return value

        async_enabled = select_team_field("async", validate_team_bool, False)
        max_concurrent_agents = select_team_field("max_concurrent_agents", validate_team_limit, len(agent_records))
        scheduler_kind = select_team_field("scheduler", lambda value: _require_typed_identifier(value, "/multi_agent/team_config/scheduler"), "deterministic")
        spawn_tool_id = select_team_field("spawn_tool", lambda value: _require_typed_identifier(value, "/multi_agent/team_config/spawn_tool"), "task")
        coordination = select_team_field("coordination", validate_team_object("coordination"), {})
        bus = select_team_field("bus", validate_team_object("bus"), {})
        workspace_sharing = select_team_field("workspace_sharing", validate_team_object("workspace_sharing"), {})
        event_log_path = select_team_field("event_log_path", validate_event_log, None)
        if event_log_path is None:
            event_log_slot_id = None
        else:
            event_log_slot = _runtime_slot(event_log_path, "multi_agent_event_log", access="append")
            event_log_slot_id = event_log_slot["slot_id"]
            team_runtime_slots.append(event_log_slot)
        team = {"enabled": True, "team_id": team_id, "version": version, "agents": agent_records, "edges": resolved_edges, "scheduler_kind": scheduler_kind, "max_concurrent_agents": max_concurrent_agents, "spawn_tool_id": spawn_tool_id, "async_enabled": async_enabled, "coordination": deepcopy(coordination), "bus": deepcopy(bus), "workspace_sharing": deepcopy(workspace_sharing), "event_log_slot_id": event_log_slot_id, "source_dependency": _dependency_obj(ledger.dependency_for(team_source_path))}

    task_cfg = _require_object(config.get("task_tool", {}), "/task_tool")
    task = options.task_contract.to_canonical_obj()
    if task_cfg:
        tool_id = _require_identifier(task_cfg.get("id", "task"), "/task_tool/id")
        subagents_raw = _require_object(task_cfg.get("subagents", {}), "/task_tool/subagents")
        subagents: list[dict[str, Any]] = []
        for subagent_id, raw_subagent in sorted(subagents_raw.items()):
            pointer = f"/task_tool/subagents/{subagent_id}"
            _require_identifier(subagent_id, pointer)
            subagent = _require_object(raw_subagent, pointer)
            _closed_fields(subagent, {"role", "description", "config_ref", "config_node_id", "replay_index", "model_id", "max_steps"}, pointer)
            def validate_description(value: Any) -> str:
                if type(value) is not str:
                    raise _error(CompileStage.SCHEMA, CompileErrorCode.TASK_SOURCE_INVALID, instance_pointer=pointer + "/description")
                return value
            description = _select_exclusive_carrier(
                (("description", "description" in subagent, subagent.get("description")), ("role", "role" in subagent, subagent.get("role"))),
                validator=validate_description,
                pointer=pointer + "/description",
                default=subagent_id,
                conflict_code=CompileErrorCode.TASK_SOURCE_INVALID,
            )
            if "config_ref" in subagent:
                config_ref = subagent["config_ref"]
                if not ((type(config_ref) is str and config_ref) or (type(config_ref) is dict and set(config_ref) == {"source"} and type(config_ref["source"]) is str and config_ref["source"])):
                    raise _error(CompileStage.SCHEMA, CompileErrorCode.TASK_SOURCE_INVALID, instance_pointer=pointer + "/config_ref")
            if "config_node_id" in subagent:
                _require_identifier(subagent["config_node_id"], pointer + "/config_node_id")
            _select_exclusive_carrier(
                (("config_ref", "config_ref" in subagent, subagent.get("config_ref")), ("config_node_id", "config_node_id" in subagent, subagent.get("config_node_id"))),
                validator=lambda value: value,
                pointer=pointer + "/config_ref",
                default=None,
                conflict_code=CompileErrorCode.TASK_SOURCE_INVALID,
            )
            config_node_id = None
            if "config_ref" in subagent:
                declaring_path = origins.get(pointer + "/config_ref", root_path)
                target_path, target_payload, _ = _read_source_ref(ledger, declaring_path, "task_subagent_config", subagent["config_ref"], pointer + "/config_ref")
                config_node_id, _ = compile_config_node(target_path, target_payload)
            elif "config_node_id" in subagent:
                declared_node = subagent["config_node_id"]
                if declared_node not in {"root", current_node_id}:
                    raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TASK_SOURCE_INVALID, instance_pointer=pointer + "/config_node_id")
                config_node_id = current_node_id
            replay_index = None
            if "replay_index" in subagent:
                declaring_path = origins.get(pointer + "/replay_index", root_path)
                replay_path, replay_payload, replay_dependency = _read_source_ref(ledger, declaring_path, "task_replay_index", subagent["replay_index"], pointer + "/replay_index")
                replay_index = {"source_dependency": _dependency_obj(replay_dependency), "data": strict_parse_payload(replay_payload, logical_path=replay_path, media_type=replay_dependency.media_type)}
            model_id = subagent.get("model_id")
            if model_id is not None:
                model_id = _require_identifier(model_id, pointer + "/model_id")
            max_steps = subagent.get("max_steps")
            if max_steps is not None and (type(max_steps) is not int or max_steps <= 0):
                raise _error(CompileStage.SCHEMA, CompileErrorCode.TASK_SOURCE_INVALID, instance_pointer=pointer + "/max_steps")
            subagents.append({"subagent_id": subagent_id, "description": description, "config_node_id": config_node_id, "replay_index": replay_index, "model_id": model_id, "max_steps": max_steps})
        if task_cfg.get("render_context") not in (None, {}):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.TASK_SOURCE_INVALID, instance_pointer="/task_tool/render_context", details={"reason": "closed_task_template_context"})
        rendered = ""
        template = None
        def validate_task_template_ref(value: Any) -> Any:
            if type(value) is str and value:
                return value
            if type(value) is dict and set(value) == {"source"} and type(value["source"]) is str and value["source"]:
                return deepcopy(value)
            raise _error(CompileStage.SCHEMA, CompileErrorCode.TASK_SOURCE_INVALID, instance_pointer="/task_tool/description_template_path")

        ref = _select_exclusive_carrier(
            (("task_tool.description_template_path", "description_template_path" in task_cfg, task_cfg.get("description_template_path")), ("task_tool.description_template", "description_template" in task_cfg, task_cfg.get("description_template"))),
            validator=validate_task_template_ref,
            pointer="/task_tool/description_template_path",
            default=None,
            conflict_code=CompileErrorCode.TASK_SOURCE_INVALID,
        )
        if ref is not None:
            declaring_pointer = "/task_tool/description_template_path" if "description_template_path" in task_cfg else "/task_tool/description_template"
            declaring_path = origins.get(declaring_pointer, root_path)
            path, payload, dep = _read_source_ref(ledger, declaring_path, "task_template", ref, "/task_tool/description_template_path")
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _error(CompileStage.PARSE, CompileErrorCode.UTF8_INVALID, logical_path=path) from exc
            context_keys = sorted(set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", text)))
            if set(context_keys) - {"agents"}:
                raise _error(CompileStage.RENDER, CompileErrorCode.TASK_SOURCE_INVALID, logical_path=path, details={"reason": "unknown_template_context"})
            rendered = text.replace("{agents}", ", ".join(item["subagent_id"] for item in subagents))
            template = {"template_id": "task-description", "engine_id": "plain-text-v1", "text": text, "text_digest": bytes_sha256(payload), "source_dependency": _dependency_obj(dep), "required_context_keys": context_keys}
        task["task_tool"] = {"tool_id": tool_id, "description_template": template, "rendered_description": rendered, "subagents": subagents}
    else:
        task["task_tool"] = None
    return team, task, nested_nodes, team_runtime_slots


def _semantic_provenance(
    raw: dict[str, list[ProvenanceContribution]],
    semantic: CompiledConfig,
    defaults: list[DefaultRecord],
    dependencies: Sequence[SourceDependency],
) -> tuple[FieldProvenance, ...]:
    semantic_object = semantic.to_canonical_obj()
    semantic_pointers = sorted(set(_walk_leaf_pointers(semantic_object)))
    dependencies_by_path: dict[str, list[SourceDependency]] = {}
    dependencies_by_content: dict[tuple[str, str, int, str], list[SourceDependency]] = {}
    for dependency in dependencies:
        dependencies_by_path.setdefault(dependency.logical_path, []).append(dependency)
        content_key = (dependency.dependency_kind, dependency.blob_digest, dependency.size_bytes, dependency.media_type)
        dependencies_by_content.setdefault(content_key, []).append(dependency)

    def value_at(pointer: str) -> Any:
        current: Any = semantic_object
        if not pointer:
            return current
        for raw_token in pointer.lstrip("/").split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            current = current[token] if type(current) is dict else current[int(token)]
        return current

    semantic_pointers.extend(
        pointer
        for pointer, contributions in raw.items()
        if _pointer_exists(semantic_object, pointer)
        and any(item.action == "merge_noop" for item in contributions)
    )
    semantic_pointers[:] = sorted(set(semantic_pointers))

    def dependency_contribution(target_pointer: str) -> ProvenanceContribution | None:
        tokens = target_pointer.rstrip("/").split("/")
        for length in range(len(tokens), 0, -1):
            ancestor = "/".join(tokens[:length]) or ""
            value = value_at(ancestor)
            candidates: list[Mapping[str, Any]] = []

            def collect_content(current: Any) -> None:
                if isinstance(current, Mapping):
                    if {"dependency_kind", "blob_digest", "size_bytes", "media_type"} <= set(current):
                        candidates.append(current)
                        return
                    for item in current.values():
                        collect_content(item)
                elif isinstance(current, (list, tuple)):
                    for item in current:
                        collect_content(item)

            collect_content(value)
            for content in candidates:
                content_key = (content["dependency_kind"], content["blob_digest"], content["size_bytes"], content["media_type"])
                matches = sorted(dependencies_by_content.get(content_key, ()), key=lambda item: item.sort_key)
                if matches:
                    dependency = matches[0]
                    suffix = target_pointer[len(ancestor):]
                    return ProvenanceContribution(
                        origin_kind="source",
                        logical_path=dependency.logical_path,
                        blob_digest=dependency.blob_digest,
                        source_pointer=suffix if suffix.startswith("/") else "",
                        dependency_kind=dependency.dependency_kind,
                        precedence_index=0,
                        action="resolve",
                        shadowed=False,
                    )
        return None

    default_pointers = {record.target_pointer for record in defaults}
    semantic_pointers.extend(pointer for pointer in default_pointers if _pointer_exists(semantic_object, pointer))
    semantic_pointers[:] = sorted(set(semantic_pointers))
    records: list[FieldProvenance] = []
    for target_pointer in semantic_pointers:
        candidates = [target_pointer]
        node_match = re.fullmatch(r"/config_nodes/\d+/semantic_config(.*)", target_pointer)
        if node_match:
            candidates.append(node_match.group(1) or "")
        candidates.extend({
            "/providers/default_model_id": ["/providers/default_model"],
            "/metadata/display_name": ["/profile/name"],
            "/metadata/description": ["/profile/description"],
            "/metadata/profile_version": ["/profile/version"],
            "/metadata/profile_metadata": ["/profile/metadata"],
        }.get(target_pointer, []))
        model_match = re.fullmatch(r"/providers/models/(\d+)/(model_id|adapter_id)(.*)", target_pointer)
        if model_match:
            source_field = "id" if model_match.group(2) == "model_id" else "adapter"
            candidates.append(f"/providers/models/{model_match.group(1)}/{source_field}{model_match.group(3)}")
        mode_match = re.fullmatch(r"/modes/(\d+)/mode_id", target_pointer)
        if mode_match:
            candidates.extend([f"/modes/{mode_match.group(1)}/id", f"/modes/{mode_match.group(1)}/name"])
        contributions: list[ProvenanceContribution] = []
        for candidate in candidates:
            if candidate in raw:
                contributions = list(raw[candidate])
                break
        default_targets = [target_pointer]
        if node_match:
            default_targets.append(node_match.group(1) or "")
        defaulted = any(
            target == pointer or target.startswith(pointer + "/")
            for target in default_targets
            for pointer in default_pointers
        )
        if not contributions and defaulted:
            contributions = [ProvenanceContribution(origin_kind="compiler_default", logical_path=None, blob_digest=None, source_pointer=None, dependency_kind=None, precedence_index=0, action="default", shadowed=False)]
        if not contributions:
            resolved = dependency_contribution(target_pointer)
            if resolved is not None:
                contributions = [resolved]
        if not contributions:
            family_aliases = {"metadata": "profile", "team": "multi_agent", "task": "task_tool", "observability": "logging", "runtime": "workspace"}
            family = target_pointer.split("/", 2)[1] if target_pointer.startswith("/") else ""
            source_family = family_aliases.get(family, family)
            family_candidates = sorted(
                (pointer for pointer in raw if pointer == f"/{source_family}" or pointer.startswith(f"/{source_family}/")),
                key=lambda pointer: (-len(pointer), pointer),
            )
            if family_candidates:
                contributions = list(raw[family_candidates[0]])
        if contributions:
            enriched: list[ProvenanceContribution] = []
            for item in contributions:
                dep_matches = sorted(dependencies_by_path.get(item.logical_path or "", ()), key=lambda dep: dep.sort_key)
                dependency_kind = item.dependency_kind or (dep_matches[0].dependency_kind if dep_matches else None)
                enriched.append(ProvenanceContribution(origin_kind=item.origin_kind, logical_path=item.logical_path, blob_digest=item.blob_digest, source_pointer=item.source_pointer, dependency_kind=dependency_kind, precedence_index=item.precedence_index, action=item.action, shadowed=item.shadowed))
            contributions = enriched
        if not contributions:
            if target_pointer not in default_pointers:
                defaults.append(DefaultRecord(target_pointer=target_pointer, default_code="explicit_compiler_default", value=value_at(target_pointer)))
                default_pointers.add(target_pointer)
            contributions = [ProvenanceContribution(origin_kind="compiler_default", logical_path=None, blob_digest=None, source_pointer=None, dependency_kind=None, precedence_index=0, action="default", shadowed=False)]
        winner = max((index for index, item in enumerate(contributions) if not item.shadowed), default=len(contributions) - 1)
        normalized = tuple(ProvenanceContribution(origin_kind=item.origin_kind, logical_path=item.logical_path, blob_digest=item.blob_digest, source_pointer=item.source_pointer, dependency_kind=item.dependency_kind, precedence_index=item.precedence_index, action=item.action, shadowed=index != winner) for index, item in enumerate(contributions))
        records.append(FieldProvenance(target_pointer=target_pointer, winner_index=winner, contributions=normalized))
    return tuple(records)


def _compile_setup_requests(value: Any) -> list[dict[str, Any]]:
    requests = _require_list(value, "/setup")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(requests):
        request = _require_object(raw, f"/setup/{index}")
        _closed_fields(request, {"binding_id", "argv_data", "input_digests", "writable_output_slot_ids", "requested_route_handle_ids", "requested_credential_handle_ids", "timeout_ms", "expected_output_artifact_ids"}, f"/setup/{index}")
        binding_id = _require_identifier(request.get("binding_id"), f"/setup/{index}/binding_id")
        arrays: dict[str, list[str]] = {}
        for field_name in ("argv_data", "input_digests", "writable_output_slot_ids", "requested_route_handle_ids", "requested_credential_handle_ids", "expected_output_artifact_ids"):
            values = request.get(field_name, [])
            if type(values) is not list or any(type(item) is not str for item in values):
                raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer=f"/setup/{index}/{field_name}")
            arrays[field_name] = list(values)
        if any(re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None for item in arrays["input_digests"]):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_INVALID_VALUE, instance_pointer=f"/setup/{index}/input_digests")
        timeout_ms = request.get("timeout_ms")
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_INVALID_VALUE, instance_pointer=f"/setup/{index}/timeout_ms")
        result.append({"binding_id": binding_id, **arrays, "timeout_ms": timeout_ms})
    return result


def _source_origin_map(
    provenance: Mapping[str, Sequence[ProvenanceContribution]],
    root_path: str,
) -> dict[str, str]:
    origins: dict[str, str] = {}
    for pointer, contributions in provenance.items():
        winners = [
            item.logical_path
            for item in contributions
            if not item.shadowed and item.logical_path is not None
        ]
        origin = winners[-1] if winners else root_path
        origins[pointer] = origin
        tokens = pointer.rstrip("/").split("/")
        for length in range(len(tokens) - 1, 0, -1):
            ancestor = "/".join(tokens[:length]) or ""
            origins.setdefault(ancestor, origin)
        if pointer.endswith("/source"):
            origins[pointer.removesuffix("/source")] = origin
    return origins


def _provisional_node_id(logical_path: str) -> str:
    return canonical_sha256({"schema": "bb.config-node-provisional.v1", "logical_path": logical_path})


def _config_node_id(semantic_config: Mapping[str, Any]) -> str:
    return canonical_sha256(
        {
            "schema": CONFIG_NODE_ID_SCHEMA_ID,
            "semantic_config": _identity_projection(semantic_config),
        }
    )


_SOURCE_DEPENDENCY_FIELDS: Final = {
    "dependency_kind", "from_logical_path", "raw_reference", "logical_path",
    "blob_digest", "size_bytes", "media_type",
}


def _semantic_path_tail(path: tuple[str | int, ...]) -> tuple[str | int, ...]:
    if len(path) >= 3 and path[0] == "config_nodes" and isinstance(path[1], int) and path[2] == "semantic_config":
        return path[3:]
    return path


def _source_dependency_position(path: tuple[str | int, ...]) -> bool:
    tail = _semantic_path_tail(path)
    patterns = (
        ("tools", "definitions", int, "source_dependency"),
        ("tools", "registry_members", int),
        ("prompts", "synthesis", "templates", int, 1, "source_dependency"),
        ("prompts", "synthesis", "tool_catalog_template", "source_dependency"),
        ("prompts", "packs", int, "entries", int, 1, "dependency"),
        ("guardrails", "definitions", int, "templates", int, 1, "source_dependency"),
        ("guardrails", "definitions", int, "source_dependency"),
        ("guardrails", "plan_bootstrap", "seed", "source_dependency"),
        ("plugins", "plugins", int, "manifest_dependency"),
        ("plugins", "plugins", int, "skills", int, "members", int),
        ("team", "source_dependency"),
        ("task", "task_tool", "description_template", "source_dependency"),
        ("task", "task_tool", "subagents", int, "replay_index", "source_dependency"),
        ("replay", "session", "source_dependency"),
    )
    for pattern in patterns:
        if len(tail) != len(pattern):
            continue
        if all((isinstance(item, expected) if expected is int else item == expected) for item, expected in zip(tail, pattern)):
            return True
    return False


def _semantic_source_content(value: Any, path: tuple[str | int, ...] = ()) -> Any:
    if type(value) is dict:
        if _source_dependency_position(path) and set(value) == _SOURCE_DEPENDENCY_FIELDS:
            return {
                "dependency_kind": value["dependency_kind"],
                "blob_digest": value["blob_digest"],
                "size_bytes": value["size_bytes"],
                "media_type": value["media_type"],
            }
        return {key: _semantic_source_content(item, (*path, key)) for key, item in value.items()}
    if type(value) is list:
        return [_semantic_source_content(item, (*path, index)) for index, item in enumerate(value)]
    return value


def _compiler_identity_key(path: tuple[str | int, ...], key: str) -> bool:
    tail = _semantic_path_tail(path)
    if len(tail) == 3 and tail[0] == "prompts" and tail[1] == "variants" and isinstance(tail[2], int):
        return key in {"variant_id", "config_node_id"}
    if len(tail) == 3 and tail[0] == "team" and tail[1] == "agents" and isinstance(tail[2], int):
        return key == "config_node_id"
    if len(tail) == 4 and tail[:3] == ("task", "task_tool", "subagents") and isinstance(tail[3], int):
        return key == "config_node_id"
    if len(tail) == 2 and tail[0] == "config_nodes" and isinstance(tail[1], int):
        return key == "node_id"
    return False


def _identity_projection(value: Any, path: tuple[str | int, ...] = ()) -> Any:
    if type(value) is dict:
        return {key: _identity_projection(item, (*path, key)) for key, item in value.items() if not _compiler_identity_key(path, key)}
    if type(value) is list:
        return [_identity_projection(item, (*path, index)) for index, item in enumerate(value)]
    return value


def _replace_semantic_ids(value: Any, node_ids: Mapping[str, str], path: tuple[str | int, ...] = ()) -> Any:
    if type(value) is dict:
        result = {key: _replace_semantic_ids(item, node_ids, (*path, key)) for key, item in value.items()}
        for key in ("node_id", "config_node_id"):
            if _compiler_identity_key(path, key) and type(result.get(key)) is str and result[key] in node_ids:
                result[key] = node_ids[result[key]]
        if _compiler_identity_key(path, "variant_id") and "variant_id" in result:
            result["variant_id"] = canonical_sha256({"schema": PROMPT_VARIANT_ID_SCHEMA_ID, "variant": _identity_projection(result, path)})
        return result
    if type(value) is list:
        return [_replace_semantic_ids(item, node_ids, (*path, index)) for index, item in enumerate(value)]
    return value


def _finalize_semantic_identity(semantic: CompiledConfig) -> CompiledConfig:
    payload = _semantic_source_content(semantic.to_canonical_obj())
    node_ids = {
        node["node_id"]: _config_node_id(node["semantic_config"])
        for node in payload["config_nodes"]
    }
    payload = _replace_semantic_ids(payload, node_ids)
    unique_nodes: dict[str, dict[str, Any]] = {}
    for node in payload["config_nodes"]:
        unique_nodes.setdefault(node["node_id"], node)
    payload["config_nodes"] = list(unique_nodes.values())
    payload["root_config_node_id"] = node_ids[semantic.root_config_node_id]
    return CompiledConfig.from_dict(payload)


def _build_semantic(
    config: dict[str, Any],
    ledger: _ReadLedger,
    options: CompileOptions,
    defaults: list[DefaultRecord],
    raw_provenance: Mapping[str, Sequence[ProvenanceContribution]],
    *,
    current_path: str | None = None,
    node_id: str | None = None,
    allow_nested: bool = True,
) -> CompiledConfig:
    root_path = current_path or ledger.closure.root_entrypoint
    if node_id is None:
        node_id = _provisional_node_id(root_path)
    origins = _source_origin_map(raw_provenance, root_path)
    providers = _compile_providers(config)
    tools, by_tool = _compile_tools(config, ledger, root_path, origins)
    modes = _mode_records(config, by_tool)
    prompts = _compile_prompts(config, modes, providers, tools, ledger, root_path, node_id, origins, defaults)
    plugins = _compile_plugins(
        config,
        ledger,
        root_path,
        origins,
        {tool["tool_id"] for tool in by_tool.values()},
        defaults,
    )
    guardrails = _compile_guardrails(config, ledger, root_path, origins)
    team, task, nested_nodes, team_runtime_slots = _compile_team_task(
        config,
        ledger,
        root_path,
        options,
        defaults,
        origins,
        current_node_id=node_id,
        allow_nested=allow_nested,
    )
    profile = _require_object(config.get("profile", {}), "/profile")
    profile_metadata = _require_object(profile.get("metadata", {}), "/profile/metadata")
    metadata = {
        "display_name": profile.get("name"),
        "description": profile.get("description"),
        "profile_version": profile.get("version"),
        "profile_metadata": deepcopy(profile_metadata),
        "source_contract": options.source_contract,
        "config_schema_id": AGENT_CONFIG_SCHEMA_ID,
        "translation": config.get("_v1_translation"),
    }
    workspace = _require_object(config.get("workspace", {}), "/workspace")
    root_name = workspace.get("root", "workspace")
    root_slot = _runtime_slot(root_name, "workspace_root")
    if "root" not in workspace:
        defaults.append(DefaultRecord(target_pointer="/runtime/workspace/root_slot_id", default_code="workspace_root_slot", value=root_slot["slot_id"]))
    slots = [root_slot, *team_runtime_slots]
    mirror = workspace.get("mirror")
    mirror_slot = None
    mirror_mode = None
    if mirror is not None:
        if type(mirror) is dict:
            _closed_fields(mirror, {"logical_name", "mode"}, "/workspace/mirror")
            mirror_name = mirror.get("logical_name")
            mirror_mode = mirror.get("mode")
            if mirror_mode is not None and type(mirror_mode) is not str:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.RUNTIME_SLOT_INVALID, instance_pointer="/workspace/mirror/mode")
        else:
            mirror_name = mirror
        mirror_slot = _runtime_slot(mirror_name, "workspace_mirror")
        slots.append(mirror_slot)
    sandbox_fields = {"driver_id", "driver", "options_schema_id", "options", "mount_requests", "network_request", "image_request", "resource_request"}
    resource_fields = {"cpu", "memory_mb", "gpu_count", "gpu_memory_mb", "timeout_ms"}

    def validate_options(candidate: Any) -> dict[str, Any]:
        mapping = _require_object(candidate, "/runtime/sandbox/options")
        _reject_embedded_authority(mapping, "/runtime/sandbox/options")
        return deepcopy(mapping)

    def validate_mounts(candidate: Any) -> list[dict[str, Any]]:
        values = _require_list(candidate, "/runtime/sandbox/mount_requests")
        checked: list[dict[str, Any]] = []
        for mount_index, raw_mount in enumerate(values):
            mount_pointer = f"/runtime/sandbox/mount_requests/{mount_index}"
            mount = _require_object(raw_mount, mount_pointer)
            _closed_fields(mount, {"source_slot_id", "target_slot_id", "access", "read_only"}, mount_pointer)
            source_slot_id = _require_identifier(mount.get("source_slot_id"), mount_pointer + "/source_slot_id")
            target_slot_id = _require_identifier(mount.get("target_slot_id"), mount_pointer + "/target_slot_id")
            access = mount.get("access", "read_only")
            read_only = mount.get("read_only", access == "read_only")
            if access not in {"read_only", "read_write"} or type(read_only) is not bool or read_only != (access == "read_only"):
                raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_INVALID_VALUE, instance_pointer=mount_pointer)
            checked.append({"source_slot_id": source_slot_id, "target_slot_id": target_slot_id, "access": access, "read_only": read_only})
        return checked

    def validate_network(candidate: Any) -> dict[str, Any]:
        mapping = _require_object(candidate, "/runtime/sandbox/network_request")
        _closed_fields(mapping, {"mode", "route_handle_ids"}, "/runtime/sandbox/network_request")
        _reject_embedded_authority(mapping, "/runtime/sandbox/network_request")
        if "mode" in mapping:
            _require_identifier(mapping["mode"], "/runtime/sandbox/network_request/mode")
        if "route_handle_ids" in mapping:
            values = _require_list(mapping["route_handle_ids"], "/runtime/sandbox/network_request/route_handle_ids")
            for index, value in enumerate(values):
                _require_identifier(value, f"/runtime/sandbox/network_request/route_handle_ids/{index}")
        return deepcopy(mapping)

    def validate_image(candidate: Any) -> dict[str, str] | None:
        if candidate is None:
            return None
        if type(candidate) is str:
            image_id = _require_identifier(candidate, "/runtime/sandbox/image_request")
        else:
            image = _require_object(candidate, "/runtime/sandbox/image_request")
            _closed_fields(image, {"image_id"}, "/runtime/sandbox/image_request")
            image_id = _require_identifier(image.get("image_id"), "/runtime/sandbox/image_request/image_id")
        if not image_id.startswith("image:"):
            raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_INVALID_VALUE, instance_pointer="/runtime/sandbox/image_request", details={"reason": "logical_image_id_required"})
        return {"image_id": image_id}

    def validate_resources(candidate: Any) -> dict[str, int]:
        mapping = _require_object(candidate, "/runtime/sandbox/resource_request")
        _closed_fields(mapping, resource_fields, "/runtime/sandbox/resource_request")
        _reject_embedded_authority(mapping, "/runtime/sandbox/resource_request")
        checked: dict[str, int] = {}
        for field_name, field_value in mapping.items():
            minimum = 0 if field_name in {"gpu_count", "gpu_memory_mb"} else 1
            if type(field_value) is not int or not JCS_SAFE_INTEGER_MIN <= field_value <= JCS_SAFE_INTEGER_MAX or field_value < minimum:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.RUNTIME_SLOT_INVALID, instance_pointer="/runtime/sandbox/resource_request/" + field_name, details={"reason": "invalid_resource_quantity", "minimum": minimum})
            checked[field_name] = field_value
        return checked

    def validate_driver(candidate: Any) -> str:
        if type(candidate) is not str:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer="/runtime/sandbox/driver_id", details={"expected": "string"})
        return _require_identifier(candidate, "/runtime/sandbox/driver_id")

    def validate_sandbox_carrier(candidate: Any) -> dict[str, Any]:
        mapping = _require_object(candidate, "/sandbox")
        _closed_fields(mapping, sandbox_fields, "/sandbox")
        _select_exclusive_carrier((("sandbox.driver_id", "driver_id" in mapping, mapping.get("driver_id")), ("sandbox.driver", "driver" in mapping, mapping.get("driver"))), validator=validate_driver, pointer="/runtime/sandbox/driver_id", default="operator_resolution_required")
        if "options_schema_id" in mapping:
            _require_identifier(mapping["options_schema_id"], "/runtime/sandbox/options_schema_id")
        if "options" in mapping:
            validate_options(mapping["options"])
        if "mount_requests" in mapping:
            validate_mounts(mapping["mount_requests"])
        if "network_request" in mapping:
            validate_network(mapping["network_request"])
        if "image_request" in mapping:
            validate_image(mapping["image_request"])
        if "resource_request" in mapping:
            validate_resources(mapping["resource_request"])
        return deepcopy(mapping)

    sandbox_source = _select_exclusive_carrier(
        (("sandbox", "sandbox" in config, config.get("sandbox")), ("workspace.sandbox", "sandbox" in workspace, workspace.get("sandbox"))),
        validator=validate_sandbox_carrier,
        pointer="/sandbox",
        default={},
    )
    driver_id = _select_exclusive_carrier(
        (("workspace.driver", "driver" in workspace, workspace.get("driver")), ("sandbox.driver_id", "driver_id" in sandbox_source, sandbox_source.get("driver_id")), ("sandbox.driver", "driver" in sandbox_source, sandbox_source.get("driver"))),
        validator=validate_driver,
        pointer="/runtime/sandbox/driver_id",
        default="operator_resolution_required",
    )
    options_schema_id = _require_identifier(sandbox_source.get("options_schema_id", "breadboard.sandbox-options.v1"), "/runtime/sandbox/options_schema_id")
    options_value = _select_exclusive_carrier((("workspace.options", "options" in workspace, workspace.get("options")), ("sandbox.options", "options" in sandbox_source, sandbox_source.get("options"))), validator=validate_options, pointer="/runtime/sandbox/options", default={})
    checked_mounts = _select_exclusive_carrier((("workspace.mounts", "mounts" in workspace, workspace.get("mounts")), ("sandbox.mount_requests", "mount_requests" in sandbox_source, sandbox_source.get("mount_requests"))), validator=validate_mounts, pointer="/runtime/sandbox/mount_requests", default=[])
    network_value = _select_exclusive_carrier((("workspace.network", "network" in workspace, workspace.get("network")), ("sandbox.network_request", "network_request" in sandbox_source, sandbox_source.get("network_request"))), validator=validate_network, pointer="/runtime/sandbox/network_request", default={"mode": "none"})
    checked_image = _select_exclusive_carrier((("workspace.image", "image" in workspace, workspace.get("image")), ("sandbox.image_request", "image_request" in sandbox_source, sandbox_source.get("image_request"))), validator=validate_image, pointer="/runtime/sandbox/image_request", default=None)
    checked_workspace_resources = validate_resources(workspace.get("resources")) if "resources" in workspace else None
    checked_sandbox_resources = validate_resources(sandbox_source.get("resource_request")) if "resource_request" in sandbox_source else None
    if "resources" in workspace and "resource_request" in sandbox_source:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.RUNTIME_SLOT_INVALID, instance_pointer="/runtime/sandbox/resource_request", details={"reason": "duplicate_resource_request_carriers"})
    checked_resources = checked_workspace_resources if checked_workspace_resources is not None else (checked_sandbox_resources or {})
    sandbox_request = {"driver_id": driver_id, "options_schema_id": options_schema_id, "options": options_value, "mount_requests": checked_mounts, "network_request": network_value, "image_request": checked_image, "resource_request": checked_resources}
    if "driver" not in workspace and "driver_id" not in sandbox_source and "driver" not in sandbox_source:
        defaults.append(DefaultRecord(target_pointer="/runtime/sandbox/driver_id", default_code="sandbox_operator_resolution", value=driver_id))
    if "network" not in workspace and "network_request" not in sandbox_source:
        defaults.append(DefaultRecord(target_pointer="/runtime/sandbox/network_request", default_code="sandbox_network_none", value={"mode": "none"}))

    long_running = deepcopy(_require_object(config.get("long_running", {}), "/long_running"))
    def validate_resume(value: Any) -> dict[str, Any]:
        mapping = _require_object(value, "/long_running/resume")
        _closed_fields(mapping, {"enabled", "state_path", "state_slot", "state_slot_id"}, "/long_running/resume")
        if "enabled" in mapping and type(mapping["enabled"]) is not bool:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer="/long_running/resume/enabled")
        if "state_path" in mapping:
            _runtime_slot(mapping["state_path"], "long_running_state", persistence="resume")
        for slot_field in ("state_slot", "state_slot_id"):
            if slot_field in mapping:
                _require_identifier(mapping[slot_field], "/long_running/resume/" + slot_field)
        _reject_embedded_authority({key: item for key, item in mapping.items() if key != "state_path"}, "/long_running/resume")
        return deepcopy(mapping)

    selected_resume = _select_exclusive_carrier(
        (("resume", "resume" in config, config.get("resume")), ("long_running.resume", "resume" in long_running, long_running.get("resume"))),
        validator=validate_resume,
        pointer="/long_running/resume",
        default=None,
    )
    if selected_resume is not None:
        long_running["resume"] = selected_resume
        resume_config = selected_resume
    else:
        long_running.pop("resume", None)
        resume_config = None
    if resume_config is not None:
        if "state_path" in resume_config:
            state_slot = _runtime_slot(resume_config["state_path"], "long_running_state", persistence="resume")
            slots.append(state_slot)
            long_running["resume"].pop("state_path")
            long_running["resume"]["state_slot_id"] = state_slot["slot_id"]
    _closed_fields(long_running, _RUNTIME_FAMILY_FIELDS["long_running"], "/long_running")
    _reject_embedded_authority(long_running, "/long_running")
    runtime = {
        "runner_adapter_id": options.target.runner_adapter_id,
        "runtime_abi": options.target.runtime_abi,
        "workspace": {"root_slot_id": root_slot["slot_id"], "mirror_enabled": mirror_slot is not None, "mirror_slot_id": None if mirror_slot is None else mirror_slot["slot_id"], "mirror_mode": mirror_mode},
        "sandbox": sandbox_request,
        "setup": _compile_setup_requests(config.get("setup", [])),
        "route_handle_ids": sorted({slot["requested_route_handle_id"] for slot in providers["policy_slots"] if slot["requested_route_handle_id"] is not None}),
        "credential_handle_ids": sorted({slot["requested_credential_handle_id"] for slot in providers["policy_slots"] if slot["requested_credential_handle_id"] is not None}),
        "image_ids": ([] if checked_image is None else [checked_image["image_id"]]),
        "verifier_binding_id": options.task_contract.verifier.binding_id,
        "limits": {},
        "evidence": options.task_contract.evidence.to_canonical_obj(),
        "retention": options.task_contract.retention.to_canonical_obj(),
        "slots": slots,
    }
    loop = deepcopy(_require_object(config.get("loop", {}), "/loop"))
    _closed_fields(loop, {"sequence", "limits", "turn_strategy", "max_iterations", "max_steps", "plan_turn_limit"}, "/loop")
    loop_limits = _require_object(loop.get("limits", {}), "/loop/limits")
    _closed_fields(loop_limits, {"max_iterations", "max_steps", "plan_turn_limit"}, "/loop/limits")
    top_level_loop_limits = {
        key: loop[key]
        for key in ("max_iterations", "max_steps", "plan_turn_limit")
        if key in loop
    }
    for limits_mapping, limits_pointer in (
        (loop_limits, "/loop/limits"),
        (top_level_loop_limits, "/loop"),
    ):
        _reject_embedded_authority(limits_mapping, limits_pointer)
        for field_name, field_value in limits_mapping.items():
            minimum = 0 if field_name == "plan_turn_limit" else 1
            if type(field_value) is not int or not JCS_SAFE_INTEGER_MIN <= field_value <= JCS_SAFE_INTEGER_MAX or field_value < minimum:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_INVALID_VALUE, instance_pointer=limits_pointer + "/" + field_name)
    turn_top = deepcopy(_require_object(config.get("turn_strategy", {}), "/turn_strategy"))
    turn_loop = _require_object(loop.get("turn_strategy", {}), "/loop/turn_strategy")
    for value, pointer in ((turn_top, "/turn_strategy"), (turn_loop, "/loop/turn_strategy")):
        _closed_fields(value, {"relay", "flow", "allow_multiple_per_turn"}, pointer)
        _reject_embedded_authority(value, pointer)
        for string_field in ("relay", "flow"):
            if string_field in value:
                _require_identifier(value[string_field], pointer + "/" + string_field)
        if "allow_multiple_per_turn" in value and type(value["allow_multiple_per_turn"]) is not bool:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer=pointer + "/allow_multiple_per_turn")
    overlapping_turn_fields = sorted(set(turn_top) & set(turn_loop))
    if overlapping_turn_fields:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_INVALID_VALUE, instance_pointer="/turn_strategy/" + overlapping_turn_fields[0], details={"reason": "duplicate_semantic_carriers", "carriers": ["turn_strategy", "loop.turn_strategy"]})
    turn_strategy = {**turn_top, **turn_loop}
    runtime["limits"] = deepcopy(loop_limits)
    if "turn_strategy" not in config and "turn_strategy" not in loop:
        defaults.append(DefaultRecord(target_pointer="/turn_strategy", default_code="empty_turn_strategy", value={}))
    sequence = loop.get("sequence")
    if type(sequence) is not list or not sequence:
        raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_INVALID_VALUE, instance_pointer="/loop/sequence")
    mode_ids = {mode["mode_id"] for mode in modes}
    normalized_steps: list[dict[str, Any]] = []
    for index, step in enumerate(sequence):
        if type(step) is str:
            mode_id = step
            condition = None
        elif type(step) is dict and set(step) <= {"if", "then", "mode"}:
            mode_id = _select_exclusive_carrier(
                (("then", "then" in step, step.get("then")), ("mode", "mode" in step, step.get("mode"))),
                validator=lambda value: _require_typed_identifier(value, f"/loop/sequence/{index}/mode"),
                pointer=f"/loop/sequence/{index}/mode",
                default=None,
            )
            condition = step.get("if")
        else:
            raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_INVALID_VALUE, instance_pointer=f"/loop/sequence/{index}")
        if mode_id not in mode_ids:
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.PROMPT_MODE_UNKNOWN, instance_pointer=f"/loop/sequence/{index}", details={"mode_id": mode_id})
        resolved_condition = None
        if condition is not None:
            if type(condition) is bool:
                resolved_condition = {"kind": "always", "expected_truthy": condition, "evaluated_value": condition}
            elif type(condition) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", condition):
                value: Any = config
                for component in condition.split("."):
                    if type(value) is not dict or component not in value:
                        raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.SCHEMA_INVALID_VALUE, instance_pointer=f"/loop/sequence/{index}/if", details={"reason": "condition_pointer_missing"})
                    value = value[component]
                if type(value) is not bool:
                    raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer=f"/loop/sequence/{index}/if", details={"expected": "boolean_pointer"})
                resolved_condition = {"kind": "config_pointer", "pointer": "/" + "/".join(condition.split(".")), "expected_truthy": True, "evaluated_value": value}
            else:
                raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_INVALID_VALUE, instance_pointer=f"/loop/sequence/{index}/if", details={"reason": "unsupported_condition"})
        normalized_steps.append({"condition": resolved_condition, "mode_id": mode_id})
    loop["sequence"] = normalized_steps
    _reject_embedded_authority(loop, "/loop")
    loop.pop("turn_strategy", None)
    recorded_default_pointers = {record.target_pointer for record in defaults}
    for family in _DEFAULT_OBJECT_FAMILIES:
        target_pointer = f"/{family}"
        if family not in config and target_pointer not in recorded_default_pointers:
            defaults.append(DefaultRecord(target_pointer=target_pointer, default_code=f"empty_{family}", value={}))
    features = _closed_runtime_family(config.get("features", {}), "features")
    completion = _closed_runtime_family(config.get("completion", {}), "completion")
    concurrency = _closed_runtime_family(config.get("concurrency", {}), "concurrency")
    permissions = _closed_runtime_family(config.get("permissions", {}), "permissions")
    enhanced_tools = _closed_runtime_family(config.get("enhanced_tools", {}), "enhanced_tools")
    replay = deepcopy(_require_object(config.get("replay", {}), "/replay"))
    _closed_fields(replay, {"strict", "session_path", "output_path"}, "/replay")
    if "session_path" in replay:
        declaring_path = origins.get("/replay/session_path", root_path)
        replay_path, replay_payload, replay_dependency = _read_source_ref(ledger, declaring_path, "replay_session", replay["session_path"], "/replay/session_path")
        replay.pop("session_path")
        replay["session"] = {"source_dependency": _dependency_obj(replay_dependency), "data": strict_parse_payload(replay_payload, logical_path=replay_path, media_type=replay_dependency.media_type)}
    if "output_path" in replay:
        replay_output_slot = _runtime_slot(replay["output_path"], "replay_output")
        runtime["slots"].append(replay_output_slot)
        replay.pop("output_path")
        replay["output_slot_id"] = replay_output_slot["slot_id"]
    if "session" in replay:
        _reject_embedded_authority(replay["session"]["data"], "/replay/session/data")
    terminal_sessions = _closed_runtime_family(config.get("terminal_sessions", {}), "terminal_sessions")
    observability = {
        "logging": _closed_runtime_family(config.get("logging", {}), "logging"),
        "telemetry": _closed_runtime_family(config.get("telemetry", {}), "telemetry"),
    }
    sampling = _require_object(config.get("sampling", {}), "/sampling")
    _closed_fields(sampling, _FAMILY_FIELDS["sampling"], "/sampling")
    _reject_embedded_authority(sampling, "/sampling")
    if "temperature" in sampling:
        temperature = sampling["temperature"]
        if (
            type(temperature) not in (int, float)
            or isinstance(temperature, bool)
            or not math.isfinite(temperature)
            or temperature < 0
            or temperature > 2
        ):
            raise _error(
                CompileStage.SCHEMA,
                CompileErrorCode.SCHEMA_INVALID_VALUE,
                instance_pointer="/sampling/temperature",
                details={"reason": "temperature_out_of_range"},
            )
    sampling = deepcopy(sampling)
    pointers = config.get("optimizer_mutable_pointers", [])
    if type(pointers) is not list or any(type(item) is not str for item in pointers):
        raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, instance_pointer="/optimizer_mutable_pointers")
    resolved_config = {
        "version": 2,
        "metadata": metadata,
        "providers": providers,
        "prompts": prompts,
        "tools": tools,
        "plugins": plugins,
        "guardrails": guardrails,
        "team": team,
        "task": task,
        "runtime": runtime,
        "modes": modes,
        "loop": loop,
        "turn_strategy": turn_strategy,
        "features": features,
        "completion": completion,
        "concurrency": concurrency,
        "permissions": permissions,
        "enhanced_tools": enhanced_tools,
        "replay": replay,
        "long_running": long_running,
        "terminal_sessions": terminal_sessions,
        "observability": observability,
        "sampling": sampling,
        "optimizer_mutable_pointers": sorted(pointers),
    }
    for mutable_pointer in pointers:
        if not _pointer_exists(resolved_config, mutable_pointer):
            raise _error(CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.SCHEMA_INVALID_VALUE, instance_pointer="/optimizer_mutable_pointers", details={"reason": "pointer_target_missing", "pointer": mutable_pointer})
    config_nodes = tuple([{"node_id": node_id, "semantic_config": resolved_config}, *nested_nodes])
    return CompiledConfig(
        root_config_node_id=node_id,
        config_nodes=config_nodes,
        metadata=metadata,
        providers=providers,
        prompts=prompts,
        tools=tools,
        plugins=plugins,
        guardrails=guardrails,
        team=team,
        task=task,
        runtime=runtime,
        modes=tuple(modes),
        loop=loop,
        turn_strategy=turn_strategy,
        features=features,
        completion=completion,
        concurrency=concurrency,
        permissions=permissions,
        enhanced_tools=enhanced_tools,
        replay=replay,
        long_running=long_running,
        terminal_sessions=terminal_sessions,
        observability=observability,
        sampling=sampling,
        optimizer_mutable_pointers=tuple(sorted(set(pointers))),
    )


def _compiler_identity(options: CompileOptions) -> CompilerIdentity:
    return CompilerIdentity(
        compiler_version=COMPILER_VERSION,
        compiler_code_digest=_compiler_implementation_digest(),
        config_schema_digest=CONFIG_SCHEMA_DIGEST,
        manifest_schema_digest=MANIFEST_SCHEMA_DIGEST,
        manifest_schema_version=_contracts_module.COMPILED_CONFIG_MANIFEST_SCHEMA_VERSION,
        runtime_abi=options.target.runtime_abi,
    )


def _compiler_input_preimage(
    compiler: CompilerIdentity,
    closure: DependencyClosureManifest,
    options: CompileOptions,
) -> dict[str, Any]:
    return {
        "schema": COMPILER_INPUT_SCHEMA_ID,
        "bundle_digest": closure.bundle_digest,
        "closure_digest": closure.closure_digest,
        "entrypoint": closure.root_entrypoint,
        "compiler_id": compiler.compiler_id,
        "compiler_version": compiler.compiler_version,
        "compiler_code_digest": compiler.compiler_code_digest,
        "config_schema_id": compiler.config_schema_id,
        "config_schema_version": compiler.config_schema_version,
        "config_schema_digest": compiler.config_schema_digest,
        "manifest_schema_id": compiler.manifest_schema_id,
        "manifest_schema_version": compiler.manifest_schema_version,
        "manifest_schema_digest": compiler.manifest_schema_digest,
        "canonicalizer_id": CANONICALIZER_ID,
        "runtime_abi": options.target.runtime_abi,
        "compile_options": options.to_canonical_obj(),
    }


def compile_config(
    reader: ManifestReader,
    closure: DependencyClosureManifest,
    options: CompileOptions,
) -> CompiledConfigManifest:
    """Compile exactly ``closure`` through ``reader`` into immutable canonical IR.

    This function performs no ambient filesystem, environment, network, cache,
    clock, randomness, dynamic-import, admission, or runtime operation.
    """

    if not isinstance(reader, ManifestReader):
        raise _error(CompileStage.READER_INTEGRITY, CompileErrorCode.READER_INTEGRITY, details={"reason": "reader_type"})
    if not isinstance(closure, DependencyClosureManifest):
        raise _error(CompileStage.READER_INTEGRITY, CompileErrorCode.CLOSURE_MISMATCH, details={"reason": "closure_type"})
    try:
        revalidated_closure = DependencyClosureManifest.from_json(closure.canonical_bytes())
    except BundleError as exc:
        raise _error(CompileStage.READER_INTEGRITY, CompileErrorCode.CLOSURE_MISMATCH, details={"reason": "closure_revalidation_failed"}) from exc
    if revalidated_closure != closure:
        raise _error(CompileStage.READER_INTEGRITY, CompileErrorCode.CLOSURE_MISMATCH, details={"reason": "closure_revalidation_mismatch"})
    if not isinstance(options, CompileOptions):
        raise _error(CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, details={"field": "options"})
    ledger = _ReadLedger(reader, closure)
    merged, raw_provenance = _resolve_inheritance(ledger)
    losses: tuple[LossRecord, ...] = ()
    notices: tuple[NoticeRecord, ...] = ()
    if options.source_contract == "v1_shadow":
        merged, losses, notices = _translate_v1_shadow(merged, closure.root_entrypoint)
        raw_provenance = _remap_v1_provenance(raw_provenance)
    _validate_root_schema(merged, options)
    defaults: list[DefaultRecord] = []
    semantic = _build_semantic(merged, ledger, options, defaults, raw_provenance)
    semantic = _finalize_semantic_identity(semantic)
    dependencies = ledger.finish()
    provenance = _semantic_provenance(raw_provenance, semantic, defaults, dependencies)
    compiler = _compiler_identity(options)
    input_digest = canonical_sha256(_compiler_input_preimage(compiler, closure, options))
    inputs = CompileInputIdentity(
        bundle_digest=closure.bundle_digest,
        closure_digest=closure.closure_digest,
        entrypoint=closure.root_entrypoint,
        options=options,
        compiler_input_digest=input_digest,
    )
    semantic_digest = canonical_sha256(
        {"schema": COMPILED_CONFIG_SEMANTIC_SCHEMA_ID, "config": semantic.to_canonical_obj()}
    )
    return CompiledConfigManifest(
        compiler=compiler,
        inputs=inputs,
        source_dependencies=dependencies,
        semantic=semantic,
        provenance=provenance,
        diagnostics=CompileDiagnostics(
            defaults=tuple(defaults), losses=losses, notices=notices
        ),
        semantic_digest=semantic_digest,
    )


def compiler_cache_key(
    closure: DependencyClosureManifest, options: CompileOptions
) -> str:
    """Return the only valid external cache key without performing cache I/O."""

    return canonical_sha256(
        _compiler_input_preimage(_compiler_identity(options), closure, options)
    )


def verify_cached_manifest(
    payload: bytes, *, expected_compiler_input_digest: str
) -> CompiledConfigManifest:
    """Validate externally supplied cached content and its bound compiler input."""

    try:
        manifest = CompiledConfigManifest.from_json(payload)
    except (BundleError, ConfigCompileError) as exc:
        raise _error(
            CompileStage.IDENTITY,
            CompileErrorCode.MANIFEST_IDENTITY_MISMATCH,
            details={"reason": "cached_manifest_invalid"},
        ) from exc
    if manifest.inputs.compiler_input_digest != expected_compiler_input_digest:
        raise _error(
            CompileStage.IDENTITY,
            CompileErrorCode.COMPILER_INPUT_MISMATCH,
            details={
                "actual": manifest.inputs.compiler_input_digest,
                "expected": expected_compiler_input_digest,
            },
        )
    return manifest


COMPILER_CODE_DIGEST: Final = _compiler_implementation_digest()

__all__ = [
    "BUILTIN_TOOL_RENDERER_ID",
    "COMPILER_CODE_DIGEST",
    "COMPILER_VERSION",
    "CONFIG_SCHEMA_DIGEST",
    "MANIFEST_SCHEMA_DIGEST",
    "V1_MAPPING_TABLE",
    "V1_MAPPING_TABLE_DIGEST",
    "compile_config",
    "compiler_cache_key",
    "deep_merge_with_provenance",
    "strict_parse_payload",
    "translate_v1_shadow",
    "verify_cached_manifest",
]
