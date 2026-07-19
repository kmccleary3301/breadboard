from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from dataclasses import FrozenInstanceError, replace
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from agentic_coder_prototype.compilation.bundle import (
    ManifestReader,
    build_dependency_closure,
    ingest_member_map,
    ingest_directory,
    ingest_zip,
)
from agentic_coder_prototype.compilation.contracts import (
    COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
    MAX_SAFE_INTEGER,
    BundleIntegrityError,
    BundleLimits,
    BundleLimitError,
    BundleValidationError,
    CanonicalJSONError,
    CompileErrorCode,
    CompileOptions,
    CompileStage,
    CompiledConfig,
    CompileTarget,
    ConfigBundleManifest,
    ConfigCompileError,
    DependencyClosureManifest,
    DependencyEdge,
    TaskContract,
    TaskEvidenceContract,
    TaskRetentionContract,
    TaskVerifierContract,
    canonical_json_bytes,
    canonical_json_loads,
    canonical_sha256,
)
from agentic_coder_prototype.compilation import server_compiler
from agentic_coder_prototype.compilation.server_compiler import (
    compile_config,
    compiler_cache_key,
    verify_cached_manifest,
    strict_parse_payload,
)
from breadboard.rl.state import InMemoryCAS


_MINIMAL_CONFIG = b"""version: 2
profile:
  name: generated-unclassified-agent
workspace:
  root: workspace
providers:
  default_model: test-model
  models:
    - id: test-model
      adapter: openai
      params:
        temperature: 0.25
prompts:
  injection:
    system_order: []
    per_turn_order: []
modes:
  - id: build
    prompt: ''
loop:
  sequence: [build]
"""


def _options(
    *,
    runtime_abi: str = "breadboard.conductor.v1",
    source_contract: str = "v2",
    v1_loss_policy: str = "reject_all",
) -> CompileOptions:
    return CompileOptions(
        target=CompileTarget(
            runner_adapter_id="breadboard.conductor.v1",
            runtime_abi=runtime_abi,
        ),
        task_contract=TaskContract(
            contract_id="swe-task.v1",
            parameter_schema={
                "type": "object",
                "properties": {"instruction": {"type": "string"}},
                "required": ["instruction"],
                "additionalProperties": False,
            },
            artifacts=(),
            verifier=TaskVerifierContract(
                binding_id=None,
                input_artifact_ids=(),
                result_schema={
                    "type": "object",
                    "properties": {"passed": {"type": "boolean"}},
                    "required": ["passed"],
                    "additionalProperties": False,
                },
                timeout_ms=30_000,
            ),
            evidence=TaskEvidenceContract(
                required_event_types=("turn.completed",),
                required_artifact_ids=(),
            ),
            retention=TaskRetentionContract(
                retention_class_id="test-evidence",
                minimum_retention_seconds=60,
            ),
        ),
        source_contract=source_contract,
        v1_loss_policy=v1_loss_policy,
    )


def _inputs(
    members: dict[str, bytes] | None = None,
    *,
    edges: tuple[DependencyEdge, ...] = (),
    root: str = "config.yaml",
    limits: BundleLimits | None = None,
) -> tuple[ManifestReader, object, object]:
    cas = InMemoryCAS()
    bundle = ingest_member_map(
        members or {root: _MINIMAL_CONFIG},
        cas,
        entrypoints={"main": root},
        source_label="compiler-test-vector",
        limits=limits,
    )
    closure = build_dependency_closure(
        bundle,
        root_entrypoint="main",
        edges=edges,
    )
    return ManifestReader(cas=cas, bundle=bundle, closure=closure), closure, bundle


def _compile(
    members: dict[str, bytes] | None = None,
    *,
    edges: tuple[DependencyEdge, ...] = (),
    root: str = "config.yaml",
    options: CompileOptions | None = None,
):
    reader, closure, bundle = _inputs(members, edges=edges, root=root)
    return compile_config(reader, closure, options or _options()), closure, bundle


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()




def test_compile_config_emits_closed_runtime_semantics() -> None:
    manifest, closure, bundle = _compile()
    semantic = manifest.semantic.to_canonical_obj()

    assert manifest.inputs.bundle_digest == bundle.bundle_digest
    assert manifest.inputs.closure_digest == closure.closure_digest
    assert manifest.inputs.entrypoint == "config.yaml"
    assert manifest.compiler.runtime_abi == "breadboard.conductor.v1"
    assert semantic["providers"]["default_model_id"] == "test-model"
    assert semantic["providers"]["models"][0]["params"]["temperature"] == 0.25
    assert [mode["mode_id"] for mode in semantic["modes"]] == ["build"]
    assert semantic["task"]["contract_id"] == "swe-task.v1"
    assert semantic["task"]["verifier"]["timeout_ms"] == 30_000
    assert semantic["task"]["evidence"]["required_event_types"] == [
        "turn.completed"
    ]

    required_families = {
        "metadata", "providers", "prompts", "tools", "plugins", "guardrails",
        "task", "runtime", "modes", "loop", "turn_strategy", "features",
        "completion", "concurrency", "permissions", "enhanced_tools", "replay",
        "long_running", "terminal_sessions", "observability",
        "optimizer_mutable_pointers",
    }
    assert required_families <= semantic.keys()
    assert manifest.diagnostics.losses == ()
    assert manifest.source_dependencies[0].logical_path == "config.yaml"
    assert manifest.source_dependencies[0].blob_digest == bundle.entries[0].blob_digest


def test_compile_config_reports_defaults_provenance_and_losses() -> None:
    manifest, _, _ = _compile()

    default_pointers = {record.target_pointer for record in manifest.diagnostics.defaults}
    provenance_pointers = {record.target_pointer for record in manifest.provenance}
    assert "/turn_strategy" in default_pointers
    assert {
        "/runtime/sandbox/driver_id",
        "/runtime/sandbox/network_request",
    } <= default_pointers
    assert "/providers/default_model_id" in provenance_pointers
    assert "/modes/0/mode_id" in provenance_pointers
    assert all(record.runner_visible is False for record in manifest.diagnostics.losses)


def test_digest_equations() -> None:
    manifest, _, _ = _compile()

    semantic_preimage = canonical_json_bytes(
        {
            "schema": COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
            "config": manifest.semantic.to_canonical_obj(),
        }
    )
    complete_preimage = canonical_json_bytes(
        manifest.to_canonical_obj(include_digest=False)
    )
    compiler_input_object = manifest.compiler_input_preimage()
    compiler_input_preimage = canonical_json_bytes(compiler_input_object)

    assert manifest.semantic_digest == _sha256(semantic_preimage)
    assert manifest.compiled_manifest_digest == _sha256(complete_preimage)
    assert manifest.inputs.compiler_input_digest == _sha256(compiler_input_preimage)
    assert set(compiler_input_object) == {
        "schema",
        "bundle_digest",
        "closure_digest",
        "entrypoint",
        "compiler_id",
        "compiler_version",
        "compiler_code_digest",
        "config_schema_id",
        "config_schema_version",
        "config_schema_digest",
        "manifest_schema_id",
        "manifest_schema_version",
        "manifest_schema_digest",
        "canonicalizer_id",
        "runtime_abi",
        "compile_options",
    }
    assert compiler_input_object["compiler_code_digest"] == (
        manifest.compiler.compiler_code_digest
    )
    assert compiler_input_object["config_schema_digest"] == (
        manifest.compiler.config_schema_digest
    )
    assert compiler_input_object["manifest_schema_digest"] == (
        manifest.compiler.manifest_schema_digest
    )
    assert compiler_input_object["runtime_abi"] == _options().target.runtime_abi
    assert compiler_input_object["compile_options"] == _options().to_canonical_obj()
    for digest in (
        manifest.inputs.bundle_digest,
        manifest.inputs.closure_digest,
        manifest.inputs.compiler_input_digest,
        manifest.semantic_digest,
        manifest.compiled_manifest_digest,
    ):
        assert digest.startswith("sha256:") and len(digest) == 71


def test_repeated_compile_bytes_equal() -> None:
    first, _, _ = _compile()
    second, _, _ = _compile()
    assert first.canonical_bytes() == second.canonical_bytes()


def test_same_semantics_from_different_raw_bytes_separates_source_identity() -> None:
    formatted = b"""# formatting must affect source identity only
version: 2
profile: {name: generated-unclassified-agent}
workspace: {root: workspace}
providers: {models: [{adapter: openai, params: {temperature: 0.25}, id: test-model}], default_model: test-model}
prompts: {injection: {system_order: [], per_turn_order: []}}
modes: [{prompt: '', id: build}]
loop: {sequence: [build]}
"""
    first, _, _ = _compile()
    second, _, _ = _compile({"config.yaml": formatted})

    assert first.inputs.bundle_digest != second.inputs.bundle_digest
    assert first.inputs.closure_digest != second.inputs.closure_digest
    assert first.inputs.compiler_input_digest != second.inputs.compiler_input_digest
    assert first.compiled_manifest_digest != second.compiled_manifest_digest
    assert first.semantic_digest == second.semantic_digest
    assert canonical_json_bytes(_runner_visible_projection(first)) == canonical_json_bytes(
        _runner_visible_projection(second)
    )


def test_compile_options_bind_compiler_input_and_semantic_target() -> None:
    first, _, _ = _compile()
    second, _, _ = _compile(options=_options(runtime_abi="breadboard.conductor.v2"))

    assert first.inputs.compiler_input_digest != second.inputs.compiler_input_digest
    assert first.semantic_digest != second.semantic_digest
    assert first.compiled_manifest_digest != second.compiled_manifest_digest
    assert first.semantic.runtime["runtime_abi"] == "breadboard.conductor.v1"
    assert second.semantic.runtime["runtime_abi"] == "breadboard.conductor.v2"


def test_generated_unknown_display_name_does_not_dispatch_a_family() -> None:
    renamed = _MINIMAL_CONFIG.replace(
        b"generated-unclassified-agent", b"name-never-seen-before-7f42"
    )
    baseline, _, _ = _compile()
    changed, _, _ = _compile({"config.yaml": renamed})

    assert changed.semantic.metadata["display_name"] == "name-never-seen-before-7f42"
    assert changed.semantic.providers == baseline.semantic.providers
    assert changed.semantic.tools == baseline.semantic.tools
    assert changed.semantic.runtime == baseline.semantic.runtime


@pytest.mark.parametrize(
    ("payload", "stage", "code"),
    [
        (b"version: 2\nversion: 2\n", CompileStage.PARSE, CompileErrorCode.DUPLICATE_MAPPING_KEY),
        (b"version: 2\nvalue: !!python/object:os.system {}\n", CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_TAG),
        (b"version: 2\nvalue: 2026-07-10\n", CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_SCALAR),
        (b"version: 2\nvalue: .nan\n", CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_SCALAR),
        (b"version: 2\nvalue: .inf\n", CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_SCALAR),
        (b"version: 2\nvalue: 9007199254740992\n", CompileStage.PARSE, CompileErrorCode.NUMBER_OUT_OF_RANGE),
        (b"version: 2\nanchor: &x {value: one}\nalias: *x\n", CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_TAG),
        (b"version: 2\nvalue: yes\n", CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_SCALAR),
        (b"version: 2\n2: value\n", CompileStage.PARSE, CompileErrorCode.UNSUPPORTED_YAML_SCALAR),
    ],
)
def test_strict_parser_rejects_ambiguous_cross_language_values(
    payload: bytes,
    stage: CompileStage,
    code: CompileErrorCode,
) -> None:
    reader, closure, _ = _inputs({"config.yaml": payload})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is stage
    assert caught.value.code is code


def test_unknown_top_level_field_is_a_typed_rejection() -> None:
    payload = _MINIMAL_CONFIG + b"surprise_runtime_authority: true\n"
    reader, closure, _ = _inputs({"config.yaml": payload})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is CompileErrorCode.SCHEMA_UNKNOWN_FIELD
    assert caught.value.instance_pointer == "/surprise_runtime_authority"


@pytest.mark.parametrize(
    ("source_pointer", "anchor", "fragment", "marker"),
    [
        ("/profile/version", b"profile:\n", b"  version: marker-profile-version\n", "marker-profile-version"),
        ("/profile/metadata", b"profile:\n", b"  metadata: {fixture: marker-profile-metadata}\n", "marker-profile-metadata"),
        ("/workspace/mirror", b"workspace:\n", b"  mirror: marker-workspace-mirror\n", "marker-workspace-mirror"),
        ("/workspace/driver", b"workspace:\n", b"  driver: marker-workspace-driver\n", "marker-workspace-driver"),
        ("/workspace/options", b"workspace:\n", b"  options: {fixture: marker-workspace-options}\n", "marker-workspace-options"),
        ("/workspace/network", b"workspace:\n", b"  network: {fixture: marker-workspace-network}\n", "marker-workspace-network"),
        ("/workspace/image", b"workspace:\n", b"  image: {fixture: marker-workspace-image}\n", "marker-workspace-image"),
        ("/workspace/resources", b"workspace:\n", b"  resources: {fixture: marker-workspace-resources}\n", "marker-workspace-resources"),
        ("/providers/policy_slots", b"providers:\n", b"  policy_slots: [{fixture: marker-provider-policy-slots}]\n", "marker-provider-policy-slots"),
        ("/tools/defs_dir", b"modes:\n", b"tools:\n  defs_dir: marker-tools-defs-dir\n", "marker-tools-defs-dir"),
        ("/tools/enabled", b"modes:\n", b"tools:\n  enabled: [marker-tools-enabled]\n", "marker-tools-enabled"),
        ("/tools/dialects", b"modes:\n", b"tools:\n  dialects: {fixture: marker-tools-dialects}\n", "marker-tools-dialects"),
        ("/plugins/search_paths", b"modes:\n", b"plugins:\n  enabled: false\n  search_paths: [marker-plugin-search-path]\n", "marker-plugin-search-path"),
        ("/claude", None, b"claude: {fixture: marker-top-level-claude}\n", "marker-top-level-claude"),
        ("/max_iterations", None, b"max_iterations: marker-top-level-max-iterations\n", "marker-top-level-max-iterations"),
    ],
)
def test_present_fields_are_semantic_explicit_losses_or_exact_denials(
    source_pointer: str,
    anchor: bytes | None,
    fragment: bytes,
    marker: str,
) -> None:
    if anchor is None:
        payload = _MINIMAL_CONFIG + fragment
    elif source_pointer.startswith(("/tools/", "/plugins/")):
        payload = _MINIMAL_CONFIG.replace(anchor, fragment + anchor, 1)
    else:
        payload = _MINIMAL_CONFIG.replace(anchor, anchor + fragment, 1)
    reader, closure, _ = _inputs({"config.yaml": payload})

    try:
        manifest = compile_config(reader, closure, _options())
    except ConfigCompileError as caught:
        assert caught.stage is CompileStage.SCHEMA
        assert caught.code is CompileErrorCode.SCHEMA_UNKNOWN_FIELD
        assert caught.instance_pointer is not None
        return

    if marker.encode() in manifest.semantic.canonical_bytes():
        return
    matching_losses = [
        loss
        for loss in manifest.diagnostics.losses
        if loss.source_logical_path == "config.yaml"
        and loss.source_pointer == source_pointer
    ]
    assert len(matching_losses) == 1
    assert matching_losses[0].runner_visible is False


