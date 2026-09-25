"""Pure E4 package serialization, input binding, and lowering of verified assets."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import Any

import yaml
from jinja2 import StrictUndefined, Template

from breadboard_engine.compilation.contracts import (
    DependencyEdge,
    canonical_json_bytes,
    canonical_sha256,
)
from breadboard_engine.e4_targets import E4TargetPackage, read_e4_target

from .compile import HarnessCompilation, HarnessCompileError, compile_harness_definition
from .lock import copy_harness_json
from .validate import (
    HarnessDefinitionValidationError,
    validate_e4_target_document,
    validate_e4_target_input_values,
)


_NATIVE_WORKER_RECIPES = MappingProxyType({
    "openhands-sdk@1.47.0": (
        "breadboard.openhands-sdk.v1.47.0",
        "8df535e010d344658393836920e031c43762e43af34d28b9c3cb3012e4c19910",
    ),
    "pi@0.73.1": (
        "breadboard.pi-coding-agent.v0.73.1",
        "2834e64d081edede815bd1fa81d8ad423b5d0bd1c2e6466ca3ba5060c12a5003",
    ),
    "hermes-agent@2026.9.11": (
        "breadboard.hermes-agent.v2026.9.11",
        "36dbabb294042943df6c6f5415eebbe4097f660bc99af1d91f052d76da7eca88",
    ),
    "openclaw@2026.9.4": (
        "breadboard.openclaw.native-chat.v1",
        "8cd597424ae5ab817574b7f6f57887fa1422c56e65383116e0d37190a42d3f6a",
    ),
})


class E4TargetCapabilityError(HarnessCompileError):
    def __init__(self, renderer_id: str, required_capabilities: tuple[str, ...]) -> None:
        self.renderer_id = renderer_id
        self.required_capabilities = required_capabilities
        super().__init__(
            f"Unsupported E4 renderer {renderer_id!r}; required runtime capabilities: "
            + ", ".join(required_capabilities)
        )


@dataclass(frozen=True, slots=True)
class E4TargetMaterialization:
    members: Mapping[str, bytes]
    index_delta: bytes


def serialize_e4_target(
    *,
    descriptor_path: str,
    descriptor: Mapping[str, Any],
    configuration: Mapping[str, Any],
    assets: Mapping[str, bytes],
) -> E4TargetMaterialization:
    """Serialize a v2 recipe's assets and an additive index fragment, without installing."""
    if type(descriptor_path) is not str or descriptor_path.rpartition("/")[2] != "target.json":
        raise HarnessCompileError("E4 descriptor path must end in target.json")
    definition = copy_harness_json(descriptor, freeze=False)
    config = copy_harness_json(configuration, freeze=False)
    if definition.get("schema_version") != "bb.e4.target.v2" or "assets" in definition:
        raise HarnessCompileError("v2 descriptor fields must omit the derived asset table")
    if config.get("schema_version") != "bb.e4.target_config.v2":
        raise HarnessCompileError("E4 serialization requires target_config.v2")
    if findings := validate_e4_target_document(config):
        raise HarnessDefinitionValidationError(findings)
    execution = definition.get("execution")
    if not isinstance(execution, Mapping) or type(execution.get("config_asset")) is not str:
        raise HarnessCompileError("E4 configuration asset is required")
    config_asset = execution["config_asset"]
    contents = dict(assets)
    if any(type(path) is not str or type(content) is not bytes for path, content in contents.items()):
        raise HarnessCompileError("E4 assets require text paths and exact bytes")
    if config_asset in contents:
        raise HarnessCompileError("E4 configuration bytes are derived, not supplied as an asset")
    contents[config_asset] = yaml.safe_dump(
        config, sort_keys=False, allow_unicode=True
    ).encode("utf-8")
    order = config["materialization"]["order"]
    if set(contents) != set(order):
        raise HarnessCompileError("E4 serialization requires exactly the declared materialization assets")
    definition["assets"] = [
        {"path": path, "sha256": "sha256:" + sha256(contents[path]).hexdigest(), "bytes": len(contents[path])}
        for path in order
    ]
    if findings := validate_e4_target_document(definition):
        raise HarnessDefinitionValidationError(findings)
    descriptor_bytes = (
        json.dumps(definition, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    parent = descriptor_path.rpartition("/")[0]
    prefix = parent + "/" if parent else ""
    members = {prefix + path: contents[path] for path in order}
    if descriptor_path in members or "index.json" in members:
        raise HarnessCompileError("E4 asset collides with its descriptor or index")
    members[descriptor_path] = descriptor_bytes
    index_delta = (
        json.dumps(
            {
                "schema_version": "bb.e4.target_index.v1",
                "targets": {
                    definition["target_id"]: {
                        "descriptor": descriptor_path,
                        "sha256": sha256(descriptor_bytes).hexdigest(),
                    }
                },
            },
            sort_keys=True,
            indent=2,
        ) + "\n"
    ).encode("utf-8")
    read_e4_target(
        definition["target_id"],
        read_resource=lambda path: index_delta if path == "index.json" else members[path],
    )
    return E4TargetMaterialization(MappingProxyType(members), index_delta)


def _encode_e4_target_input_frame(
    request_schema_version: str,
    dynamic_fields: Mapping[str, Any],
) -> bytes:
    frame = {
        "request_schema_version": request_schema_version,
        "target_dynamic_fields": dynamic_fields,
    }
    if request_schema_version == "bb.rl.headless-run-request.v1":
        if any(type(value) is not str or not value for value in dynamic_fields.values()):
            raise ValueError("v1 target inputs require non-empty text")
        return canonical_json_bytes(frame)
    if request_schema_version not in {
        "bb.rl.headless-run-request.v2",
        "bb.rl.headless-run-request.v3",
    }:
        raise ValueError("unsupported E4 request input revision")
    # Constructor-visible object order and 1 versus 1.0 survive this encoding.
    # The resulting bytes, rather than a JCS-normalized object, are the identity.
    return json.dumps(
        frame,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def serialize_e4_target_inputs(
    request_schema_version: str,
    dynamic_fields: Mapping[str, Any],
) -> bytes:
    """Encode a JSON input frame without profile-dependent omission or coercion."""
    fields = (
        dynamic_fields
        if request_schema_version == "bb.rl.headless-run-request.v1"
        else copy_harness_json(dynamic_fields, freeze=False)
    )
    return _encode_e4_target_input_frame(request_schema_version, fields)


def bind_e4_target_inputs(
    package: E4TargetPackage,
    request_schema_version: str,
    dynamic_fields: Mapping[str, Any],
) -> bytes:
    """Validate bootstrap values against their verified declaration and encode them."""
    target_version = package.descriptor.get("schema_version")
    supported_requests = {
        "bb.e4.target.v1": {
            "bb.rl.headless-run-request.v1",
            "bb.rl.headless-run-request.v3",
        },
        "bb.e4.target.v2": {
            "bb.rl.headless-run-request.v2",
            "bb.rl.headless-run-request.v3",
        },
    }.get(target_version, set())
    if request_schema_version not in supported_requests:
        raise ValueError("headless request and target versions do not match")
    if target_version == "bb.e4.target.v1":
        if any(type(value) is not str or not value for value in dynamic_fields.values()):
            raise ValueError("legacy target inputs require non-empty text")
        return serialize_e4_target_inputs(request_schema_version, dynamic_fields)

    execution = package.descriptor.get("execution")
    if not isinstance(execution, Mapping) or type(execution.get("config_asset")) is not str:
        raise ValueError("E4 target configuration asset is required")
    from breadboard_engine.compilation.server_compiler import strict_parse_payload

    configuration = strict_parse_payload(
        package.read_asset_bytes(execution["config_asset"]),
        logical_path=f"{package.descriptor_path}:{execution['config_asset']}",
    )
    if (
        type(configuration) is not dict
        or configuration.get("schema_version") != "bb.e4.target_config.v2"
        or configuration.get("target_id") != package.target_id
    ):
        raise ValueError("E4 target configuration identity does not match")
    fields = copy_harness_json(dynamic_fields, freeze=False)
    findings = validate_e4_target_input_values(configuration, fields)
    if findings:
        raise HarnessDefinitionValidationError(findings)
    declaration = configuration["inputs"]
    ordered = {name: fields[name] for name in declaration["order"] if name in fields}
    return _encode_e4_target_input_frame(request_schema_version, ordered)


@dataclass(frozen=True, slots=True)
class E4TargetRendering:
    target_id: str
    overlay_id: str
    descriptor_digest: str
    execution_config_digest: str
    overlay_digest: str
    rendered_prompt_digest: str | None
    system_prompt: str | None
    ordered_tool_names: tuple[str, ...]
    tools: tuple[Mapping[str, Any], ...]
    renderer_id: str = "breadboard.e4.legacy-string-template.v1"
    runtime_profile: Mapping[str, Any] | None = None


def _lower_mini_target(
    package: E4TargetPackage,
    harness: Mapping[str, Any],
    dynamic_fields: Mapping[str, Any],
) -> E4TargetRendering:
    """Lower the pinned Mini recipe; runtime facts are supplied inside its lease."""
    # This renderer implements one source composition, not arbitrary Mini configs.
    # The descriptor transitively binds every serializer-produced source asset.
    if (
        package.target_id != "mini-swe-agent@2.4.6"
        or sha256(package.descriptor_bytes).hexdigest()
        != "191999cd6da077ad29d8413356a7e5e2c1ae98c8448759f8c8f29e81ebfca85e"
        or dynamic_fields
    ):
        raise HarnessCompileError("Mini requires its pinned recipe and no caller template inputs")
    native = json.loads(package.read_asset_text("native-config.json"))
    system_prompt = Template(
        native["agent"]["system_template"], undefined=StrictUndefined
    ).render()
    surface = json.loads(package.read_asset_text("tool-surface.json"))
    tool = surface["tools"]["bash"]
    descriptor = package.descriptor
    overlay = descriptor["overlay"]
    return E4TargetRendering(
        target_id=package.target_id,
        overlay_id=overlay["overlay_id"],
        descriptor_digest=canonical_sha256(descriptor),
        execution_config_digest=canonical_sha256(harness),
        overlay_digest=canonical_sha256(overlay),
        rendered_prompt_digest=canonical_sha256({"text": system_prompt}),
        system_prompt=system_prompt,
        ordered_tool_names=("bash",),
        tools=(copy_harness_json({"name": "bash", **tool}, freeze=True),),
        renderer_id="breadboard.mini-swe-agent.v2.4.6",
        runtime_profile=copy_harness_json(native, freeze=True),
    )


def _lower_worker_target(
    package: E4TargetPackage,
    harness: Mapping[str, Any],
    dynamic_fields: Mapping[str, Any],
) -> E4TargetRendering:
    """Bind source assets; the owned source worker renders runtime-dependent fields."""
    recipe = _NATIVE_WORKER_RECIPES.get(package.target_id)
    if (
        recipe is None
        or sha256(package.descriptor_bytes).hexdigest() != recipe[1]
        or harness["renderer"]["selector"] != recipe[0]
        or dynamic_fields
    ):
        raise HarnessCompileError("Native worker targets require their pinned recipe and no caller template inputs")
    native = json.loads(package.read_asset_text("native-config.json"))
    surface = json.loads(package.read_asset_text("tool-surface.json"))
    order = tuple(surface["ordered_tools"])
    compiler_tools = []
    for name in order:
        tool = {"name": name, **surface["tools"][name]}
        # The compiler encodes required order through parameter order; the
        # native HTTP path retains the untouched schemas in runtime_profile.
        parameters = tool["parameters"]
        required = parameters.get("required", [])
        properties = parameters["properties"]
        if [key for key in properties if key in required] != required:
            ordered_properties = {key: properties[key] for key in required}
            ordered_properties.update(properties)
            properties = ordered_properties
        tool["parameters"] = {
            **parameters, "properties": properties, "required": required,
        }
        compiler_tools.append(copy_harness_json(tool, freeze=True))
    descriptor = package.descriptor
    overlay = descriptor["overlay"]
    return E4TargetRendering(
        target_id=package.target_id,
        overlay_id=overlay["overlay_id"],
        descriptor_digest=canonical_sha256(descriptor),
        execution_config_digest=canonical_sha256(harness),
        overlay_digest=canonical_sha256(overlay),
        rendered_prompt_digest=None,
        system_prompt=None,
        ordered_tool_names=order,
        tools=tuple(compiler_tools),
        renderer_id=recipe[0],
        runtime_profile=copy_harness_json(native, freeze=True),
    )


def lower_e4_target(
    package: E4TargetPackage,
    dynamic_fields: Mapping[str, Any],
) -> E4TargetRendering:
    """Render declared bytes without distribution lookup or runtime activation."""
    target_id = package.target_id
    descriptor = package.descriptor
    target_version = descriptor.get("schema_version")
    if target_version not in ("bb.e4.target.v1", "bb.e4.target.v2"):
        raise HarnessCompileError("unsupported E4 target revision")
    execution = descriptor.get("execution")
    overlay = descriptor.get("overlay")
    if not isinstance(execution, Mapping) or not isinstance(overlay, Mapping):
        raise ValueError("E4 target execution and overlay descriptors are required")
    config_asset = execution.get("config_asset")
    prompt_asset = execution.get("system_prompt_asset")
    prompt_source = execution.get("system_prompt_source")
    tool_asset = execution.get("tool_surface_asset")
    if (
        any(type(value) is not str or not value for value in (config_asset, tool_asset))
        or (
            (type(prompt_asset) is not str or not prompt_asset)
            and (type(prompt_source) is not str or not prompt_source)
        )
    ):
        raise ValueError("E4 target execution assets or prompt source are invalid")
    from breadboard_engine.compilation.server_compiler import strict_parse_payload

    harness = strict_parse_payload(
        package.read_asset_bytes(config_asset),
        logical_path=f"{package.descriptor_path}:{config_asset}",
        legacy_yaml_scalars=target_version == "bb.e4.target.v1",
    )
    if type(harness) is not dict or harness.get("target_id") != target_id:
        raise ValueError("E4 target harness identity is invalid")
    if target_version == "bb.e4.target.v2":
        if findings := validate_e4_target_document(harness):
            raise HarnessDefinitionValidationError(findings)
        if harness["schema_version"] != "bb.e4.target_config.v2":
            raise HarnessCompileError("E4 target configuration revision does not match")
        if prompt_asset is None and harness["renderer"]["selector"] != "breadboard.openclaw.native-chat.v1":
            raise HarnessCompileError("only the pinned OpenClaw worker may render a source prompt")
        if harness["renderer"]["selector"] == "breadboard.mini-swe-agent.v2.4.6":
            return _lower_mini_target(package, harness, dynamic_fields)
        if harness["renderer"]["selector"] in {
            "breadboard.openhands-sdk.v1.47.0",
            "breadboard.pi-coding-agent.v0.73.1",
            "breadboard.hermes-agent.v2026.9.11",
            "breadboard.openclaw.native-chat.v1",
        }:
            return _lower_worker_target(package, harness, dynamic_fields)
        raise E4TargetCapabilityError(
            harness["renderer"]["selector"], tuple(harness["required_capabilities"])
        )
    if (
        harness.get("schema_version") != "bb.e4.target_config.v1"
        or set(harness) != {
            "schema_version", "target_id", "prompt", "tools",
            "session", "thinking", "retry", "transport",
        }
    ):
        raise ValueError("E4 target configuration revision or fields are unsupported")
    retry = harness.get("retry")
    if (
        harness.get("session") != {
            "persistence": "disabled", "extensions": "disabled",
            "skills": "disabled", "prompt_templates": "disabled",
        }
        or harness.get("thinking") is not False
        or type(retry) is not dict
        or set(retry) != {"enabled", "max_retries"}
        or retry["enabled"] is not False
        or type(retry["max_retries"]) is not int
        or retry["max_retries"] != 0
        or harness.get("transport") != {"mode": "json"}
    ):
        raise ValueError("E4 target runtime policy is unsupported")
    prompt_config = harness.get("prompt")
    tools_config = harness.get("tools")
    if type(prompt_config) is not dict or type(tools_config) is not dict:
        raise ValueError("E4 target prompt and tool configuration is invalid")
    if (
        set(prompt_config) != {"renderer", "asset", "dynamic_fields"}
        or prompt_config.get("renderer") != "pi-0.57.1"
        or prompt_config.get("asset") != prompt_asset
        or set(tools_config) != {"ordered", "surface_asset"}
        or tools_config.get("surface_asset") != tool_asset
    ):
        raise ValueError("E4 target renderer or asset references are unsupported")
    required_fields = prompt_config.get("dynamic_fields")
    if (
        type(required_fields) is not list
        or not required_fields
        or any(type(value) is not str or not value for value in required_fields)
        or len(set(required_fields)) != len(required_fields)
        or set(dynamic_fields) != set(required_fields)
    ):
        raise ValueError("E4 target dynamic fields do not match the target contract")
    prompt_template = package.read_asset_text(prompt_asset)
    values: dict[str, str] = {}
    for field_name in required_fields:
        value = dynamic_fields[field_name]
        if type(value) is not str or not value or len(value.encode("utf-8")) > 16_384:
            raise ValueError(f"E4 target dynamic field {field_name!r} is invalid")
        values[field_name] = value
    placeholder = re.compile(
        r"\{\{(" + "|".join(re.escape(name) for name in required_fields) + r")\}\}"
    )
    rendered_prompt = placeholder.sub(
        lambda match: values[match.group(1)], prompt_template
    )
    # Historical v1 also rejects placeholder-shaped text introduced by a value.
    if (
        re.search(r"\{\{[^{}]+\}\}", rendered_prompt)
        or len(rendered_prompt.encode("utf-8")) > 512 * 1024
    ):
        raise ValueError("E4 target prompt rendering is invalid or too large")
    surface = json.loads(package.read_asset_text(tool_asset))
    ordered_names = tools_config.get("ordered")
    if (
        type(surface) is not dict
        or surface.get("target_id") != target_id
        or type(ordered_names) is not list
        or surface.get("ordered_tools") != ordered_names
        or any(type(name) is not str or not name for name in ordered_names)
    ):
        raise ValueError("E4 target tool ordering is invalid")
    surface_tools = surface.get("tools")
    if type(surface_tools) is not dict or set(surface_tools) != set(ordered_names):
        raise ValueError("E4 target tool surface is incomplete")
    tools: list[Mapping[str, Any]] = []
    for name in ordered_names:
        tool = surface_tools[name]
        if (
            type(tool) is not dict
            or type(tool.get("description")) is not str
            or type(tool.get("parameters")) is not dict
        ):
            raise ValueError(f"E4 target tool {name!r} is invalid")
        tools.append(copy_harness_json({
            "name": name,
            "description": tool["description"],
            "parameters": tool["parameters"],
        }, freeze=True))
    overlay_id = overlay.get("overlay_id")
    if type(overlay_id) is not str or not overlay_id:
        raise ValueError("E4 target overlay identity is invalid")
    return E4TargetRendering(
        target_id=target_id,
        overlay_id=overlay_id,
        descriptor_digest=canonical_sha256(descriptor),
        execution_config_digest=canonical_sha256(harness),
        overlay_digest=canonical_sha256(overlay),
        rendered_prompt_digest=canonical_sha256({"text": rendered_prompt}),
        system_prompt=rendered_prompt,
        ordered_tool_names=tuple(ordered_names),
        tools=tuple(tools),
        renderer_id="breadboard.e4.legacy-string-template.v1",
    )


@dataclass(frozen=True, slots=True)
class E4HarnessSource:
    compilation: HarnessCompilation
    rendering: E4TargetRendering
    source_ref: str
    lock_ref: str
    members: Mapping[str, bytes]
    edges: tuple[DependencyEdge, ...]


def lower_e4_harness(
    package: E4TargetPackage,
    dynamic_fields: Mapping[str, Any],
    runtime_configuration: Mapping[str, Any],
    *,
    request_schema_version: str = "bb.rl.headless-run-request.v1",
) -> E4HarnessSource:
    """Capture target derivation inputs and lower into the existing config language."""
    input_bytes = bind_e4_target_inputs(package, request_schema_version, dynamic_fields)
    fields = json.loads(input_bytes)["target_dynamic_fields"]
    rendered = lower_e4_target(package, fields)
    config = copy_harness_json(runtime_configuration, freeze=False)
    if type(config.get("version")) is not int or config["version"] != 2:
        raise HarnessCompileError("E4 runtime configuration requires version 2")
    owned = {"extends", "prompts", "tools", "modes", "loop", "e4_target"}
    if owned.intersection(config):
        raise HarnessCompileError("runtime configuration overrides target-owned fields")

    source_ref = "e4-harness.json"
    inputs_ref = "e4-inputs.json"
    runtime_ref = "e4-runtime.json"
    lock_ref = "e4-harness.lock.json"
    package_prefix = "config/e4_targets/"
    index_ref = package_prefix + "index.json"
    descriptor_ref = package_prefix + package.descriptor_path
    descriptor_parent = package.descriptor_path.rpartition("/")[0]
    members = {
        inputs_ref: input_bytes,
        runtime_ref: canonical_json_bytes(config),
        index_ref: package.index_bytes,
        descriptor_ref: package.descriptor_bytes,
    }
    edges = [
        DependencyEdge(source_ref, "e4_target_index", index_ref, index_ref, 0),
        DependencyEdge(source_ref, "e4_target_inputs", inputs_ref, inputs_ref, 0),
        DependencyEdge(source_ref, "e4_target_runtime", runtime_ref, runtime_ref, 0),
        DependencyEdge(source_ref, "e4_target_lock", lock_ref, lock_ref, 0),
        DependencyEdge(index_ref, "e4_target_descriptor", package.descriptor_path, descriptor_ref, 0),
    ]
    for ordinal, (relative_path, content) in enumerate(package.assets.items()):
        path = package_prefix + (
            descriptor_parent + "/" + relative_path if descriptor_parent else relative_path
        )
        members[path] = content
        edges.append(DependencyEdge(descriptor_ref, "e4_target_asset", relative_path, path, ordinal))

    for ordinal, tool in enumerate(rendered.tools):
        schema = tool["parameters"]
        if (
            set(schema) - {"type", "properties", "patternProperties", "required", "additionalProperties"}
            or schema.get("type") != "object"
            or not isinstance(schema.get("properties"), Mapping)
        ):
            raise HarnessCompileError("target tool schema cannot be represented by the compiler")
        properties = schema["properties"]
        required = schema.get("required", ())
        if not isinstance(required, tuple):
            raise HarnessCompileError("target required-parameter order cannot be preserved")
        if (
            any(type(name) is not str or name not in properties for name in required)
            or tuple(name for name in properties if name in required) != required
        ):
            raise HarnessCompileError("target required-parameter order cannot be preserved")
        definition = {
            "id": tool["name"],
            "name": tool["name"],
            "description": tool["description"],
            "parameters": [
                {"name": name, "required": name in required, "schema": parameter}
                for name, parameter in properties.items()
            ],
        }
        if "additionalProperties" in schema:
            definition["provider_routing"] = {
                "openai": {"additionalProperties": schema["additionalProperties"]}
            }
        path = f"e4-tools/{ordinal:04d}.yaml"
        members[path] = canonical_json_bytes(definition)
        edges.append(DependencyEdge(source_ref, "tool_registry", "e4-tools", path, ordinal))

    config.update({
        "e4_target": {
            "target_id": package.target_id,
            "index_ref": index_ref,
            "inputs_ref": inputs_ref,
            "runtime_ref": runtime_ref,
            "lock_ref": lock_ref,
        },
        "prompts": {
            "renderer_id": "breadboard.prompt-assembly.verbatim.v1",
            "tool_prompt_mode": "native_only",
            "injection": {"system_order": ["mode_specific"], "per_turn_order": []},
        },
        "tools": {
            "registry": {"paths": ["e4-tools"], "include": list(rendered.ordered_tool_names)},
        },
        "modes": [{
            "id": "build",
            **({"prompt": rendered.system_prompt} if rendered.system_prompt is not None else {}),
            "tools_enabled": list(rendered.ordered_tool_names),
        }],
        "loop": {"sequence": ["build"]},
    })
    compilation = compile_harness_definition(
        config, source_ref=source_ref, resource_inputs=members
    )
    members[source_ref] = canonical_json_bytes(config)
    return E4HarnessSource(
        compilation=compilation,
        rendering=rendered,
        source_ref=source_ref,
        lock_ref=lock_ref,
        members=MappingProxyType(members),
        edges=tuple(edges),
    )
