"""Canonical tool definitions, calls, and results."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional
from .contract_wire import (ProviderContractError, _canonical_value, _coalesce_text_fields, _require_text, _strict_dict, canonical_json)

def _reject_native_json_constant(value):
    # Python accepts these extensions; stock JSON.parse rejects them.
    raise ValueError(f"Invalid JSON constant: {value}")


@dataclass
class ProviderToolCall:
    """Normalized provider tool call used by runtime adapters.

    ``arguments`` remains the canonical JSON string consumed by existing
    execution paths. ``arguments_json`` is the same canonical spelling, while
    ``parsed_arguments`` owns the lossless JSON value used by exchange v2.
    Native execution may opt into ``allow_raw_arguments``. Malformed JSON then
    remains lexical text in ``raw_arguments`` and has no parsed JSON value.
    """

    id: Optional[str]
    name: Optional[str]
    arguments: Any = None
    type: str = "function"
    raw: Any = None
    arguments_json: Optional[str] = None
    allow_raw_arguments: bool = False
    raw_arguments: Optional[str] = None
    parsed_arguments: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.id is not None:
            self.id = _require_text(self.id, "tool call id")
        if self.name is not None:
            self.name = _require_text(self.name, "tool name")

        if self.raw_arguments is not None:
            if not self.allow_raw_arguments or not isinstance(self.raw_arguments, str):
                raise ProviderContractError("raw tool arguments require explicit native admission")
            if len(self.raw_arguments) > 65536:
                raise ProviderContractError("raw tool arguments exceed the contract bound")
            if self.arguments is not None or self.arguments_json is not None:
                raise ProviderContractError("raw tool arguments cannot include parsed JSON arguments")
            self.arguments = self.raw_arguments
            self.parsed_arguments = None
            self.type = _require_text(self.type, "tool call type")
            if self.type != "function":
                raise ProviderContractError(f"unsupported tool call type: {self.type!r}")
            return
        if self.arguments_json is not None:
            if not isinstance(self.arguments_json, str):
                raise ProviderContractError("arguments_json must be text")
            try:
                parsed = json.loads(self.arguments_json)
            except (TypeError, ValueError):
                raise ProviderContractError("malformed tool call arguments") from None
            if self.arguments is not None:
                try:
                    supplied = (
                        json.loads(self.arguments)
                        if isinstance(self.arguments, str)
                        else _canonical_value(self.arguments)
                    )
                except (TypeError, ValueError, ProviderContractError):
                    raise ProviderContractError(
                        "malformed tool call arguments"
                    ) from None
                if supplied != parsed:
                    raise ProviderContractError(
                        "tool call arguments disagree with arguments_json"
                    )
        elif self.arguments is None:
            parsed = {}
        elif isinstance(self.arguments, str):
            try:
                parsed = json.loads(self.arguments, parse_constant=(
                    _reject_native_json_constant if self.allow_raw_arguments else None
                ))
            except (TypeError, ValueError):
                if not self.allow_raw_arguments:
                    raise ProviderContractError("malformed tool call arguments") from None
                if len(self.arguments) > 65536:
                    raise ProviderContractError("raw tool arguments exceed the contract bound")
                self.raw_arguments = self.arguments
                self.parsed_arguments = None
                self.type = _require_text(self.type, "tool call type")
                if self.type != "function":
                    raise ProviderContractError(f"unsupported tool call type: {self.type!r}")
                return
        else:
            parsed = _canonical_value(self.arguments)

        canonical = canonical_json(parsed)
        self.arguments = canonical
        self.arguments_json = canonical
        self.parsed_arguments = parsed
        self.type = _require_text(self.type, "tool call type")
        if self.type != "function":
            raise ProviderContractError(
                f"unsupported tool call type: {self.type!r}"
            )

    def as_dict(self) -> Dict[str, Any]:
        if self.id is None or self.name is None:
            raise ProviderContractError("completed tool call requires id and name")
        if self.raw_arguments is not None:
            return {"call_id": self.id, "name": self.name, "raw_arguments": self.raw_arguments}
        return {
            "call_id": self.id,
            "name": self.name,
            "arguments_json": self.arguments_json,
            "arguments": self.parsed_arguments,
        }


def _normalize_tool_schema(tool: Any, index: int, *, api_variant: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(tool, Mapping):
        raise ProviderContractError(f"request.tools[{index}] must be an object")
    if api_variant == "responses" and "function" not in tool and tool.get("type") in {
        "function", "namespace", "web_search", "custom", "tool_search",
    }:
        field_name = f"request.tools[{index}]"
        kind = tool["type"]
        if kind == "function":
            function = _strict_dict(
                tool, field_name=field_name,
                allowed={"type", "name", "description", "parameters", "strict", "defer_loading"},
            )
            if not isinstance(function.get("parameters"), Mapping):
                raise ProviderContractError(f"{field_name}.parameters must be an object")
            if "strict" in function and not isinstance(function["strict"], bool):
                raise ProviderContractError(f"{field_name}.strict must be boolean")
            if "defer_loading" in function and not isinstance(function["defer_loading"], bool):
                raise ProviderContractError(f"{field_name}.defer_loading must be boolean")
            # Validate the function contract without changing its flat wire shape.
            _normalize_tool_schema({
                key: value for key, value in function.items() if key not in {"type", "defer_loading"}
            }, index)
        elif kind == "namespace":
            namespace = _strict_dict(
                tool, field_name=field_name,
                allowed={"type", "name", "description", "tools"},
            )
            _require_text(namespace.get("name"), f"{field_name}.name", max_length=256)
            if "description" in namespace:
                _require_text(namespace["description"], f"{field_name}.description", max_length=16384)
            children = namespace.get("tools")
            if not isinstance(children, list):
                raise ProviderContractError(f"{field_name}.tools must be an array")
            for child_index, child in enumerate(children):
                if not isinstance(child, Mapping) or child.get("type") != "function" or "function" in child:
                    raise ProviderContractError(f"{field_name}.tools[{child_index}] must be a flat function")
                _normalize_tool_schema(child, child_index, api_variant="responses")
        elif kind == "custom":
            custom = _strict_dict(tool, field_name=field_name,
                                  allowed={"type", "name", "description", "format"})
            _require_text(custom.get("name"), f"{field_name}.name", max_length=256)
            if "description" in custom:
                _require_text(custom["description"], f"{field_name}.description", max_length=16384)
            grammar = _strict_dict(custom.get("format"), field_name=f"{field_name}.format",
                                   allowed={"type", "syntax", "definition"})
            if grammar.get("type") != "grammar" or grammar.get("syntax") != "lark":
                raise ProviderContractError(f"{field_name}.format must be a lark grammar")
            _require_text(grammar.get("definition"), f"{field_name}.format.definition", max_length=16384)
        elif kind == "tool_search":
            search = _strict_dict(tool, field_name=field_name,
                                  allowed={"type", "execution", "description", "parameters"})
            if search.get("execution") != "client":
                raise ProviderContractError(f"{field_name}.execution must be client")
            _require_text(search.get("description"), f"{field_name}.description", max_length=16384)
            if not isinstance(search.get("parameters"), Mapping):
                raise ProviderContractError(f"{field_name}.parameters must be an object")
        else:
            hosted = _strict_dict(
                tool, field_name=field_name, allowed={"type", "external_web_access", "search_content_types"},
            )
            if "external_web_access" in hosted and not isinstance(hosted["external_web_access"], bool):
                raise ProviderContractError(f"{field_name}.external_web_access must be boolean")
            if "search_content_types" in hosted:
                kinds = hosted["search_content_types"]
                if not isinstance(kinds, list) or any(kind not in ("text", "image") for kind in kinds):
                    raise ProviderContractError(f"{field_name}.search_content_types must list text or image")
        return json.loads(canonical_json(dict(tool)))
    if "function" in tool:
        wrapped = _strict_dict(
            tool,
            field_name=f"request.tools[{index}]",
            allowed={"type", "function"},
        )
        if wrapped.get("type") not in {None, "function"}:
            raise ProviderContractError(
                f"request.tools[{index}] has unsupported type"
            )
        fn = _strict_dict(
            wrapped.get("function"),
            field_name=f"request.tools[{index}].function",
            allowed={"name", "description", "parameters", "strict"},
        )
    else:
        fn = _strict_dict(
            tool,
            field_name=f"request.tools[{index}]",
            allowed={"name", "description", "parameters", "input_schema", "strict"},
        )
        if "parameters" in fn and "input_schema" in fn:
            raise ProviderContractError(
                f"request.tools[{index}] cannot define both parameters and input_schema"
            )
    name = fn.get("name")
    parameters = fn.get("parameters", fn.get("input_schema", {}))
    if not isinstance(name, str) or not name or not isinstance(parameters, Mapping):
        raise ProviderContractError(
            f"request.tools[{index}] requires name and parameters"
        )
    _require_text(name, f"request.tools[{index}].name", max_length=256)
    result: Dict[str, Any] = {
        "name": name,
        "parameters": json.loads(canonical_json(dict(parameters))),
    }
    strict = fn.get("strict")
    if strict is not None:
        if not isinstance(strict, bool):
            raise ProviderContractError(
                f"request.tools[{index}].strict must be boolean"
            )
        result["strict"] = strict
    description = fn.get("description")
    if description is not None:
        if not isinstance(description, str) or not description:
            raise ProviderContractError(
                f"request.tools[{index}].description must be nonempty"
            )
        result["description"] = _require_text(
            description, f"request.tools[{index}].description", max_length=16384
        )
    return result


def normalize_tool_call_dict(raw: Any, index: int = 0) -> ProviderToolCall:
    raw = _strict_dict(
        raw,
        field_name=f"tool_calls[{index}]",
        allowed={
            "call_id",
            "id",
            "tool_call_id",
            "name",
            "arguments",
            "arguments_json",
            "type",
            "tool_type",
            "function",
        },
    )
    raw_function = raw.get("function")
    if raw_function is not None:
        fn = _strict_dict(
            raw_function,
            field_name=f"tool_calls[{index}].function",
            allowed={"name", "arguments"},
        )
    else:
        fn = {}
    top_name = _coalesce_text_fields(
        raw, ("name",), field_name=f"tool_calls[{index}].name"
    )
    function_name = _coalesce_text_fields(
        fn, ("name",), field_name=f"tool_calls[{index}].function.name"
    )
    if top_name is not None and function_name is not None and top_name != function_name:
        raise ProviderContractError(f"tool_calls[{index}] names disagree")
    top_arguments_json = raw.get("arguments_json")
    function_arguments_json = fn.get("arguments")
    if "arguments_json" in raw and not isinstance(
        top_arguments_json, str
    ):
        raise ProviderContractError(
            f"tool_calls[{index}].arguments_json must be text"
        )
    if "arguments" in fn and not isinstance(
        function_arguments_json, str
    ):
        raise ProviderContractError(
            f"tool_calls[{index}].function.arguments must be text"
        )
    if (
        top_arguments_json is not None
        and function_arguments_json is not None
        and top_arguments_json != function_arguments_json
    ):
        raise ProviderContractError(
            f"tool_calls[{index}] arguments_json aliases disagree"
        )
    if (
        "arguments" not in raw
        and top_arguments_json is None
        and function_arguments_json is None
    ):
        raise ProviderContractError(
            f"tool_calls[{index}] requires explicit arguments"
        )
    if (
        "arguments" in raw
        and raw["arguments"] is None
        and top_arguments_json is None
        and function_arguments_json is None
    ):
        top_arguments_json = "null"
    call = ProviderToolCall(
        id=_coalesce_text_fields(
            raw,
            ("call_id", "id", "tool_call_id"),
            field_name=f"tool_calls[{index}].call_id",
            required=True,
        ),
        name=top_name or function_name,
        arguments=raw.get("arguments"),
        arguments_json=(
            top_arguments_json
            if top_arguments_json is not None
            else function_arguments_json
        ),
        type=_coalesce_text_fields(
            raw,
            ("type", "tool_type"),
            field_name=f"tool_calls[{index}].type",
        )
        or "function",
    )
    if call.name is None:
        raise ProviderContractError(f"tool_calls[{index}].name is required")
    return call


def normalize_tool_result_dict(raw: Any, index: int = 0) -> Dict[str, Any]:
    raw = _strict_dict(
        raw,
        field_name=f"tool_results[{index}]",
        allowed={
            "call_id",
            "tool_call_id",
            "tool_use_id",
            "result",
            "out",
            "error",
        },
    )
    call_id = _coalesce_text_fields(
        raw,
        ("call_id", "tool_call_id", "tool_use_id"),
        field_name=f"tool_results[{index}].call_id",
        required=True,
    )
    semantic_keys = [
        key for key in ("result", "out", "error") if key in raw
    ]
    if len(semantic_keys) != 1:
        raise ProviderContractError(
            "tool result requires exactly one of result, out, or error"
        )
    semantic_key = semantic_keys[0]
    result: Dict[str, Any] = {"call_id": call_id}
    if semantic_key == "error":
        result["error"] = raw[semantic_key]
    else:
        result["result"] = raw[semantic_key]
    canonical_json({key: value for key, value in result.items() if key != "call_id"})
    return result