def test_mark_task_complete_is_a_semantic_boolean() -> None:
    payload = _MINIMAL_CONFIG.replace(
        b"modes:\n", b"tools:\n  mark_task_complete: true\nmodes:\n", 1
    )
    manifest, _, _ = _compile({"config.yaml": payload})

    assert manifest.semantic.tools["mark_task_complete"] is True


def test_workspace_mount_scalar_member_is_an_exact_typed_denial() -> None:
    payload = _MINIMAL_CONFIG.replace(
        b"workspace:\n", b"workspace:\n  mounts: [not-a-mount-object]\n", 1
    )
    reader, closure, _ = _inputs({"config.yaml": payload})

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is CompileErrorCode.SCHEMA_TYPE_MISMATCH
    assert caught.value.instance_pointer == "/runtime/sandbox/mount_requests/0"


def test_extends_consumes_exact_declared_edges_in_order() -> None:
    members = {
        "configs/base-a.yaml": b"features:\n  plan: false\ncompletion:\n  confidence_threshold: 0.1\n",
        "configs/base-b.yaml": b"features:\n  plan: true\ncompletion:\n  confidence_threshold: 0.6\n",
        "configs/child.yaml": _MINIMAL_CONFIG.replace(
            b"version: 2\n",
            b"version: 2\nextends: [base-a.yaml, base-b.yaml]\n",
        ),
    }
    edges = (
        DependencyEdge(
            from_path="configs/child.yaml",
            kind="extends",
            raw_ref="base-a.yaml",
            logical_path="configs/base-a.yaml",
            ordinal=0,
        ),
        DependencyEdge(
            from_path="configs/child.yaml",
            kind="extends",
            raw_ref="base-b.yaml",
            logical_path="configs/base-b.yaml",
            ordinal=1,
        ),
    )
    manifest, _, _ = _compile(members, edges=edges, root="configs/child.yaml")

    assert manifest.semantic.features["plan"] is True
    assert manifest.semantic.completion["confidence_threshold"] == 0.6
    features_provenance = next(
        record for record in manifest.provenance if record.target_pointer == "/features/plan"
    )
    assert [item.logical_path for item in features_provenance.contributions] == [
        "configs/base-a.yaml",
        "configs/base-b.yaml",
    ]
    assert features_provenance.contributions[0].shadowed is True
    assert features_provenance.contributions[1].shadowed is False


def test_reference_without_exact_closure_edge_is_rejected() -> None:
    payload = _MINIMAL_CONFIG.replace(
        b"version: 2\n", b"version: 2\nextends: base.yaml\n"
    )
    reader, closure, _ = _inputs({"config.yaml": payload})

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.stage is CompileStage.DEPENDENCY_RESOLUTION
    assert caught.value.code is CompileErrorCode.REFERENCE_UNDECLARED
    assert caught.value.logical_path == "config.yaml"
    assert caught.value.dependency_kind == "extends"
    assert caught.value.raw_reference == "base.yaml"


def test_duplicate_reference_edges_are_an_exact_typed_denial() -> None:
    payload = _MINIMAL_CONFIG.replace(
        b"version: 2\n", b"version: 2\nextends: base.yaml\n"
    )
    members = {
        "config.yaml": payload,
        "base-a.yaml": b"features: {base: a}\n",
        "base-b.yaml": b"features: {base: b}\n",
    }
    edges = (
        DependencyEdge("config.yaml", "extends", "base.yaml", "base-a.yaml", 0),
        DependencyEdge("config.yaml", "extends", "base.yaml", "base-b.yaml", 1),
    )
    reader, closure, _ = _inputs(members, edges=edges)

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.stage is CompileStage.DEPENDENCY_RESOLUTION
    assert caught.value.code is CompileErrorCode.REFERENCE_AMBIGUOUS
    assert caught.value.logical_path == "config.yaml"
    assert caught.value.dependency_kind == "extends"
    assert caught.value.raw_reference == "base.yaml"
    assert caught.value.related_logical_paths == ("base-a.yaml", "base-b.yaml")


def test_cyclic_inheritance_is_an_exact_typed_denial() -> None:
    members = {
        "config.yaml": _MINIMAL_CONFIG.replace(
            b"version: 2\n", b"version: 2\nextends: base.yaml\n"
        ),
        "base.yaml": b"extends: config.yaml\nfeatures: {base: true}\n",
    }
    edges = (
        DependencyEdge("config.yaml", "extends", "base.yaml", "base.yaml", 0),
        DependencyEdge("base.yaml", "extends", "config.yaml", "config.yaml", 0),
    )
    with pytest.raises(
        BundleValidationError,
        match="dependency closure contains a cycle",
    ):
        _inputs(members, edges=edges)


def test_unexplained_closure_member_is_rejected() -> None:
    members = {"config.yaml": _MINIMAL_CONFIG, "unused.yaml": b"features: {}\n"}
    edges = (
        DependencyEdge(
            from_path="config.yaml",
            kind="member",
            raw_ref="unused.yaml",
            logical_path="unused.yaml",
            ordinal=0,
        ),
    )
    reader, closure, _ = _inputs(members, edges=edges)

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.code is CompileErrorCode.CLOSURE_MISMATCH
    assert caught.value.related_logical_paths == ("unused.yaml",)


_RICH_CONFIG = b"""version: 2
profile:
  name: rich-manifest-vector
workspace:
  root: workspace
  sandbox:
    driver_id: docker
    options: {read_only_root: true}
    mount_requests: []
    network_request: {mode: none}
    image_request: {image_id: image:test}
    resource_request: {cpu: 2, memory_mb: 1024}
providers:
  default_model: test-model
  models:
    - id: test-model
      adapter: openai_responses
      context_length: 32000
      params: {temperature: 0.2}
      route_handle_id: route:test
      credential_handle_id: credential:test
      trainable_json_pointers: [/params/temperature]
provider_tools:
  use_native: true
  api_variant: responses
prompts:
  tool_prompt_mode: system_compiled_and_persistent_per_turn
  packs:
    base:
      system: {source: prompts/system.txt}
      shared: {literal: Shared rules.}
  injection:
    system_order: ['@pack(base).system', '@pack(base).shared']
    per_turn_order: [mode_specific, '@pack(base).shared']
  dialects:
    default: [pythonic, json]
  dedupe: true
tools:
  registry:
    paths: [tools]
    include: [read_file]
    exclude: []
  overlays:
    - rename: {read-file: read_file}
      descriptions: {read-file: Read a declared file exactly.}
      syntax_style: {read-file: json}
      provider_preference: {read-file: {openai: {strict: true}}}
  aliases: {read: read_file}
tool_packs:
  core:
    description: Core read tools
    tools: [read_file]
    exposure: model
    support_status: supported
tool_bindings:
  - id: binding:read
    tool_id: read_file
    binding_kind: server
    execution_profile: sandboxed
    placement: server
    exposure: model
    support_status: supported
modes:
  - id: plan
    prompt: {source: prompts/plan.txt}
    tools_enabled: [read_file]
    dialects: [pythonic, json]
  - id: build
    prompt: '@pack(base).shared'
    tools_enabled: ['*']
loop:
  sequence: [plan, build]
  limits: {max_iterations: 20}
  turn_strategy: {flow: tool_role}
turn_strategy: {relay: continuation, allow_multiple_per_turn: false}
features: {plan: true, todos: {enabled: true}}
completion: {confidence_threshold: 0.7, allow_zero_tool_completion: true}
concurrency: {max_parallel_tools: 2}
permissions: {default: ask}
enhanced_tools: {enabled: true}
guardrails:
  include: [{source: guardrails/base.yaml}]
plugins:
  enabled: true
  manifest_refs: [{source: plugins/demo.yaml}]
  trust_requests: {demo.plugin: untrusted}
multi_agent:
  enabled: true
  team_config: {source: teams/main.yaml}
task_tool:
  id: task
  description_template_path: {source: templates/task.txt}
  subagents: {reviewer: {role: review}, worker: {role: edit}}
terminal_sessions: {enabled: true, max_sessions: 2}
long_running: {enabled: true, budget: {turns: 100}}
replay: {strict: true}
logging: {level: info}
telemetry: {sink_slot_id: telemetry:events}
optimizer_mutable_pointers: [/completion/confidence_threshold, /providers/models/0/params/temperature]
"""

_RICH_MEMBERS = {
    "config.yaml": _RICH_CONFIG,
    "prompts/system.txt": b"[CACHE] System rules.\n",
    "prompts/plan.txt": b"Plan carefully.\n",
    "tools/read.yaml": b"""id: read-file
name: read_file_original
description: Read a file.
type_id: python
parameters:
  - name: path
    schema:
      type: string
      minLength: 1
      pattern: '^[^\\x00]+$'
    description: Logical path
    required: true
    examples: [src/main.py]
  - name: line
    type: integer
    required: false
    default: 1
    validation: {minimum: 1}
manipulations: [read]
syntax_formats_supported: [python]
preferred_formats: [python]
use_cases: [inspection]
performance_data: {latency_class: low}
dependencies: []
execution: {blocking: false, max_per_turn: 8}
provider_routing: {openai: {strict: false, additionalProperties: false}}
""",
    "guardrails/base.yaml": b"""schema_version: 1
description: Base guardrails
guards:
  - id: no-secrets
    type: regex
    enabled: true
    templates:
      violation: {source: templates/guardrail.txt}
    parameters: {pattern: secret}
""",
    "templates/guardrail.txt": b"Blocked {{ value }}\n",
    "plugins/demo.yaml": b"""id: demo.plugin
version: '1.0'
name: Demo plugin
description: Explicit plugin
permissions: {workspace_read: true}
runtime:
  kind: mcp
  server_id: demo-server
  operator_binding_id: binding:mcp-demo
  requested_tool_ids: [read-file]
  requested_route_handle_ids: [route:mcp-demo]
skills:
  - id: demo-skill
    kind: prompt
    members: [{source: skills/demo.txt}]
""",
    "skills/demo.txt": b"Use the declared skill only.\n",
    "teams/main.yaml": b"""team_id: test-team
agents:
  - id: reviewer
    config_node_id: root
coordination: {strategy: sequential}
""",
    "templates/task.txt": b"Delegate to {agents}.\n",
}


def _rich_edges() -> tuple[DependencyEdge, ...]:
    specifications = (
        ("config.yaml", "prompt", "prompts/system.txt", "prompts/system.txt"),
        ("config.yaml", "mode_prompt", "prompts/plan.txt", "prompts/plan.txt"),
        ("config.yaml", "tool_registry", "tools", "tools/read.yaml"),
        ("config.yaml", "guardrail", "guardrails/base.yaml", "guardrails/base.yaml"),
        ("guardrails/base.yaml", "guardrail_template", "templates/guardrail.txt", "templates/guardrail.txt"),
        ("config.yaml", "plugin_manifest", "plugins/demo.yaml", "plugins/demo.yaml"),
        ("plugins/demo.yaml", "plugin_skill", "skills/demo.txt", "skills/demo.txt"),
        ("config.yaml", "team_config", "teams/main.yaml", "teams/main.yaml"),
        ("config.yaml", "task_template", "templates/task.txt", "templates/task.txt"),
    )
    return tuple(
        DependencyEdge(
            from_path=from_path,
            kind=kind,
            raw_ref=raw_ref,
            logical_path=logical_path,
            ordinal=0,
        )
        for from_path, kind, raw_ref, logical_path in specifications
    )


def test_compile_config_resolves_rich_prompts_tools_plugins_guardrails_and_team() -> None:
    manifest, _, _ = _compile(_RICH_MEMBERS, edges=_rich_edges())
    semantic = manifest.semantic.to_canonical_obj()

    assert len(semantic["prompts"]["variants"]) == 2
    plan_variant = next(
        item
        for item in semantic["prompts"]["variants"]
        if item["mode_id"] == "plan" and item["dialect_ids"] == ["pythonic", "json"]
    )
    assert plan_variant["system"]["text"] == "System rules.\n\nShared rules."
    assert plan_variant["per_turn"]["text"] == "Plan carefully.\n\nShared rules."

    tool = semantic["tools"]["definitions"][0]
    assert tool["tool_id"] == "read-file"
    assert tool["model_name"] == "read_file"
    assert tool["parameters"][0]["schema"] == {
        "type": "string",
        "minLength": 1,
        "pattern": "^[^\\x00]+$",
    }
    assert tool["parameters"][1]["has_default"] is True
    assert tool["parameters"][1]["default_value"] == 1
    assert tool["execution"] == {"blocking": False, "max_per_turn": 8}
    assert tool["provider_routing"]["openai"] == {
        "strict": True,
        "additionalProperties": False,
    }
    assert semantic["tools"]["aliases"] == [["read", "read-file"]]
    assert semantic["tools"]["selected_tool_ids"] == ["read-file"]
    assert semantic["tools"]["binding_requests"][0]["binding_id"] == "binding:read"

    assert semantic["guardrails"]["definitions"][0]["templates"][0][1]["text"] == "Blocked {{ value }}\n"
    plugin = semantic["plugins"]["plugins"][0]
    assert plugin["trust_request"] == "untrusted"
    assert plugin["skills"][0]["compiled_payload"] == ["Use the declared skill only.\n"]
    assert plugin["mcp_requests"][0]["operator_binding_id"] == "binding:mcp-demo"
    assert semantic["team"]["team_id"] == "test-team"
    assert semantic["task"]["task_tool"]["rendered_description"] == "Delegate to reviewer, worker.\n"

    assert semantic["runtime"]["route_handle_ids"] == ["route:test"]
    assert semantic["runtime"]["credential_handle_ids"] == ["credential:test"]
    assert semantic["runtime"]["limits"] == {"max_iterations": 20}
    assert semantic["turn_strategy"] == {
        "relay": "continuation",
        "allow_multiple_per_turn": False,
        "flow": "tool_role",
    }
    assert semantic["optimizer_mutable_pointers"] == [
        "/completion/confidence_threshold",
        "/providers/models/0/params/temperature",
    ]

    consumed = {dependency.logical_path for dependency in manifest.source_dependencies}
    assert consumed == set(_RICH_MEMBERS)


_NESTED_TEAM = b"""team_id: nested-team
agents:
  - id: reviewer
    role: review
    config_ref: agents/reviewer.yaml
    read_only: true
    allow_spawn: false
    description: Reviews declared work.
coordination: {strategy: sequential}
"""


def _nested_team_members() -> dict[str, bytes]:
    root = _MINIMAL_CONFIG.replace(
        b"modes:\n",
        b"multi_agent:\n  enabled: true\n  team_config: {source: teams/main.yaml}\nmodes:\n",
    )
    reviewer = _MINIMAL_CONFIG.replace(
        b"generated-unclassified-agent",
        b"declared-reviewer",
    )
    return {
        "config.yaml": root,
        "teams/main.yaml": _NESTED_TEAM,
        "agents/reviewer.yaml": reviewer,
    }


def test_declared_nested_agent_config_compiles_once_to_a_config_node() -> None:
    edges = (
        DependencyEdge(
            "config.yaml",
            "team_config",
            "teams/main.yaml",
            "teams/main.yaml",
            0,
        ),
        DependencyEdge(
            "teams/main.yaml",
            "team_agent_config",
            "agents/reviewer.yaml",
            "agents/reviewer.yaml",
            0,
        ),
    )
    manifest, _, _ = _compile(_nested_team_members(), edges=edges)
    semantic = manifest.semantic.to_canonical_obj()
    nodes = {node["node_id"]: node for node in semantic["config_nodes"]}
    root_node_id = semantic["root_config_node_id"]
    reviewer_node_id = semantic["team"]["agents"][0]["config_node_id"]

    assert semantic["root_config_node_id"] == root_node_id
    assert set(nodes) == {root_node_id, reviewer_node_id}
    assert all(set(node) == {"node_id", "semantic_config"} for node in nodes.values())
    assert nodes[reviewer_node_id]["semantic_config"]["metadata"]["display_name"] == (
        "declared-reviewer"
    )
    assert semantic["team"]["agents"] == [
        {
            "agent_id": "reviewer",
            "role": "review",
            "config_node_id": reviewer_node_id,
            "entrypoint": True,
            "read_only": True,
            "allow_spawn": False,
            "description": "Reviews declared work.",
        }
    ]
    assert b"config_ref" not in canonical_json_bytes(semantic["team"])
    nested_dependency = next(
        dependency
        for dependency in manifest.source_dependencies
        if dependency.dependency_kind == "team_agent_config"
    )
    assert nested_dependency.from_logical_path == "teams/main.yaml"
    assert nested_dependency.raw_reference == "agents/reviewer.yaml"
    assert nested_dependency.logical_path == "agents/reviewer.yaml"


def test_missing_nested_agent_edge_is_an_exact_typed_denial() -> None:
    members = _nested_team_members()
    members.pop("agents/reviewer.yaml")
    team_edge = DependencyEdge(
        "config.yaml",
        "team_config",
        "teams/main.yaml",
        "teams/main.yaml",
        0,
    )
    reader, closure, _ = _inputs(members, edges=(team_edge,))

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.stage is CompileStage.DEPENDENCY_RESOLUTION
    assert caught.value.code is CompileErrorCode.REFERENCE_UNDECLARED
    assert caught.value.logical_path == "teams/main.yaml"
    assert caught.value.dependency_kind == "team_agent_config"
    assert caught.value.raw_reference == "agents/reviewer.yaml"


@pytest.mark.parametrize(
    ("field", "raw_ref", "edge_kind"),
    [
        (b"templates: {catalog: {source: templates/catalog.j2}}", "templates/catalog.j2", "prompt_template"),
        (b"tool_catalog: {source: templates/tools.j2}", "templates/tools.j2", "prompt_template"),
    ],
)
def test_declared_prompt_templates_are_closure_consumed_and_identity_bound(
    field: bytes,
    raw_ref: str,
    edge_kind: str,
) -> None:
    payload = _MINIMAL_CONFIG.replace(
        b"prompts:\n  injection:", b"prompts:\n  " + field + b"\n  injection:",
    )
    edges = (
        DependencyEdge("config.yaml", edge_kind, raw_ref, raw_ref, 0),
    )
    first, _, _ = _compile(
        {"config.yaml": payload, raw_ref: b"{{ tools }}\n"}, edges=edges
    )
    second, _, _ = _compile(
        {"config.yaml": payload, raw_ref: b"{{ tools }}\nChanged.\n"}, edges=edges
    )

    dependency = next(
        item for item in first.source_dependencies if item.logical_path == raw_ref
    )
    assert dependency.dependency_kind == edge_kind
    assert first.semantic_digest != second.semantic_digest


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            _MINIMAL_CONFIG.replace(
                b"      adapter: openai",
                b"      adapter: openai\n      route_handle_id: https://example.test/api",
            ),
            CompileErrorCode.FORBIDDEN_AUTHORITY,
        ),
        (
            _MINIMAL_CONFIG.replace(
                b"      params:\n        temperature: 0.25",
                b"      params: {temperature: 0.25, api_key: secret}",
            ),
            CompileErrorCode.FORBIDDEN_AUTHORITY,
        ),
    ],
)
def test_provider_authority_is_a_typed_denial(payload: bytes, code: CompileErrorCode) -> None:
    reader, closure, _ = _inputs({"config.yaml": payload})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.code is code


def test_recursive_provider_authority_and_fallback_smuggling_are_denied() -> None:
    recursive_secret = _MINIMAL_CONFIG.replace(
        b"      params:\n        temperature: 0.25",
        b"      params:\n        temperature: 0.25\n        response_format: {schema: {api_key: secret}}",
    )
    reader, closure, _ = _inputs({"config.yaml": recursive_secret})
    with pytest.raises(ConfigCompileError) as secret:
        compile_config(reader, closure, _options())
    assert secret.value.stage is CompileStage.SCHEMA
    assert secret.value.code is CompileErrorCode.FORBIDDEN_AUTHORITY

    fallback = _MINIMAL_CONFIG.replace(
        b"      adapter: openai",
        b"      adapter: openai\n      routing: {fallback_model_ids: ['https://attacker.example/model']}",
    )
    reader, closure, _ = _inputs({"config.yaml": fallback})
    with pytest.raises(ConfigCompileError) as fallback_error:
        compile_config(reader, closure, _options())
    assert fallback_error.value.code is CompileErrorCode.FORBIDDEN_AUTHORITY

    tool = canonical_json_loads(_tool_member("fallback-tool", "fallback_tool"))
    tool["provider_routing"] = {
        "openai": {"fallback_formats": ["sh -c id"]}
    }
    members = {
        "config.yaml": _tool_root(),
        "tools/fallback.yaml": canonical_json_bytes(tool),
    }
    edge = DependencyEdge(
        "config.yaml", "tool_registry", "tools", "tools/fallback.yaml", 0
    )
    reader, closure, _ = _inputs(members, edges=(edge,))
    with pytest.raises(ConfigCompileError) as tool_fallback:
        compile_config(reader, closure, _options())
    assert tool_fallback.value.code is CompileErrorCode.FORBIDDEN_AUTHORITY


def test_cache_key_binds_options_and_cached_content_is_revalidated() -> None:
    first_reader, first_closure, _ = _inputs()
    first_options = _options()
    first = compile_config(first_reader, first_closure, first_options)
    second_reader, second_closure, _ = _inputs()
    second_options = _options(runtime_abi="breadboard.conductor.v2")

    assert compiler_cache_key(first_closure, first_options) == first.inputs.compiler_input_digest
    assert compiler_cache_key(second_closure, second_options) != first.inputs.compiler_input_digest
    assert verify_cached_manifest(
        first.canonical_bytes(),
        expected_compiler_input_digest=first.inputs.compiler_input_digest,
    ).canonical_bytes() == first.canonical_bytes()

    with pytest.raises(ConfigCompileError) as wrong_input:
        verify_cached_manifest(
            first.canonical_bytes(),
            expected_compiler_input_digest=compiler_cache_key(second_closure, second_options),
        )
    assert wrong_input.value.code is CompileErrorCode.COMPILER_INPUT_MISMATCH

    poisoned = bytearray(first.canonical_bytes())
    position = poisoned.index(b"rich" if b"rich" in poisoned else b"test-model")
    poisoned[position] ^= 1
    with pytest.raises(ConfigCompileError) as stale_content:
        verify_cached_manifest(
            bytes(poisoned),
            expected_compiler_input_digest=first.inputs.compiler_input_digest,
        )
    assert stale_content.value.code is CompileErrorCode.MANIFEST_IDENTITY_MISMATCH


class _PoisonableCAS:
    def __init__(self) -> None:
        self.backing = InMemoryCAS()
        self.payload_override: bytes | None = None
        self.missing_record = False

    def put_bytes(self, data: bytes, **kwargs: object):
        return self.backing.put_bytes(data, **kwargs)

    def has(self, artifact_ref: object) -> bool:
        return self.backing.has(artifact_ref)

    def get_ref(self, artifact_id: str):
        if self.missing_record:
            raise KeyError(artifact_id)
        return self.backing.get_ref(artifact_id)

    def get_bytes(self, artifact_ref: object, *, max_bytes: int | None = None) -> bytes:
        if self.payload_override is not None:
            return self.payload_override
        return self.backing.get_bytes(artifact_ref, max_bytes=max_bytes)


@pytest.mark.parametrize("poison", ["bytes", "record"])
def test_corrupt_cas_is_rejected_before_parse(poison: str) -> None:
    cas = _PoisonableCAS()
    bundle = ingest_member_map(
        {"config.yaml": _MINIMAL_CONFIG},
        cas,
        entrypoints={"main": "config.yaml"},
    )
    closure = build_dependency_closure(bundle, root_entrypoint="main")
    reader = ManifestReader(cas=cas, bundle=bundle, closure=closure)
    if poison == "bytes":
        cas.payload_override = b"version: 2\n"
    else:
        cas.missing_record = True

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.stage is CompileStage.READER_INTEGRITY
    assert caught.value.code is CompileErrorCode.SOURCE_INTEGRITY


_V1_SHADOW_CONFIG = b"""model: test-model
workspace:
  root: workspace
providers:
  models:
    - id: test-model
      adapter: openai
      params: {temperature: 0.25}
prompts:
  injection:
    system_order: []
    per_turn_order: []
modes:
  - id: build
    prompt: ''
loop:
  sequence: [build]
"""


def _runner_visible_projection(manifest: object) -> dict[str, object]:
    semantic = manifest.semantic.to_canonical_obj()  # type: ignore[attr-defined]
    prompts = semantic["prompts"]
    variants = []
    for variant in prompts["variants"]:
        variants.append(
            {
                key: value
                for key, value in variant.items()
                if key not in {"variant_id", "config_node_id"}
            }
        )
    prompts = {**prompts, "variants": variants}
    return {
        key: value
        for key, value in semantic.items()
        if key not in {"root_config_node_id", "config_nodes", "metadata", "prompts"}
    } | {"prompts": prompts}


def test_v1_shadow_vectors_are_runner_visible_equivalent() -> None:
    native, _, _ = _compile()
    shadow, _, _ = _compile(
        {"config.yaml": _V1_SHADOW_CONFIG},
        options=_options(
            source_contract="v1_shadow",
            v1_loss_policy="allow_enumerated_nonsemantic",
        ),
    )

    assert canonical_json_bytes(_runner_visible_projection(shadow)) == canonical_json_bytes(
        _runner_visible_projection(native)
    )
    assert shadow.compiled_manifest_digest != native.compiled_manifest_digest
    assert shadow.semantic.metadata["translation"]["translator_id"] == (
        "breadboard.v1-shadow-translator.v1"
    )
    assert shadow.diagnostics.losses == ()
    assert shadow.diagnostics.notices


def test_v1_shadow_mismatch_is_observable_without_runtime_fallback() -> None:
    native, _, _ = _compile()
    mismatched = _V1_SHADOW_CONFIG.replace(b"prompt: ''", b"prompt: Different behavior")
    shadow, _, _ = _compile(
        {"config.yaml": mismatched},
        options=_options(
            source_contract="v1_shadow",
            v1_loss_policy="allow_enumerated_nonsemantic",
        ),
    )

    assert canonical_json_bytes(_runner_visible_projection(shadow)) != canonical_json_bytes(
        _runner_visible_projection(native)
    )


def test_v1_shadow_translation_is_explicit_and_closes_legacy_fields() -> None:
    source = _V1_SHADOW_CONFIG.replace(
        b"model: test-model\n",
        b"""model: test-model
max_iterations: 17
resume: {enabled: true, state_slot: resume-state}
claude: {api_variant: anthropic_messages}
""",
    )
    manifest, _, _ = _compile(
        {"config.yaml": source},
        options=_options(
            source_contract="v1_shadow",
            v1_loss_policy="allow_enumerated_nonsemantic",
        ),
    )

    assert manifest.semantic.loop["max_iterations"] == 17
    assert manifest.semantic.long_running["resume"] == {
        "enabled": True,
        "state_slot": "resume-state",
    }
    assert manifest.semantic.providers["provider_tools"]["anthropic"] == {
        "api_variant": "anthropic_messages"
    }
    translated_targets = {
        notice.target_pointer
        for notice in manifest.diagnostics.notices
        if notice.code == "v1_field_translated"
    }
    assert {"/providers/default_model", "/loop/max_iterations"} <= translated_targets


def test_v1_document_is_never_auto_translated_on_the_v2_contract() -> None:
    reader, closure, _ = _inputs({"config.yaml": _V1_SHADOW_CONFIG})

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is CompileErrorCode.SCHEMA_UNKNOWN_FIELD
    assert caught.value.instance_pointer == "/model"


def test_v1_translation_conflict_is_an_exact_typed_denial() -> None:
    conflict = _V1_SHADOW_CONFIG.replace(
        b"providers:\n",
        b"providers:\n  default_model: already-declared\n",
    )
    reader, closure, _ = _inputs({"config.yaml": conflict})

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(
            reader,
            closure,
            _options(
                source_contract="v1_shadow",
                v1_loss_policy="allow_enumerated_nonsemantic",
            ),
        )

    assert caught.value.stage is CompileStage.TRANSLATION
    assert caught.value.code is CompileErrorCode.V1_TRANSLATION_UNSUPPORTED
    assert caught.value.logical_path == "config.yaml"
    assert caught.value.instance_pointer == "/model"


@pytest.mark.parametrize(
    ("family", "adapter", "provider_policy"),
    [
        ("codex", "openai_responses", {"use_native": True, "api_variant": "responses"}),
        ("claude", "anthropic", {"use_native": True, "api_variant": "anthropic_messages"}),
        ("opencode", "responses", {"use_native": True, "responses_use_developer_role": True}),
        ("oh-my-opencode", "openai_responses", {"use_native": True, "responses_stateful": False}),
        ("pi", "openai", {"use_native": False, "api_variant": "chat_completions"}),
        ("terminal", "test", {"use_native": False, "terminal_tool_protocol": True}),
        ("swe", "test", {"use_native": False, "verifier_required": True}),
    ],
)
def test_representative_families_compile_by_semantics_not_name(
    family: str,
    adapter: str,
    provider_policy: dict[str, object],
) -> None:
    source = canonical_json_bytes(
        {
            "version": 2,
            "profile": {"name": f"representative-{family}"},
            "workspace": {"root": "workspace"},
            "providers": {
                "default_model": f"{family}-model",
                "models": [
                    {
                        "id": f"{family}-model",
                        "adapter": adapter,
                        "params": {},
                    }
                ],
            },
            "provider_tools": provider_policy,
            "prompts": {
                "tool_prompt_mode": "system_once",
                "injection": {"system_order": [], "per_turn_order": []},
            },
            "modes": [{"id": "build", "prompt": {"literal": f"{family} behavior"}}],
            "loop": {"sequence": ["build"]},
            "features": {"family_capability": family},
            "terminal_sessions": {"enabled": family == "terminal"},
        }
    )

    manifest, _, _ = _compile({"config.yaml": source})
    semantic = manifest.semantic.to_canonical_obj()

    assert semantic["metadata"]["display_name"] == f"representative-{family}"
    assert semantic["providers"]["models"][0]["adapter_id"] == adapter
    assert semantic["providers"]["provider_tools"] == provider_policy
    assert semantic["prompts"]["variants"][0]["per_turn"]["text"] == ""
    assert semantic["modes"][0]["prompt_source_id"] == "mode:build"


@pytest.mark.parametrize(
    ("family", "fragment", "code"),
    [
        (
            "terminal",
            b"workspace:\n  root: /host/absolute\n",
            CompileErrorCode.RUNTIME_SLOT_INVALID,
        ),
        (
            "swe",
            b"surprise_verifier_path: /tmp/verifier\n",
            CompileErrorCode.SCHEMA_UNKNOWN_FIELD,
        ),
    ],
)
def test_unsupported_family_authority_has_exact_typed_denial(
    family: str,
    fragment: bytes,
    code: CompileErrorCode,
) -> None:
    if family == "terminal":
        payload = _MINIMAL_CONFIG.replace(b"workspace:\n  root: workspace\n", fragment)
    else:
        payload = _MINIMAL_CONFIG + fragment
    reader, closure, _ = _inputs({"config.yaml": payload})

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.code is code


def test_inheritance_preserves_null_empty_map_and_list_semantics() -> None:
    members = {
        "base.yaml": b"""features:
  nested: {kept: base, overwritten: base}
  null_target: {value: base}
  empty_target: {value: base}
  list_target: [base]
""",
        "later.yaml": b"""features:
  nested: {overwritten: later, added: later}
  null_target: null
  empty_target: {}
  list_target: [later]
""",
        "config.yaml": _MINIMAL_CONFIG.replace(
            b"version: 2\n",
            b"version: 2\nextends: [base.yaml, later.yaml]\nfeatures:\n  nested: {child: child}\n  empty_target: {}\n",
        ),
    }
    edges = (
        DependencyEdge("config.yaml", "extends", "base.yaml", "base.yaml", 0),
        DependencyEdge("config.yaml", "extends", "later.yaml", "later.yaml", 1),
    )
    manifest, _, _ = _compile(members, edges=edges)

    assert manifest.semantic.to_canonical_obj()["features"] == {
        "nested": {
            "kept": "base",
            "overwritten": "later",
            "added": "later",
            "child": "child",
        },
        "null_target": None,
        "empty_target": {"value": "base"},
        "list_target": ["later"],
    }
    empty_provenance = next(
        item for item in manifest.provenance if item.target_pointer == "/features/empty_target"
    )
    assert any(
        contribution.action == "merge_noop"
        for contribution in empty_provenance.contributions
    )


def test_ordered_semantic_lists_change_semantic_identity() -> None:
    plan_mode = b"  - id: plan\n    prompt: ''\n"
    first_source = _MINIMAL_CONFIG.replace(
        b"modes:\n", b"modes:\n" + plan_mode
    ).replace(b"sequence: [build]", b"sequence: [plan, build]")
    second_source = first_source.replace(
        b"sequence: [plan, build]", b"sequence: [build, plan]"
    )
    first, _, _ = _compile({"config.yaml": first_source})
    second, _, _ = _compile({"config.yaml": second_source})

    assert [step["mode_id"] for step in first.semantic.loop["sequence"]] == [
        "plan",
        "build",
    ]
    assert [step["mode_id"] for step in second.semantic.loop["sequence"]] == [
        "build",
        "plan",
    ]
    assert first.semantic_digest != second.semantic_digest


def test_tool_binding_cycle_is_a_typed_denial() -> None:
    original = b"""tool_bindings:
  - id: binding:read
    tool_id: read_file
    binding_kind: server
    execution_profile: sandboxed
    placement: server
    exposure: model
    support_status: supported
modes:
"""
    cyclic = b"""tool_bindings:
  - id: binding:read
    tool_id: read_file
    binding_kind: server
    execution_profile: sandboxed
    fallback_binding_ids: ['binding:write']
  - id: binding:write
    tool_id: read_file
    binding_kind: server
    execution_profile: sandboxed
    fallback_binding_ids: ['binding:read']
modes:
"""
    payload = _RICH_CONFIG.replace(original, cyclic)
    members = {**_RICH_MEMBERS, "config.yaml": payload}
    reader, closure, _ = _inputs(members, edges=_rich_edges())

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.code is CompileErrorCode.TOOL_BINDING_CYCLE


def test_inheritance_depth_limit_is_typed_and_deterministic() -> None:
    limits = replace(BundleLimits(), max_dependency_depth=32)
    depth = limits.max_dependency_depth + 2
    members = {"config-0.yaml": b"features: {root: true}\n"}
    edges: list[DependencyEdge] = []
    for index in range(1, depth):
        name = f"config-{index}.yaml"
        parent = f"config-{index - 1}.yaml"
        members[name] = f"extends: {parent}\nfeatures: {{level: {index}}}\n".encode()
        edges.append(DependencyEdge(name, "extends", parent, parent, 0))
    root = f"config-{depth - 1}.yaml"
    members[root] += _MINIMAL_CONFIG
    with pytest.raises(BundleLimitError, match="dependency depth"):
        _inputs(members, edges=tuple(edges), root=root, limits=limits)


def _tool_member(tool_id: str, name: str) -> bytes:
    return canonical_json_bytes(
        {
            "id": tool_id,
            "name": name,
            "description": f"{name} description",
            "parameters": [],
        }
    )


def _tool_root(extra_tools: bytes = b"") -> bytes:
    return _MINIMAL_CONFIG.replace(
        b"modes:\n",
        b"tools:\n  registry: {paths: [tools], include: ['*']}\n" + extra_tools + b"modes:\n",
    )


def test_duplicate_tool_ids_are_rejected_after_directory_resolution() -> None:
    members = {
        "config.yaml": _tool_root(),
        "tools/a.yaml": _tool_member("duplicate", "first"),
        "tools/b.yaml": _tool_member("duplicate", "second"),
    }
    edges = (
        DependencyEdge("config.yaml", "tool_registry", "tools", "tools/a.yaml", 0),
        DependencyEdge("config.yaml", "tool_registry", "tools", "tools/b.yaml", 1),
    )
    reader, closure, _ = _inputs(members, edges=edges)

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.code is CompileErrorCode.TOOL_DUPLICATE_ID


def test_duplicate_final_tool_names_and_dangling_aliases_are_rejected() -> None:
    base_members = {
        "tools/a.yaml": _tool_member("tool-a", "first"),
        "tools/b.yaml": _tool_member("tool-b", "second"),
    }
    edges = (
        DependencyEdge("config.yaml", "tool_registry", "tools", "tools/a.yaml", 0),
        DependencyEdge("config.yaml", "tool_registry", "tools", "tools/b.yaml", 1),
    )
    collision = _tool_root(
        b"  overlays:\n    - rename: {tool-b: first}\n"
    )
    reader, closure, _ = _inputs(
        {"config.yaml": collision, **base_members},
        edges=edges,
    )
    with pytest.raises(ConfigCompileError) as duplicate:
        compile_config(reader, closure, _options())
    assert duplicate.value.code is CompileErrorCode.TOOL_DUPLICATE_NAME

    dangling = _tool_root(b"  aliases: {ghost: missing}\n")
    reader, closure, _ = _inputs(
        {"config.yaml": dangling, **base_members},
        edges=edges,
    )
    with pytest.raises(ConfigCompileError) as alias:
        compile_config(reader, closure, _options())
    assert alias.value.code is CompileErrorCode.TOOL_ALIAS_TARGET_UNKNOWN


def _plugin_root(refs: str, trust: str) -> bytes:
    plugin = (
        "plugins:\n"
        "  enabled: true\n"
        f"  manifest_refs: [{refs}]\n"
        f"  trust_requests: {trust}\n"
    ).encode()
    return _MINIMAL_CONFIG.replace(b"modes:\n", plugin + b"modes:\n")


def test_duplicate_plugins_and_undeclared_trust_are_typed_denials() -> None:
    plugin_a = canonical_json_bytes({"id": "same.plugin", "runtime": {"kind": "none"}})
    plugin_b = canonical_json_bytes({"id": "same.plugin", "runtime": {"kind": "none"}})
    members = {
        "config.yaml": _plugin_root(
            "{source: plugins/a.json}, {source: plugins/b.json}",
            "{same.plugin: trusted}",
        ),
        "plugins/a.json": plugin_a,
        "plugins/b.json": plugin_b,
    }
    edges = (
        DependencyEdge("config.yaml", "plugin_manifest", "plugins/a.json", "plugins/a.json", 0),
        DependencyEdge("config.yaml", "plugin_manifest", "plugins/b.json", "plugins/b.json", 1),
    )
    reader, closure, _ = _inputs(members, edges=edges)
    with pytest.raises(ConfigCompileError) as duplicate:
        compile_config(reader, closure, _options())
    assert duplicate.value.code is CompileErrorCode.PLUGIN_DUPLICATE_ID

    untrusted_members = {
        "config.yaml": _plugin_root("{source: plugins/a.json}", "{}"),
        "plugins/a.json": plugin_a,
    }
    reader, closure, _ = _inputs(untrusted_members, edges=(edges[0],))
    with pytest.raises(ConfigCompileError) as trust:
        compile_config(reader, closure, _options())
    assert trust.value.code is CompileErrorCode.PLUGIN_TRUST_UNDECLARED


def test_raw_plugin_command_and_missing_skill_edge_are_typed_denials() -> None:
    root = _plugin_root("{source: plugins/a.json}", "{demo.plugin: trusted}")
    command_plugin = canonical_json_bytes(
        {"id": "demo.plugin", "runtime": {"kind": "mcp", "command": "server"}}
    )
    edge = DependencyEdge(
        "config.yaml", "plugin_manifest", "plugins/a.json", "plugins/a.json", 0
    )
    reader, closure, _ = _inputs(
        {"config.yaml": root, "plugins/a.json": command_plugin},
        edges=(edge,),
    )
    with pytest.raises(ConfigCompileError) as command:
        compile_config(reader, closure, _options())
    assert command.value.code is CompileErrorCode.PLUGIN_RUNTIME_FORBIDDEN

    skill_plugin = canonical_json_bytes(
        {
            "id": "demo.plugin",
            "runtime": {"kind": "none"},
            "skills": [
                {"id": "demo", "kind": "prompt", "members": [{"source": "skills/demo.txt"}]}
            ],
        }
    )
    reader, closure, _ = _inputs(
        {"config.yaml": root, "plugins/a.json": skill_plugin},
        edges=(edge,),
    )
    with pytest.raises(ConfigCompileError) as missing_skill:
        compile_config(reader, closure, _options())
    assert missing_skill.value.code is CompileErrorCode.REFERENCE_UNDECLARED
    assert missing_skill.value.dependency_kind == "plugin_skill"


@pytest.mark.parametrize(
    ("payload", "dependency_kind"),
    [
        (
            _MINIMAL_CONFIG.replace(
                b"prompts:\n",
                b"prompts:\n  packs: {base: {system: {source: prompts/missing.txt}}}\n",
            ),
            "prompt",
        ),
        (
            _MINIMAL_CONFIG.replace(b"prompt: ''", b"prompt: {source: prompts/mode.txt}"),
            "mode_prompt",
        ),
        (
            _MINIMAL_CONFIG.replace(
                b"modes:\n",
                b"guardrails:\n  include: [{source: guardrails/missing.yaml}]\nmodes:\n",
            ),
            "guardrail",
        ),
        (
            _MINIMAL_CONFIG.replace(
                b"modes:\n",
                b"plugins:\n  enabled: true\n  manifest_refs: [{source: plugins/missing.yaml}]\n  trust_requests: {missing: trusted}\nmodes:\n",
            ),
            "plugin_manifest",
        ),
        (
            _MINIMAL_CONFIG.replace(
                b"modes:\n",
                b"multi_agent:\n  enabled: true\n  team_config: {source: teams/missing.yaml}\nmodes:\n",
            ),
            "team_config",
        ),
        (
            _MINIMAL_CONFIG.replace(
                b"modes:\n",
                b"task_tool:\n  description_template_path: {source: templates/missing.txt}\nmodes:\n",
            ),
            "task_template",
        ),
        (_tool_root(), "tool_registry"),
    ],
)
def test_every_source_family_requires_an_exact_closure_edge(
    payload: bytes,
    dependency_kind: str,
) -> None:
    reader, closure, _ = _inputs({"config.yaml": payload})

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.code is CompileErrorCode.REFERENCE_UNDECLARED
    assert caught.value.dependency_kind == dependency_kind


class _DuplicateItemsMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        if key != "type":
            raise KeyError(key)
        return "object"

    def __iter__(self) -> Iterator[str]:
        return iter(("type",))

    def __len__(self) -> int:
        return 1

    def items(self):
        return (("type", "object"), ("type", "array"))


def test_json_bearing_models_reject_duplicate_item_streams() -> None:
    with pytest.raises(BundleValidationError, match="duplicate"):
        TaskContract(
            contract_id="ambiguous-task.v1",
            parameter_schema=_DuplicateItemsMapping(),
            artifacts=(),
            verifier=TaskVerifierContract(None, (), None, None),
            evidence=TaskEvidenceContract((), ()),
            retention=TaskRetentionContract("test", None),
        )


def test_compiled_models_are_recursively_immutable_and_return_detached_wires() -> None:
    manifest, _, _ = _compile()
    before = manifest.canonical_bytes()

    with pytest.raises(TypeError):
        manifest.semantic.providers["models"][0]["params"]["temperature"] = 1.0
    with pytest.raises(FrozenInstanceError):
        manifest.inputs.entrypoint = "other.yaml"

    detached = manifest.to_canonical_obj()
    detached["semantic"]["providers"]["models"][0]["params"]["temperature"] = 1.0
    shallow_copy = copy.copy(manifest.semantic)
    with pytest.raises(TypeError):
        shallow_copy.providers["models"][0]["params"]["temperature"] = 1.0
    assert manifest.canonical_bytes() == before


def test_bundle_and_closure_wire_decoders_require_and_revalidate_identities() -> None:
    _, closure, bundle = _inputs()
    bundle_wire = bundle.to_dict()
    closure_wire = closure.to_dict()

    missing_bundle = copy.deepcopy(bundle_wire)
    missing_bundle.pop("bundle_digest")
    missing_closure = copy.deepcopy(closure_wire)
    missing_closure.pop("closure_digest")
    with pytest.raises(BundleValidationError):
        ConfigBundleManifest.from_dict(missing_bundle)
    with pytest.raises(BundleValidationError):
        ConfigBundleManifest.from_json(canonical_json_bytes(missing_bundle))
    with pytest.raises(BundleValidationError):
        DependencyClosureManifest.from_dict(missing_closure)
    with pytest.raises(BundleValidationError):
        DependencyClosureManifest.from_json(canonical_json_bytes(missing_closure))

    stale_bundle = copy.deepcopy(bundle_wire)
    stale_bundle["entries"][0]["media_type"] = "application/octet-stream"
    stale_closure = copy.deepcopy(closure_wire)
    stale_closure["members"][0]["media_type"] = "application/octet-stream"
    with pytest.raises(BundleIntegrityError):
        ConfigBundleManifest.from_dict(stale_bundle)
    with pytest.raises(BundleIntegrityError):
        DependencyClosureManifest.from_dict(stale_closure)

    assert replace(bundle, bundle_digest="").bundle_digest == bundle.bundle_digest
    assert replace(closure, closure_digest="").closure_digest == closure.closure_digest


@pytest.mark.parametrize("value", [MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER])
def test_canonical_integer_safe_boundaries_are_accepted(value: int) -> None:
    assert canonical_json_loads(canonical_json_bytes(value)) == value
    assert canonical_sha256({"nested": [value]}).startswith("sha256:")


@pytest.mark.parametrize("value", [MAX_SAFE_INTEGER + 1, -(MAX_SAFE_INTEGER + 1)])
def test_canonical_integer_unsafe_boundaries_are_rejected_everywhere(value: int) -> None:
    for candidate in (value, {"nested": [value]}):
        with pytest.raises(CanonicalJSONError):
            canonical_json_bytes(candidate)
        with pytest.raises(CanonicalJSONError):
            canonical_sha256(candidate)
    with pytest.raises(CanonicalJSONError):
        canonical_json_loads(str(value))
    with pytest.raises(CanonicalJSONError):
        canonical_json_loads(json.dumps({"nested": [value]}))


def test_repeated_reference_occurrences_require_distinct_dependency_edges() -> None:
    root = _MINIMAL_CONFIG.replace(
        b"version: 2\n", b"version: 2\nextends: [base.yaml, base.yaml]\n"
    )
    members = {"config.yaml": root, "base.yaml": b"features: {plan: true}\n"}
    first = DependencyEdge("config.yaml", "extends", "base.yaml", "base.yaml", 0)
    reader, closure, _ = _inputs(members, edges=(first,))
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.DEPENDENCY_RESOLUTION
    assert caught.value.code is CompileErrorCode.REFERENCE_UNDECLARED
    assert caught.value.raw_reference == "base.yaml"

    second = DependencyEdge("config.yaml", "extends", "base.yaml", "base.yaml", 1)
    manifest, _, _ = _compile(members, edges=(first, second))
    assert any(
        dependency.logical_path == "base.yaml"
        and dependency.dependency_kind == "extends"
        for dependency in manifest.source_dependencies
    )


def _tool_schema_member(reference: str) -> bytes:
    return canonical_json_bytes(
        {
            "id": "schema-tool",
            "name": "schema_tool",
            "description": "Validate one payload.",
            "type_id": "python",
            "parameters": [
                {
                    "name": "payload",
                    "schema": {
                        "$defs": {"payload": {"type": "string"}},
                        "$ref": reference,
                    },
                    "required": True,
                }
            ],
        }
    )


@pytest.mark.parametrize(
    "reference",
    [
        "https://attacker.example/schema.json",
        "file:///etc/passwd",
        "/etc/passwd",
        "other.json#/$defs/payload",
    ],
)
def test_tool_parameter_schema_rejects_non_fragment_references(reference: str) -> None:
    members = {
        "config.yaml": _tool_root(),
        "tools/schema.yaml": _tool_schema_member(reference),
    }
    edge = DependencyEdge(
        "config.yaml", "tool_registry", "tools", "tools/schema.yaml", 0
    )
    reader, closure, _ = _inputs(members, edges=(edge,))
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.REFERENCE_RESOLUTION
    assert caught.value.code is CompileErrorCode.TOOL_INVALID
    assert caught.value.logical_path == "tools/schema.yaml"
    assert caught.value.details["reason"] == "external_schema_reference_forbidden"


def test_tool_parameter_schema_accepts_internal_fragment_references() -> None:
    members = {
        "config.yaml": _tool_root(),
        "tools/schema.yaml": _tool_schema_member("#/$defs/payload"),
    }
    edge = DependencyEdge(
        "config.yaml", "tool_registry", "tools", "tools/schema.yaml", 0
    )
    manifest, _, _ = _compile(members, edges=(edge,))
    schema = manifest.semantic.tools["definitions"][0]["parameters"][0]["schema"]
    assert schema["$ref"] == "#/$defs/payload"


def test_inherited_task_and_inline_guardrail_references_resolve_from_the_declaration() -> None:
    root = _MINIMAL_CONFIG.replace(
        b"version: 2\n", b"version: 2\nextends: base.yaml\n"
    )
    base = b"""task_tool:
  id: task
  description_template_path: {source: templates/task.txt}
  subagents:
    reviewer:
      role: review
      config_ref: {source: agents/reviewer.yaml}
      replay_index: {source: replay/reviewer.json}
guardrails:
  definitions:
    - id: inherited-guard
      type: regex
      templates: {violation: {source: templates/guard.txt}}
"""
    reviewer = _MINIMAL_CONFIG.replace(
        b"generated-unclassified-agent", b"inherited-reviewer"
    )
    members = {
        "config.yaml": root,
        "base.yaml": base,
        "templates/task.txt": b"Delegate to {agents}.\n",
        "templates/guard.txt": b"Blocked {{ value }}\n",
        "agents/reviewer.yaml": reviewer,
        "replay/reviewer.json": b'{"schema":"replay.v1"}',
    }
    specifications = (
        ("config.yaml", "extends", "base.yaml", "base.yaml", 0),
        ("base.yaml", "task_template", "templates/task.txt", "templates/task.txt", 0),
        ("base.yaml", "task_subagent_config", "agents/reviewer.yaml", "agents/reviewer.yaml", 0),
        ("base.yaml", "task_replay_index", "replay/reviewer.json", "replay/reviewer.json", 0),
        ("base.yaml", "guardrail_template", "templates/guard.txt", "templates/guard.txt", 0),
    )
    edges = tuple(DependencyEdge(*specification) for specification in specifications)
    manifest, _, _ = _compile(members, edges=edges)
    dependencies = {
        (dependency.dependency_kind, dependency.logical_path): dependency.from_logical_path
        for dependency in manifest.source_dependencies
    }
    assert dependencies[("task_template", "templates/task.txt")] == "base.yaml"
    assert dependencies[("task_subagent_config", "agents/reviewer.yaml")] == "base.yaml"
    assert dependencies[("task_replay_index", "replay/reviewer.json")] == "base.yaml"
    assert dependencies[("guardrail_template", "templates/guard.txt")] == "base.yaml"


def test_prompt_variant_wire_revalidates_tool_set_invariants() -> None:
    manifest, _, _ = _compile()
    semantic = manifest.semantic.to_canonical_obj()
    variant = semantic["prompts"]["variants"][0]
    variant["tool_set_digest"] = canonical_sha256(
        {"schema": "bb.tool-set.v1", "tool_ids": ["smuggled-tool"]}
    )
    with pytest.raises(BundleValidationError, match="tool_set_digest"):
        CompiledConfig.from_dict(semantic)

    semantic = manifest.semantic.to_canonical_obj()
    semantic["prompts"]["variants"][0]["tool_catalog"][
        "effective_tool_ids"
    ] = ["smuggled-tool"]
    with pytest.raises(BundleValidationError, match="effective_tool_ids"):
        CompiledConfig.from_dict(semantic)


def test_tool_catalog_delimiter_collision_is_a_typed_render_denial() -> None:
    tool = _tool_member("delimiter", "delimiter_tool")
    raw = canonical_json_loads(tool)
    raw["description"] = "Injected\n## other-tool"
    members = {
        "config.yaml": _tool_root(),
        "tools/delimiter.yaml": canonical_json_bytes(raw),
    }
    edge = DependencyEdge(
        "config.yaml", "tool_registry", "tools", "tools/delimiter.yaml", 0
    )
    reader, closure, _ = _inputs(members, edges=(edge,))
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.RENDER
    assert caught.value.code is CompileErrorCode.PROMPT_RENDER_FAILED
    assert caught.value.details["reason"] == "catalog_delimiter_collision"


def test_equivalent_directory_and_archive_sources_share_logical_identity(
    tmp_path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.yaml").write_bytes(_MINIMAL_CONFIG)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as writer:
        info = zipfile.ZipInfo("config.yaml")
        info.external_attr = 0o644 << 16
        writer.writestr(info, _MINIMAL_CONFIG)

    directory_cas = InMemoryCAS()
    archive_cas = InMemoryCAS()
    directory_bundle = ingest_directory(
        source,
        directory_cas,
        entrypoints={"main": "config.yaml"},
        source_label="path-layout",
    )
    archive_bundle = ingest_zip(
        archive.getvalue(),
        archive_cas,
        entrypoints={"main": "config.yaml"},
        source_label="archive-layout",
    )
    assert directory_bundle.provenance.source_kind == "directory"
    assert archive_bundle.provenance.source_kind == "zip"
    assert directory_bundle.provenance != archive_bundle.provenance
    assert directory_bundle.bundle_digest == archive_bundle.bundle_digest

    directory_closure = build_dependency_closure(
        directory_bundle, root_entrypoint="main"
    )
    archive_closure = build_dependency_closure(
        archive_bundle, root_entrypoint="main"
    )
    directory_manifest = compile_config(
        ManifestReader(
            cas=directory_cas,
            bundle=directory_bundle,
            closure=directory_closure,
        ),
        directory_closure,
        _options(),
    )
    archive_manifest = compile_config(
        ManifestReader(
            cas=archive_cas,
            bundle=archive_bundle,
            closure=archive_closure,
        ),
        archive_closure,
        _options(),
    )
    assert directory_manifest.canonical_bytes() == archive_manifest.canonical_bytes()


def test_compiler_implementation_digest_is_path_stable_and_semantically_sensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, _, _ = _compile()
    original = server_compiler._compile_providers

    def controlled_implementation_change(config):
        return original(config)

    monkeypatch.setattr(
        server_compiler, "_compile_providers", controlled_implementation_change
    )
    changed, _, _ = _compile()

    assert baseline.compiler.compiler_code_digest != changed.compiler.compiler_code_digest
    assert baseline.inputs.compiler_input_digest != changed.inputs.compiler_input_digest
    assert baseline.semantic_digest == changed.semantic_digest
    assert baseline.compiled_manifest_digest != changed.compiled_manifest_digest


def _nested_object(depth: int) -> dict[str, object]:
    value: object = 0
    for _ in range(depth):
        value = {"value": value}
    assert isinstance(value, dict)
    return value


def test_parser_enforces_exact_byte_depth_and_node_limits() -> None:
    byte_limit = server_compiler.MAX_DOCUMENT_BYTES
    assert strict_parse_payload(
        b"{}" + b" " * (byte_limit - 2),
        logical_path="limit.json",
        media_type="application/json",
    ) == {}
    with pytest.raises(ConfigCompileError) as bytes_error:
        strict_parse_payload(
            b"{}" + b" " * (byte_limit - 1),
            logical_path="limit.json",
            media_type="application/json",
        )
    assert bytes_error.value.stage is CompileStage.PARSE
    assert bytes_error.value.code is CompileErrorCode.RESOURCE_LIMIT_EXCEEDED

    accepted_depth = _nested_object(server_compiler.MAX_DOCUMENT_DEPTH)
    assert strict_parse_payload(
        json.dumps(accepted_depth).encode(),
        logical_path="depth.json",
        media_type="application/json",
    ) == accepted_depth
    rejected_depth = _nested_object(server_compiler.MAX_DOCUMENT_DEPTH + 1)
    with pytest.raises(ConfigCompileError) as depth_error:
        strict_parse_payload(
            json.dumps(rejected_depth).encode(),
            logical_path="depth.json",
            media_type="application/json",
        )
    assert depth_error.value.stage is CompileStage.PARSE
    assert depth_error.value.code is CompileErrorCode.RESOURCE_LIMIT_EXCEEDED

    accepted_nodes = {"values": [0] * (server_compiler.MAX_DOCUMENT_NODES - 2)}
    assert strict_parse_payload(
        json.dumps(accepted_nodes).encode(),
        logical_path="nodes.json",
        media_type="application/json",
    ) == accepted_nodes
    rejected_nodes = {"values": [0] * (server_compiler.MAX_DOCUMENT_NODES - 1)}
    with pytest.raises(ConfigCompileError) as node_error:
        strict_parse_payload(
            json.dumps(rejected_nodes).encode(),
            logical_path="nodes.json",
            media_type="application/json",
        )
    assert node_error.value.stage is CompileStage.PARSE
    assert node_error.value.code is CompileErrorCode.RESOURCE_LIMIT_EXCEEDED


def test_yaml_alias_and_deep_nesting_bombs_are_typed_denials() -> None:
    alias_bomb = b"root: &root [*root]\n"
    with pytest.raises(ConfigCompileError) as alias_error:
        strict_parse_payload(alias_bomb, logical_path="alias.yaml")
    assert alias_error.value.stage is CompileStage.PARSE
    assert alias_error.value.code is CompileErrorCode.UNSUPPORTED_YAML_TAG

    depth_bomb = (
        b"value: "
        + b"[" * (server_compiler.MAX_DOCUMENT_DEPTH + 1)
        + b"0"
        + b"]" * (server_compiler.MAX_DOCUMENT_DEPTH + 1)
    )
    with pytest.raises(ConfigCompileError) as depth_error:
        strict_parse_payload(depth_bomb, logical_path="depth.yaml")
    assert depth_error.value.stage is CompileStage.PARSE
    assert depth_error.value.code is CompileErrorCode.RESOURCE_LIMIT_EXCEEDED


def test_merged_document_node_budget_is_typed_and_deterministic() -> None:
    per_base = server_compiler.MAX_MERGE_NODES // 3 + 1
    base_members = {
        f"base-{base_index}.json": canonical_json_bytes(
            {
                "features": {
                    f"b{base_index}-{index}": False for index in range(per_base)
                }
            }
        )
        for base_index in range(3)
    }
    root = strict_parse_payload(_MINIMAL_CONFIG, logical_path="config.yaml")
    root["extends"] = list(base_members)
    members = {"config.json": canonical_json_bytes(root), **base_members}
    edges = tuple(
        DependencyEdge(
            "config.json", "extends", path, path, ordinal
        )
        for ordinal, path in enumerate(base_members)
    )
    reader, closure, _ = _inputs(
        members,
        edges=edges,
        root="config.json",
    )
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.MERGE
    assert caught.value.code is CompileErrorCode.RESOURCE_LIMIT_EXCEEDED


def test_bundle_closure_dependency_and_reader_aggregate_limits_are_enforced() -> None:
    with pytest.raises(BundleLimitError):
        _inputs(
            {"config.yaml": _MINIMAL_CONFIG},
            limits=replace(
                BundleLimits(),
                max_member_bytes=len(_MINIMAL_CONFIG) - 1,
                max_total_bytes=len(_MINIMAL_CONFIG),
            ),
        )

    root = _MINIMAL_CONFIG.replace(
        b"version: 2\n", b"version: 2\nextends: [base.yaml, base.yaml]\n"
    )
    base = b"features: {plan: true}\n"
    limits = replace(
        BundleLimits(),
        max_member_bytes=max(len(root), len(base)),
        max_total_bytes=len(root) + len(base),
        max_dependency_edges=2,
    )
    edges = (
        DependencyEdge("config.yaml", "extends", "base.yaml", "base.yaml", 0),
        DependencyEdge("config.yaml", "extends", "base.yaml", "base.yaml", 1),
    )
    reader, closure, _ = _inputs(
        {"config.yaml": root, "base.yaml": base},
        edges=edges,
        limits=limits,
    )
    reader.read_bytes("config.yaml")
    reader.read_bytes("base.yaml")
    with pytest.raises(BundleLimitError):
        reader.read_bytes("base.yaml")

    with pytest.raises(BundleLimitError):
        _inputs(
            {"config.yaml": root, "base.yaml": base},
            edges=edges,
            limits=replace(limits, max_dependency_edges=1),
        )


@pytest.mark.parametrize(
    ("family", "value"),
    [
        ("features", "{nested: {api_key: secret}}"),
        ("completion", "{verification: {url: 'https://attacker.example'}}"),
        ("concurrency", "{max_parallel_tools: {command: 'sh -c id'}}"),
        ("permissions", "{network: {token: secret}}"),
        ("enhanced_tools", "{tool_ids: [{path: /etc/passwd}]}"),
        ("long_running", "{budget: {shell: /bin/sh}}"),
        ("terminal_sessions", "{persistence: {command: id}}"),
        ("logging", "{sink_slot_id: 'https://attacker.example'}"),
        ("telemetry", "{event_types: [{credential: secret}]}"),
    ],
)
def test_runtime_visible_families_reject_recursive_authority(
    family: str,
    value: str,
) -> None:
    payload = _MINIMAL_CONFIG.replace(
        b"modes:\n", f"{family}: {value}\nmodes:\n".encode(), 1
    )
    reader, closure, _ = _inputs({"config.yaml": payload})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is CompileErrorCode.FORBIDDEN_AUTHORITY


@pytest.mark.parametrize(
    "family",
    [
        "features",
        "completion",
        "concurrency",
        "permissions",
        "enhanced_tools",
        "long_running",
        "terminal_sessions",
        "logging",
        "telemetry",
    ],
)
def test_runtime_visible_families_reject_unknown_fields(family: str) -> None:
    payload = _MINIMAL_CONFIG.replace(
        b"modes:\n", f"{family}: {{surprise_runtime_switch: true}}\nmodes:\n".encode(), 1
    )
    reader, closure, _ = _inputs({"config.yaml": payload})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is CompileErrorCode.SCHEMA_UNKNOWN_FIELD


@pytest.mark.parametrize(
    ("workspace_fragment", "code"),
    [
        (b"  mounts: [{source: /etc, target: workspace/host}]\n", CompileErrorCode.SCHEMA_UNKNOWN_FIELD),
        (b"  mounts: [{command: 'sh -c id'}]\n", CompileErrorCode.SCHEMA_UNKNOWN_FIELD),
        (b"  image: 'https://attacker.example/image:latest'\n", CompileErrorCode.SCHEMA_INVALID_VALUE),
        (b"  image: {image_id: image:test, credential: secret}\n", CompileErrorCode.SCHEMA_UNKNOWN_FIELD),
    ],
)
def test_mount_and_image_requests_reject_raw_authority(
    workspace_fragment: bytes,
    code: CompileErrorCode,
) -> None:
    payload = _MINIMAL_CONFIG.replace(
        b"workspace:\n", b"workspace:\n" + workspace_fragment, 1
    )
    reader, closure, _ = _inputs({"config.yaml": payload})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is code


def test_prompt_selection_binding_selectors_and_semantic_pointers_are_closed() -> None:
    unknown_mode = _MINIMAL_CONFIG.replace(
        b"prompts:\n",
        b"prompts:\n  synthesis:\n    selection:\n      by_mode: {ghost: pythonic}\n",
    )
    reader, closure, _ = _inputs({"config.yaml": unknown_mode})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SEMANTIC_VALIDATION
    assert caught.value.code is CompileErrorCode.PROMPT_MODE_UNKNOWN

    hostile_selection = _MINIMAL_CONFIG.replace(
        b"prompts:\n",
        b"prompts:\n  synthesis:\n    selection:\n      by_mode: {build: {api_key: secret}}\n",
    )
    reader, closure, _ = _inputs({"config.yaml": hostile_selection})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.code is CompileErrorCode.FORBIDDEN_AUTHORITY

    binding = b"""tool_bindings:
  - id: binding:test
    tool_id: schema_tool
    environment_selector: {nested: {token: secret}}
"""
    root = _tool_root().replace(b"modes:\n", binding + b"modes:\n")
    members = {
        "config.yaml": root,
        "tools/schema.yaml": _tool_schema_member("#/$defs/payload"),
    }
    edge = DependencyEdge(
        "config.yaml", "tool_registry", "tools", "tools/schema.yaml", 0
    )
    reader, closure, _ = _inputs(members, edges=(edge,))
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.code is CompileErrorCode.FORBIDDEN_AUTHORITY


@pytest.mark.parametrize(
    ("field", "pointer", "stage", "code"),
    [
        ("trainable_json_pointers", "params/temperature", CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID),
        ("trainable_json_pointers", "/params/missing", CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.PROVIDER_INVALID),
        ("optimizer_mutable_pointers", "/providers/~2bad", CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.SCHEMA_INVALID_VALUE),
        ("optimizer_mutable_pointers", "/providers/missing", CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.SCHEMA_INVALID_VALUE),
    ],
)
def test_mutable_pointer_declarations_are_canonical_and_resolve(
    field: str,
    pointer: str,
    stage: CompileStage,
    code: CompileErrorCode,
) -> None:
    if field == "trainable_json_pointers":
        payload = _MINIMAL_CONFIG.replace(
            b"      params:\n",
            f"      trainable_json_pointers: ['{pointer}']\n      params:\n".encode(),
        )
    else:
        payload = _MINIMAL_CONFIG.replace(
            b"modes:\n",
            f"optimizer_mutable_pointers: ['{pointer}']\nmodes:\n".encode(),
        )
    reader, closure, _ = _inputs({"config.yaml": payload})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is stage
    assert caught.value.code is code


def _sampling_source(value: bytes, *, mutable: bool = True) -> bytes:
    declaration = (
        b"optimizer_mutable_pointers: ['/sampling/temperature']\n"
        if mutable
        else b""
    )
    return _MINIMAL_CONFIG.replace(
        b"modes:\n",
        b"sampling:\n  temperature: " + value + b"\n" + declaration + b"modes:\n",
    )


def test_sampling_temperature_is_exact_source_semantics_and_identity() -> None:
    source = _sampling_source(b"0.7")
    manifest, _, bundle = _compile({"config.yaml": source})
    semantic = manifest.semantic.to_canonical_obj()
    root = next(
        node
        for node in semantic["config_nodes"]
        if node["node_id"] == semantic["root_config_node_id"]
    )
    assert semantic["sampling"] == {"temperature": 0.7}
    assert root["semantic_config"]["sampling"] == {"temperature": 0.7}
    assert manifest.semantic.optimizer_mutable_pointers == (
        "/sampling/temperature",
    )
    assert bundle.entries[0].blob_digest == _sha256(source)
    assert manifest.source_dependencies[0].blob_digest == _sha256(source)
    changed, _, _ = _compile({"config.yaml": _sampling_source(b"0.8")})
    assert changed.semantic_digest != manifest.semantic_digest
    assert changed.compiled_manifest_digest != manifest.compiled_manifest_digest


def test_sampling_absence_is_empty_and_cannot_be_declared_mutable() -> None:
    manifest, _, _ = _compile()
    assert manifest.semantic.sampling == {}
    payload = _MINIMAL_CONFIG.replace(
        b"modes:\n",
        b"optimizer_mutable_pointers: ['/sampling/temperature']\nmodes:\n",
    )
    reader, closure, _ = _inputs({"config.yaml": payload})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SEMANTIC_VALIDATION
    assert caught.value.code is CompileErrorCode.SCHEMA_INVALID_VALUE
    assert caught.value.details["reason"] == "pointer_target_missing"


@pytest.mark.parametrize("value", [b"true", b"'hot'", b"-0.1", b"2.1"])
def test_sampling_temperature_rejects_invalid_type_or_range(value: bytes) -> None:
    reader, closure, _ = _inputs(
        {"config.yaml": _sampling_source(value, mutable=False)}
    )
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is CompileErrorCode.SCHEMA_INVALID_VALUE
    assert caught.value.instance_pointer == "/sampling/temperature"


def _semantic_leaf_pointers(value: object, pointer: str = "") -> set[str]:
    if isinstance(value, dict):
        result: set[str] = set()
        for key, item in value.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            result |= _semantic_leaf_pointers(item, f"{pointer}/{escaped}")
        return result
    if isinstance(value, list):
        result = set()
        for index, item in enumerate(value):
            result |= _semantic_leaf_pointers(item, f"{pointer}/{index}")
        return result
    return {pointer}


def test_every_semantic_leaf_and_default_has_a_complete_provenance_record() -> None:
    manifest, _, _ = _compile(_RICH_MEMBERS, edges=_rich_edges())
    provenance = {record.target_pointer: record for record in manifest.provenance}
    assert _semantic_leaf_pointers(manifest.semantic.to_canonical_obj()) <= set(provenance)

    expected_members = {
        "/tools/definitions/0/description": "tools/read.yaml",
        "/guardrails/definitions/0/templates/0/1/text": "templates/guardrail.txt",
        "/plugins/plugins/0/skills/0/compiled_payload/0": "skills/demo.txt",
        "/team/team_id": "teams/main.yaml",
    }
    for target_pointer, logical_path in expected_members.items():
        winner = provenance[target_pointer].contributions[
            provenance[target_pointer].winner_index
        ]
        assert winner.origin_kind == "source"
        assert winner.logical_path == logical_path
        assert winner.blob_digest is not None
        assert winner.source_pointer is not None
        assert winner.dependency_kind is not None

    minimal, _, _ = _compile()
    default_pointers = [record.target_pointer for record in minimal.diagnostics.defaults]
    assert len(default_pointers) == len(set(default_pointers))
    assert {
        "/prompts/tool_prompt_mode",
        "/prompts/synthesis/enabled",
        "/plugins/enabled",
        "/runtime/sandbox/driver_id",
        "/runtime/sandbox/network_request",
    } <= set(default_pointers)
    minimal_provenance = {
        record.target_pointer: record for record in minimal.provenance
    }
    for target_pointer in default_pointers:
        record = minimal_provenance[target_pointer]
        winner = record.contributions[record.winner_index]
        assert winner.origin_kind == "compiler_default"
        assert winner.action == "default"


def test_implementation_digest_binds_defaults_closures_and_policy_globals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    baseline = server_compiler._compiler_implementation_digest()
    monkeypatch.chdir(tmp_path)
    assert server_compiler._compiler_implementation_digest() == baseline

    with monkeypatch.context() as patch:
        patch.setattr(
            server_compiler._walk_leaf_pointers,
            "__defaults__",
            ("/default-only-change",),
        )
        assert server_compiler._compiler_implementation_digest() != baseline

    with monkeypatch.context() as patch:
        patch.setattr(
            server_compiler._require_object,
            "__kwdefaults__",
            {"allow_none": True},
        )
        assert server_compiler._compiler_implementation_digest() != baseline

    original = server_compiler._compile_providers

    def controlled_implementation(marker: str):
        def wrapper(config):
            if marker == "unreachable-marker":
                raise AssertionError
            return original(config)

        wrapper.__module__ = server_compiler.__name__
        return wrapper

    with monkeypatch.context() as patch:
        patch.setattr(
            server_compiler,
            "_compile_providers",
            controlled_implementation("closure-a"),
        )
        closure_a = server_compiler._compiler_implementation_digest()
    with monkeypatch.context() as patch:
        patch.setattr(
            server_compiler,
            "_compile_providers",
            controlled_implementation("closure-b"),
        )
        closure_b = server_compiler._compiler_implementation_digest()
    assert closure_a != closure_b

    with monkeypatch.context() as patch:
        patch.setattr(
            server_compiler,
            "_PLUGIN_ID_RE",
            server_compiler.re.compile(r"^[a-z]+$"),
        )
        assert server_compiler._compiler_implementation_digest() != baseline

    changed_policy = copy.deepcopy(server_compiler._COMPILER_POLICY_DESCRIPTOR)
    changed_policy["parser"] = "semantic-policy-change"
    with monkeypatch.context() as patch:
        patch.setattr(
            server_compiler,
            "_COMPILER_POLICY_DESCRIPTOR",
            changed_policy,
        )
        assert server_compiler._compiler_implementation_digest() != baseline


@pytest.mark.parametrize(
    "enum_member",
    [CompileStage.PARSE, CompileErrorCode.INVALID_JSON],
    ids=["compile-stage", "compile-error-code"],
)
def test_implementation_digest_binds_semantic_enum_wire_values(
    enum_member: CompileStage | CompileErrorCode,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = server_compiler._compiler_implementation_digest()
    with monkeypatch.context() as patch:
        patch.setattr(enum_member, "_value_", enum_member.value + "-mutated")
        assert server_compiler._compiler_implementation_digest() != baseline
    assert server_compiler._compiler_implementation_digest() == baseline

def _compile_hostile_tool_catalog(template: bytes) -> ConfigCompileError:
    root = _MINIMAL_CONFIG.replace(
        b"prompts:\n",
        b"prompts:\n  tool_catalog: {source: templates/catalog.j2}\n",
    )
    edge = DependencyEdge(
        "config.yaml",
        "prompt_template",
        "templates/catalog.j2",
        "templates/catalog.j2",
        0,
    )
    reader, closure, _ = _inputs(
        {"config.yaml": root, "templates/catalog.j2": template},
        edges=(edge,),
    )
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    return caught.value


@pytest.mark.parametrize(
    "template",
    [
        b"{{ tools['__class__'] }}",
        b"{{ 'x' * 100000000 }}",
        b"{{ 2 ** 1000000 }}",
        b"{{ (1 + 2) * 3 }}",
        b"x" * (1024 * 1024 + 1),
    ],
    ids=["getitem-attribute", "string-multiply", "power", "arithmetic", "output-bytes"],
)
def test_prompt_templates_deny_getitem_arithmetic_and_output_bombs(
    template: bytes,
) -> None:
    error = _compile_hostile_tool_catalog(template)
    assert error.stage is CompileStage.RENDER
    assert error.code is CompileErrorCode.PROMPT_TEMPLATE_INVALID
    assert error.logical_path == "templates/catalog.j2"
    assert b"0x" not in canonical_json_bytes(error.to_canonical_obj())


@pytest.mark.parametrize("keyword", ["$dynamicRef", "$recursiveRef"])
def test_tool_parameter_schema_recursively_rejects_external_reference_keywords(
    keyword: str,
) -> None:
    tool = canonical_json_loads(_tool_schema_member("#/$defs/payload"))
    tool["parameters"][0]["schema"]["allOf"] = [
        {"properties": {"nested": {keyword: "https://attacker.example/schema"}}}
    ]
    members = {
        "config.yaml": _tool_root(),
        "tools/schema.yaml": canonical_json_bytes(tool),
    }
    edge = DependencyEdge(
        "config.yaml", "tool_registry", "tools", "tools/schema.yaml", 0
    )
    reader, closure, _ = _inputs(members, edges=(edge,))
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.REFERENCE_RESOLUTION
    assert caught.value.code is CompileErrorCode.TOOL_INVALID
    assert caught.value.details["reason"] == "unsupported_schema_reference"


@pytest.mark.parametrize(
    ("fragment", "code"),
    [
        (b"turn_strategy: {surprise: true}\n", CompileErrorCode.SCHEMA_UNKNOWN_FIELD),
        (b"turn_strategy: {relay: {command: 'sh -c id'}}\n", CompileErrorCode.FORBIDDEN_AUTHORITY),
        (b"loop:\n  sequence: [build]\n  surprise: true\n", CompileErrorCode.SCHEMA_UNKNOWN_FIELD),
        (b"loop:\n  sequence: [build]\n  limits: {max_iterations: {token: secret}}\n", CompileErrorCode.FORBIDDEN_AUTHORITY),
    ],
)
def test_turn_strategy_and_loop_are_closed_recursive_authority_boundaries(
    fragment: bytes,
    code: CompileErrorCode,
) -> None:
    if fragment.startswith(b"loop:"):
        payload = _MINIMAL_CONFIG.replace(b"loop:\n  sequence: [build]\n", fragment)
    else:
        payload = _MINIMAL_CONFIG.replace(b"modes:\n", fragment + b"modes:\n")
    reader, closure, _ = _inputs({"config.yaml": payload})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is code
@pytest.mark.parametrize(
    ("value", "stage", "code"),
    [
        (True, CompileStage.SCHEMA, CompileErrorCode.RUNTIME_SLOT_INVALID),
        ("1", CompileStage.SCHEMA, CompileErrorCode.RUNTIME_SLOT_INVALID),
        (-1, CompileStage.SCHEMA, CompileErrorCode.RUNTIME_SLOT_INVALID),
        (0, CompileStage.SCHEMA, CompileErrorCode.RUNTIME_SLOT_INVALID),
        (MAX_SAFE_INTEGER + 1, CompileStage.PARSE, CompileErrorCode.NUMBER_OUT_OF_RANGE),
    ],
)
def test_sandbox_resource_request_rejects_non_positive_safe_integers(
    value: object,
    stage: CompileStage,
    code: CompileErrorCode,
) -> None:
    encoded = json.dumps(value).encode()
    payload = _MINIMAL_CONFIG.replace(
        b"  root: workspace\n",
        b"  root: workspace\n  resources: {cpu: " + encoded + b"}\n",
    )
    reader, closure, _ = _inputs({"config.yaml": payload})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is stage
    assert caught.value.code is code
    if stage is CompileStage.SCHEMA:
        assert caught.value.instance_pointer == "/runtime/sandbox/resource_request/cpu"


def test_sandbox_resource_request_rejects_unknown_fields_and_accepts_safe_boundary() -> None:
    unknown = _MINIMAL_CONFIG.replace(
        b"  root: workspace\n",
        b"  root: workspace\n  resources: {cpu: 1, surprise: 1}\n",
    )
    reader, closure, _ = _inputs({"config.yaml": unknown})
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is CompileErrorCode.SCHEMA_UNKNOWN_FIELD

    boundary = _MINIMAL_CONFIG.replace(
        b"  root: workspace\n",
        (
            "  root: workspace\n"
            f"  resources: {{cpu: {MAX_SAFE_INTEGER}, memory_mb: 1, gpu_count: 1, gpu_memory_mb: 1, timeout_ms: 1}}\n"
        ).encode(),
    )
    manifest, _, _ = _compile({"config.yaml": boundary})
    assert manifest.semantic.runtime["sandbox"]["resource_request"] == {
        "cpu": MAX_SAFE_INTEGER,
        "memory_mb": 1,
        "gpu_count": 1,
        "gpu_memory_mb": 1,
        "timeout_ms": 1,
    }


def test_resource_request_carriers_are_validated_before_duplicate_resolution() -> None:
    invalid_sandbox_carrier = _MINIMAL_CONFIG.replace(
        b"  root: workspace\n",
        b"  root: workspace\n  resources: {cpu: 1}\n",
    ).replace(
        b"providers:\n",
        b"sandbox:\n  resource_request: {cpu: '1', surprise: 1}\nproviders:\n",
        1,
    )
    reader, closure, _ = _inputs({"config.yaml": invalid_sandbox_carrier})
    with pytest.raises(ConfigCompileError) as invalid:
        compile_config(reader, closure, _options())
    assert invalid.value.stage is CompileStage.SCHEMA
    assert invalid.value.code is CompileErrorCode.SCHEMA_UNKNOWN_FIELD
    assert invalid.value.instance_pointer == "/runtime/sandbox/resource_request/surprise"

    duplicate_valid_carriers = invalid_sandbox_carrier.replace(
        b"{cpu: '1', surprise: 1}",
        b"{cpu: 2}",
    )
    reader, closure, _ = _inputs({"config.yaml": duplicate_valid_carriers})
    with pytest.raises(ConfigCompileError) as duplicate:
        compile_config(reader, closure, _options())
    assert duplicate.value.stage is CompileStage.SCHEMA
    assert duplicate.value.code is CompileErrorCode.RUNTIME_SLOT_INVALID
    assert duplicate.value.instance_pointer == "/runtime/sandbox/resource_request"
    assert duplicate.value.details["reason"] == "duplicate_resource_request_carriers"


def _payload_with_assignments(
    assignments: tuple[tuple[str, object], ...],
) -> dict[str, object]:
    payload = canonical_json_loads(
        canonical_json_bytes(strict_parse_payload(_MINIMAL_CONFIG, logical_path="config.yaml"))
    )
    for path, value in assignments:
        target = payload
        parts = path.split("/")
        for part in parts[:-1]:
            if part.isdecimal():
                target = target[int(part)]
            else:
                target = target.setdefault(part, {})
        final = parts[-1]
        if final.isdecimal():
            target[int(final)] = value
        else:
            target[final] = value
    return payload


@pytest.mark.parametrize(
    ("assignments", "stage", "code", "pointer", "task_template"),
    [
        ((("providers/models/0/routing/fallback_model_ids", []), ("providers/models/0/routing/fallback_models", "invalid")), CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID, "/providers/models/0/routing/fallback_model_ids", False),
        ((("providers/models/0/routing", {}), ("providers/routing", {"fallback_model_ids": "invalid"})), CompileStage.SCHEMA, CompileErrorCode.PROVIDER_INVALID, "/providers/models/0/routing/fallback_model_ids", False),
        ((("provider_tools", {"use_native": False}), ("providers/provider_tools", {"api_key": "secret"})), CompileStage.SCHEMA, CompileErrorCode.FORBIDDEN_AUTHORITY, "/provider_tools/api_key", False),
        ((("tool_packs/demo/tools", []), ("tool_packs/demo/tool_ids", "invalid")), CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_SELECTION_UNKNOWN, "/tool_packs/demo/tools", False),
        ((("tool_packs/higher/tools", []), ("tools/packs/lower/tools", "invalid")), CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.TOOL_SELECTION_UNKNOWN, "/tool_packs/lower/tools", False),
        ((("tool_bindings", []), ("tools/bindings", {})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, "/tool_bindings", False),
        ((("prompts/synthesis", {}), ("prompts/tool_prompt_synthesis", "invalid")), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, "/prompts/synthesis", False),
        ((("guardrails/definitions", []), ("guardrails/guards", "invalid")), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, "/guardrails/definitions", False),
        ((("multi_agent/enabled", True), ("multi_agent/team_config", {"team_id": "valid-team", "agents": [{"id": "root-agent"}]}), ("multi_agent/team", "invalid")), CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, "/multi_agent/team_config", False),
        ((("task_tool/description_template_path", "templates/task.txt"), ("task_tool/description_template", 1)), CompileStage.SCHEMA, CompileErrorCode.TASK_SOURCE_INVALID, "/task_tool/description_template_path", True),
        ((("sandbox", {}), ("workspace/sandbox", "invalid")), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, "/sandbox", False),
        ((("workspace/driver", "driver:valid"), ("sandbox/driver_id", {"command": "sh"})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, "/runtime/sandbox/driver_id", False),
        ((('sandbox/driver_id', 'driver:valid'), ('sandbox/driver', {'command': 'sh'})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, '/runtime/sandbox/driver_id', False),
        ((('workspace/driver', 'driver:valid'), ('sandbox/driver', {'command': 'sh'})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, '/runtime/sandbox/driver_id', False),
        ((("workspace/options", {}), ("sandbox/options", {"api_key": "secret"})), CompileStage.SCHEMA, CompileErrorCode.FORBIDDEN_AUTHORITY, "/runtime/sandbox/options/api_key", False),
        ((("workspace/mounts", []), ("sandbox/mount_requests", {})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, "/runtime/sandbox/mount_requests", False),
        ((("workspace/network", {"mode": "none"}), ("sandbox/network_request", {"surprise": True})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_UNKNOWN_FIELD, "/runtime/sandbox/network_request/surprise", False),
        ((("workspace/image", "image:valid"), ("sandbox/image_request", {"surprise": True})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_UNKNOWN_FIELD, "/runtime/sandbox/image_request/surprise", False),
        ((("resume", {"enabled": False}), ("long_running/resume", "invalid")), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, "/long_running/resume", False),
        ((('modes/0/id', 'build'), ('modes/0/name', {'command': 'sh'})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, '/modes/0/id', False),
        ((('prompts/synthesis/selection', {}), ('tools/dialects/selection', 'invalid')), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, '/tools/dialects/selection', False),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'valid-team', 'agents': [{'id': 'root-agent', 'config_node_id': 'root', 'config_ref': 1}]})), CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/agents/0/config_ref', False),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'valid-team', 'agents': [{'id': 'a'}, {'id': 'b'}], 'edges': [{'from_agent_id': 'a', 'from': 1, 'to_agent_id': 'b'}]})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, '/multi_agent/team_config/edges/0/from', False),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'valid-team', 'agents': [{'id': 'a'}, {'id': 'b'}], 'edges': [{'from_agent_id': 'a', 'to_agent_id': 'b', 'to': 1}]})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, '/multi_agent/team_config/edges/0/to', False),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'valid-team', 'agents': [{'id': 'a'}], 'async': False}), ('multi_agent/async', 'invalid')), CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/async', False),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'valid-team', 'agents': [{'id': 'a'}], 'max_concurrent_agents': 1}), ('multi_agent/max_concurrent_agents', 'invalid')), CompileStage.SCHEMA, CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/max_concurrent_agents', False),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'valid-team', 'agents': [{'id': 'a'}], 'scheduler': 'deterministic'}), ('multi_agent/scheduler', {})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, '/multi_agent/team_config/scheduler', False),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'valid-team', 'agents': [{'id': 'a'}], 'spawn_tool': 'task'}), ('multi_agent/spawn_tool', {})), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, '/multi_agent/team_config/spawn_tool', False),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'valid-team', 'agents': [{'id': 'a'}], 'coordination': {}}), ('multi_agent/coordination', {'api_key': 'secret'})), CompileStage.SCHEMA, CompileErrorCode.FORBIDDEN_AUTHORITY, '/multi_agent/team_config/coordination/api_key', False),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'valid-team', 'agents': [{'id': 'a'}], 'bus': {}}), ('multi_agent/bus', {'token': 'secret'})), CompileStage.SCHEMA, CompileErrorCode.FORBIDDEN_AUTHORITY, '/multi_agent/team_config/bus/token', False),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'valid-team', 'agents': [{'id': 'a'}], 'workspace_sharing': {}}), ('multi_agent/workspace_sharing', {'command': 'sh'})), CompileStage.SCHEMA, CompileErrorCode.FORBIDDEN_AUTHORITY, '/multi_agent/team_config/workspace_sharing/command', False),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'valid-team', 'agents': [{'id': 'a'}], 'event_log_path': 'events/log.jsonl'}), ('multi_agent/event_log_path', {})), CompileStage.SEMANTIC_VALIDATION, CompileErrorCode.RUNTIME_SLOT_INVALID, None, False),
        ((('task_tool/subagents/worker/description', 'Worker'), ('task_tool/subagents/worker/role', 1)), CompileStage.SCHEMA, CompileErrorCode.TASK_SOURCE_INVALID, '/task_tool/subagents/worker/description', False),
        ((('task_tool/subagents/worker/config_node_id', 'root'), ('task_tool/subagents/worker/config_ref', 1)), CompileStage.SCHEMA, CompileErrorCode.TASK_SOURCE_INVALID, '/task_tool/subagents/worker/config_ref', False),
        ((('loop/max_iterations', 1), ('loop/limits/max_iterations', 'invalid')), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_INVALID_VALUE, '/loop/limits/max_iterations', False),
        ((('turn_strategy/relay', 'continuation'), ('loop/turn_strategy/relay', {'command': 'sh'})), CompileStage.SCHEMA, CompileErrorCode.FORBIDDEN_AUTHORITY, '/loop/turn_strategy/relay/command', False),
        ((('loop/sequence', [{'if': True, 'then': 'build', 'mode': {'command': 'sh'}}]),), CompileStage.SCHEMA, CompileErrorCode.SCHEMA_TYPE_MISMATCH, '/loop/sequence/0/mode', False),
    ],
    ids=[
        "provider-fallback-alias", "provider-routing", "provider-tools",
        "pack-tool-alias", "tool-packs", "tool-bindings", "prompt-synthesis",
        "guardrail-definitions", "team-config", "task-template", "sandbox-root",
        "sandbox-driver", "sandbox-driver-alias", "sandbox-driver-third",
        "sandbox-options", "sandbox-mounts", "sandbox-network", "sandbox-image", "resume",
        "mode-id", "dialect-selection", "team-agent-config", "team-edge-from",
        "team-edge-to", "team-async", "team-limit", "team-scheduler",
        "team-spawn-tool", "team-coordination", "team-bus", "team-workspace-sharing",
        "team-event-log", "task-description", "task-subagent-config", "loop-limit",
        "turn-strategy", "loop-mode",
    ],
)
def test_present_lower_precedence_carriers_cannot_be_silently_ignored(
    assignments: tuple[tuple[str, object], ...],
    stage: CompileStage,
    code: CompileErrorCode,
    pointer: str | None,
    task_template: bool,
) -> None:
    payload = _payload_with_assignments(assignments)

    members = {"config.json": canonical_json_bytes(payload)}
    edges: tuple[DependencyEdge, ...] = ()
    if task_template:
        members["templates/task.txt"] = b"Delegate to {agents}.\n"
        edges = (
            DependencyEdge(
                "config.json", "task_template", "templates/task.txt",
                "templates/task.txt", 0,
            ),
        )
    reader, closure, _ = _inputs(members, edges=edges, root="config.json")
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is stage
    assert caught.value.code is code
    assert caught.value.instance_pointer == pointer
    if code is CompileErrorCode.TEAM_INVALID and pointer == "/multi_agent/team_config":
        assert caught.value.details["reason"] == "duplicate_semantic_carriers"


@pytest.mark.parametrize(
    ("assignments", "code", "pointer"),
    [
        ((('providers/models/0/routing/fallback_model_ids', []), ('providers/models/0/routing/fallback_models', [])), CompileErrorCode.PROVIDER_INVALID, '/providers/models/0/routing/fallback_model_ids'),
        ((('providers/models/0/routing', {}), ('providers/routing', {})), CompileErrorCode.PROVIDER_INVALID, '/providers/models/0/routing'),
        ((('provider_tools', {}), ('providers/provider_tools', {})), CompileErrorCode.PROVIDER_INVALID, '/provider_tools'),
        ((('tool_packs/demo/tools', []), ('tool_packs/demo/tool_ids', [])), CompileErrorCode.TOOL_SELECTION_UNKNOWN, '/tool_packs/demo/tools'),
        ((('tool_packs/higher', {}), ('tools/packs/lower', {})), CompileErrorCode.TOOL_SELECTION_UNKNOWN, '/tool_packs'),
        ((('tool_bindings', []), ('tools/bindings', [])), CompileErrorCode.TOOL_BINDING_INVALID, '/tool_bindings'),
        ((('modes/0/id', 'build'), ('modes/0/name', 'build')), CompileErrorCode.PROMPT_MODE_UNKNOWN, '/modes/0/id'),
        ((('prompts/synthesis', {}), ('prompts/tool_prompt_synthesis', {})), CompileErrorCode.SCHEMA_INVALID_VALUE, '/prompts/synthesis'),
        ((('prompts/synthesis/selection', {}), ('tools/dialects/selection', {})), CompileErrorCode.PROMPT_DIALECT_UNKNOWN, '/prompts/synthesis/selection'),
        ((('guardrails/definitions', []), ('guardrails/guards', [])), CompileErrorCode.GUARDRAIL_INVALID, '/guardrails/definitions'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a'}]}), ('multi_agent/team', {'team_id': 'team', 'agents': [{'id': 'a'}]})), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a', 'config_ref': 'root', 'config_node_id': 'root'}]})), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/agents/0/config_ref'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a'}, {'id': 'b'}], 'edges': [{'from_agent_id': 'a', 'from': 'a', 'to_agent_id': 'b'}]})), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/edges/0/from'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a'}, {'id': 'b'}], 'edges': [{'from_agent_id': 'a', 'to_agent_id': 'b', 'to': 'b'}]})), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/edges/0/to'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a'}], 'async': False}), ('multi_agent/async', False)), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/async'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a'}], 'max_concurrent_agents': 1}), ('multi_agent/max_concurrent_agents', 1)), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/max_concurrent_agents'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a'}], 'scheduler': 'deterministic'}), ('multi_agent/scheduler', 'deterministic')), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/scheduler'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a'}], 'spawn_tool': 'task'}), ('multi_agent/spawn_tool', 'task')), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/spawn_tool'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a'}], 'coordination': {}}), ('multi_agent/coordination', {})), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/coordination'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a'}], 'bus': {}}), ('multi_agent/bus', {})), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/bus'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a'}], 'workspace_sharing': {}}), ('multi_agent/workspace_sharing', {})), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/workspace_sharing'),
        ((('multi_agent/enabled', True), ('multi_agent/team_config', {'team_id': 'team', 'agents': [{'id': 'a'}], 'event_log_path': 'events/a.jsonl'}), ('multi_agent/event_log_path', 'events/b.jsonl')), CompileErrorCode.TEAM_INVALID, '/multi_agent/team_config/event_log_path'),
        ((('task_tool/subagents/worker/description', 'Worker'), ('task_tool/subagents/worker/role', 'Worker')), CompileErrorCode.TASK_SOURCE_INVALID, '/task_tool/subagents/worker/description'),
        ((('task_tool/subagents/worker/config_ref', 'root'), ('task_tool/subagents/worker/config_node_id', 'root')), CompileErrorCode.TASK_SOURCE_INVALID, '/task_tool/subagents/worker/config_ref'),
        ((('task_tool/description_template_path', 'templates/task-a.txt'), ('task_tool/description_template', 'templates/task-b.txt')), CompileErrorCode.TASK_SOURCE_INVALID, '/task_tool/description_template_path'),
        ((('sandbox/driver_id', 'driver:a'), ('sandbox/driver', 'driver:a')), CompileErrorCode.SCHEMA_INVALID_VALUE, '/runtime/sandbox/driver_id'),
        ((('sandbox', {}), ('workspace/sandbox', {})), CompileErrorCode.SCHEMA_INVALID_VALUE, '/sandbox'),
        ((('workspace/driver', 'driver:a'), ('sandbox/driver_id', 'driver:a')), CompileErrorCode.SCHEMA_INVALID_VALUE, '/runtime/sandbox/driver_id'),
        ((('workspace/driver', 'driver:a'), ('sandbox/driver', 'driver:a')), CompileErrorCode.SCHEMA_INVALID_VALUE, '/runtime/sandbox/driver_id'),
        ((('workspace/options', {}), ('sandbox/options', {})), CompileErrorCode.SCHEMA_INVALID_VALUE, '/runtime/sandbox/options'),
        ((('workspace/mounts', []), ('sandbox/mount_requests', [])), CompileErrorCode.SCHEMA_INVALID_VALUE, '/runtime/sandbox/mount_requests'),
        ((('workspace/network', {'mode': 'none'}), ('sandbox/network_request', {'mode': 'none'})), CompileErrorCode.SCHEMA_INVALID_VALUE, '/runtime/sandbox/network_request'),
        ((('workspace/image', 'image:a'), ('sandbox/image_request', 'image:a')), CompileErrorCode.SCHEMA_INVALID_VALUE, '/runtime/sandbox/image_request'),
        ((('resume', {'enabled': False}), ('long_running/resume', {'enabled': False})), CompileErrorCode.SCHEMA_INVALID_VALUE, '/long_running/resume'),
        ((('loop/sequence', [{'if': True, 'then': 'build', 'mode': 'build'}]),), CompileErrorCode.SCHEMA_INVALID_VALUE, '/loop/sequence/0/mode'),
    ],
)
def test_simultaneous_valid_semantic_carriers_are_typed_conflicts(
    assignments: tuple[tuple[str, object], ...],
    code: CompileErrorCode,
    pointer: str,
) -> None:
    payload = _payload_with_assignments(assignments)
    reader, closure, _ = _inputs(
        {"config.json": canonical_json_bytes(payload)},
        root="config.json",
    )
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is code
    assert caught.value.instance_pointer == pointer
    assert caught.value.details["reason"] == "duplicate_semantic_carriers"


def test_tools_packs_mapping_is_compiled_instead_of_silently_dropped() -> None:
    payload = canonical_json_loads(
        canonical_json_bytes(strict_parse_payload(_MINIMAL_CONFIG, logical_path="config.yaml"))
    )
    payload["tools"] = {
        "packs": {
            "compact": {
                "description": "No-op compact pack.",
                "tools": [],
                "support_status": "supported",
            }
        }
    }
    manifest, _, _ = _compile(
        {"config.json": canonical_json_bytes(payload)},
        root="config.json",
    )
    assert manifest.semantic.to_canonical_obj()["tools"]["packs"] == [
        {
            "pack_id": "compact",
            "description": "No-op compact pack.",
            "tool_ids": [],
            "exposure": "model",
            "support_status": "supported",
        }
    ]


def test_tool_parameter_schema_carrier_validates_inline_schema_before_selection() -> None:
    tool = canonical_json_loads(_tool_schema_member("#/$defs/payload"))
    tool["parameters"][0]["$ref"] = "https://attacker.example/schema"
    members = {
        "config.yaml": _tool_root(),
        "tools/schema.yaml": canonical_json_bytes(tool),
    }
    edge = DependencyEdge(
        "config.yaml", "tool_registry", "tools", "tools/schema.yaml", 0
    )
    reader, closure, _ = _inputs(members, edges=(edge,))
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.REFERENCE_RESOLUTION
    assert caught.value.code is CompileErrorCode.TOOL_INVALID
    assert caught.value.instance_pointer == "/parameters/0/schema/$ref"

    tool["parameters"][0]["$ref"] = "#/$defs/payload"
    reader, closure, _ = _inputs(
        {
            "config.yaml": _tool_root(),
            "tools/schema.yaml": canonical_json_bytes(tool),
        },
        edges=(edge,),
    )
    with pytest.raises(ConfigCompileError) as duplicate:
        compile_config(reader, closure, _options())
    assert duplicate.value.stage is CompileStage.SCHEMA
    assert duplicate.value.code is CompileErrorCode.TOOL_INVALID
    assert duplicate.value.instance_pointer == "/parameters/0/schema"
    assert duplicate.value.details["reason"] == "duplicate_semantic_carriers"


def test_top_and_loop_turn_strategy_cannot_define_the_same_field() -> None:
    payload = canonical_json_loads(
        canonical_json_bytes(strict_parse_payload(_MINIMAL_CONFIG, logical_path="config.yaml"))
    )
    payload["turn_strategy"] = {"relay": "continuation"}
    payload["loop"]["turn_strategy"] = {"relay": "continuation"}
    reader, closure, _ = _inputs(
        {"config.json": canonical_json_bytes(payload)},
        root="config.json",
    )
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is CompileErrorCode.SCHEMA_INVALID_VALUE
    assert caught.value.instance_pointer == "/turn_strategy/relay"
    assert caught.value.details["reason"] == "duplicate_semantic_carriers"

@pytest.mark.parametrize("carrier", ["workspace", "sandbox"])
def test_explicit_null_resource_carriers_are_not_treated_as_absent(
    carrier: str,
) -> None:
    payload = canonical_json_loads(
        canonical_json_bytes(strict_parse_payload(_MINIMAL_CONFIG, logical_path="config.yaml"))
    )
    if carrier == "workspace":
        payload["workspace"]["resources"] = None
    else:
        payload["sandbox"] = {"resource_request": None}
    reader, closure, _ = _inputs(
        {"config.json": canonical_json_bytes(payload)},
        root="config.json",
    )
    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())
    assert caught.value.stage is CompileStage.SCHEMA
    assert caught.value.code is CompileErrorCode.SCHEMA_TYPE_MISMATCH
    assert caught.value.instance_pointer == "/runtime/sandbox/resource_request"


def test_user_identity_shaped_metadata_remains_semantic_and_distinct() -> None:
    first_source = _MINIMAL_CONFIG.replace(
        b"  name: generated-unclassified-agent\n",
        b"  name: generated-unclassified-agent\n  metadata: {node_id: user-a, nested: {config_node_id: config-a, variant_id: variant-a}}\n",
    )
    second_source = first_source.replace(b"user-a", b"user-b").replace(
        b"variant-a", b"variant-b"
    )
    first, _, _ = _compile({"config.yaml": first_source})
    second, _, _ = _compile({"config.yaml": second_source})
    first_metadata = first.semantic.to_canonical_obj()["metadata"]["profile_metadata"]
    second_metadata = second.semantic.to_canonical_obj()["metadata"]["profile_metadata"]
    assert first_metadata == {
        "node_id": "user-a",
        "nested": {
            "config_node_id": "config-a",
            "variant_id": "variant-a",
        },
    }
    assert second_metadata["node_id"] == "user-b"
    assert first.semantic_digest != second.semantic_digest


def test_source_dependency_shaped_user_metadata_is_not_rewritten_or_deleted() -> None:
    digest = "sha256:" + "0" * 64
    shaped = {
        "dependency_kind": "user-metadata",
        "from_logical_path": "user/from",
        "raw_reference": "user-a",
        "logical_path": "user/to",
        "blob_digest": digest,
        "size_bytes": 1,
        "media_type": "text/plain",
    }
    first_data = canonical_json_loads(
        canonical_json_bytes(
            strict_parse_payload(_MINIMAL_CONFIG, logical_path="config.yaml")
        )
    )
    first_data["profile"]["metadata"] = {"shaped": shaped}
    second_data = copy.deepcopy(first_data)
    second_data["profile"]["metadata"]["shaped"]["raw_reference"] = "user-b"
    first, _, _ = _compile({"config.json": canonical_json_bytes(first_data)}, root="config.json")
    second, _, _ = _compile({"config.json": canonical_json_bytes(second_data)}, root="config.json")
    first_metadata = first.semantic.to_canonical_obj()["metadata"]["profile_metadata"]
    second_metadata = second.semantic.to_canonical_obj()["metadata"]["profile_metadata"]
    assert first_metadata["shaped"] == shaped
    assert second_metadata["shaped"]["raw_reference"] == "user-b"
    assert first.semantic_digest != second.semantic_digest


@pytest.mark.parametrize("kind", ["depth", "nodes"])
def test_json_resource_bombs_are_denied_before_json_object_construction(
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = (
        _nested_object(server_compiler.MAX_DOCUMENT_DEPTH + 1)
        if kind == "depth"
        else {"values": [0] * (server_compiler.MAX_DOCUMENT_NODES - 1)}
    )
    payload = json.dumps(value).encode()

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("over-budget JSON reached json.loads")

    monkeypatch.setattr(server_compiler.json, "loads", forbidden)
    with pytest.raises(ConfigCompileError) as caught:
        strict_parse_payload(
            payload,
            logical_path="bomb.json",
            media_type="application/json",
        )
    assert caught.value.stage is CompileStage.PARSE
    assert caught.value.code is CompileErrorCode.RESOURCE_LIMIT_EXCEEDED


@pytest.mark.parametrize("kind", ["depth", "nodes"])
def test_yaml_resource_bombs_are_denied_before_yaml_node_construction(
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = (
        b"value: "
        + b"[" * (server_compiler.MAX_DOCUMENT_DEPTH + 1)
        + b"0"
        + b"]" * (server_compiler.MAX_DOCUMENT_DEPTH + 1)
        if kind == "depth"
        else b"values: [" + b"0," * server_compiler.MAX_DOCUMENT_NODES + b"0]"
    )

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("over-budget YAML reached yaml.compose")

    monkeypatch.setattr(server_compiler.yaml, "compose", forbidden)
    with pytest.raises(ConfigCompileError) as caught:
        strict_parse_payload(payload, logical_path="bomb.yaml")
    assert caught.value.stage is CompileStage.PARSE
    assert caught.value.code is CompileErrorCode.RESOURCE_LIMIT_EXCEEDED


def test_manifest_schema_version_binds_compiler_input_and_cache_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, closure, _ = _inputs()
    options = _options()
    manifest = compile_config(reader, closure, options)
    preimage = manifest.compiler_input_preimage()
    assert preimage["manifest_schema_version"] == manifest.compiler.manifest_schema_version
    baseline = compiler_cache_key(closure, options)

    with monkeypatch.context() as patch:
        patch.setattr(
            server_compiler._contracts_module,
            "COMPILED_CONFIG_MANIFEST_SCHEMA_VERSION",
            manifest.compiler.manifest_schema_version + 1,
        )
        changed = compiler_cache_key(closure, options)
    assert changed != baseline
