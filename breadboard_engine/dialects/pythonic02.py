from __future__ import annotations

import ast
import html
import json
import re
from typing import Dict, List

from ..core.core import BaseToolDialect, ToolDefinition, ToolCallParsed


class Pythonic02Dialect(BaseToolDialect):
    type_id = "python"

    function_call_description_template = (
        "\nYou may call a python functions to execute an action.\n"
        "To do so, you must wrap it in the following template:\n\n"
        "<TOOL_CALL> function_name(arg_1=value1, arg2=value2, ...) </TOOL_CALL>\n\n"
        "and it is wrapped as <TOOL_CALL> ... </TOOL_CALL>.\n"
        "The call MUST begin with the sequence \"<TOOL_CALL>\" and MUST end with the sequence \"</TOOL_CALL>\" to be valid.\n"
        "The inner content must be valid python code.\n\n"
        "Here are your available functions:\n\n{available_functions}\n\n"
        "Syntax: strictly use parentheses with comma-separated arguments and equal signs for keyword args.\n"
        "Example: my_tool(arg1=123, arg2=\"text\"). Do NOT use colons.\n"
    )

    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:
        functions_available_strings: list[str] = []
        for func in tools:
            arg_lines: list[str] = []
            for f_arg in func.parameters:
                desc = (f_arg.description or "").replace("\n", " ")
                parts = [f_arg.name]
                if f_arg.type:
                    parts.append(f": {f_arg.type}")
                if f_arg.default is not None:
                    parts.append(f" = {f_arg.default}")
                if desc:
                    parts.append(f"\t # {desc}")
                arg_lines.append("".join(parts))
            args_str = ",\n\t".join(arg_lines)
            functions_available_strings.append(
                f"def {func.name}(\t\t{args_str}\n)\n\"\"\"\n{func.description}\n\"\"\""
            )
        available = "\n\n".join(functions_available_strings)
        return self.function_call_description_template.format(available_functions=available)

    @staticmethod
    def _normalize_tool_name(name: str, tools_lookup: Dict[str, ToolDefinition]) -> str | None:
        if name in tools_lookup:
            return name
        cleaned = re.sub(r"[^a-z0-9]+", "", name.lower())
        alias_map = {
            "opencodeaddfile": "Write",
            "generalbashcommands": "Bash",
            "aidersearchreplace": "apply_search_replace",
            "unifieddiffgitlike": "apply_unified_patch",
            "bashcommands": "Bash",
            "opencode": "Write",
        }
        mapped = alias_map.get(cleaned)
        if mapped and mapped in tools_lookup:
            return mapped
        for candidate in tools_lookup:
            if re.sub(r"[^a-z0-9]+", "", candidate.lower()) == cleaned:
                return candidate
        return None

    def parse_calls(self, input_str: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:
        pattern = re.compile(r"<TOOL_CALL>(.*?)</TOOL_CALL>", re.DOTALL | re.IGNORECASE)
        xml_tool_pattern = re.compile(
            r"<tool_call(?P<attrs>\s[^>]*)?>(?P<body>.*?)</tool_call>",
            re.DOTALL | re.IGNORECASE,
        )
        xml_param_pattern = re.compile(r"<parameter\b([^>]*)>(.*?)</parameter>", re.DOTALL | re.IGNORECASE)
        tools_lookup = {tool.name: tool for tool in tools}
        function_calls: list[ToolCallParsed] = []
        seen_calls: set[tuple[str, str]] = set()

        def _extract_json_object(source: str, start: int) -> str | None:
            if start < 0 or start >= len(source) or source[start] != "{":
                return None
            depth = 0
            in_string = False
            escape = False
            for idx in range(start, len(source)):
                ch = source[idx]
                if in_string:
                    if escape:
                        escape = False
                        continue
                    if ch == "\\":
                        escape = True
                        continue
                    if ch == "\"":
                        in_string = False
                    continue
                if ch == "\"":
                    in_string = True
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return source[start : idx + 1]
            return None

        def _extract_dotted_name(func_node: ast.AST) -> str | None:
            if isinstance(func_node, ast.Name):
                return func_node.id
            if isinstance(func_node, ast.Attribute):
                base = _extract_dotted_name(func_node.value)
                if not base:
                    return None
                return f"{base}.{func_node.attr}"
            return None

        def _scan_delimiters(src: str) -> tuple[bool, int, int, int | None]:
            in_string = False
            quote = ""
            escape = False
            paren_depth = 0
            brace_depth = 0
            saw_paren = False
            end_of_first_call: int | None = None
            for idx, ch in enumerate(src):
                if in_string:
                    if escape:
                        escape = False
                        continue
                    if ch == "\\":
                        escape = True
                        continue
                    if ch == quote:
                        in_string = False
                        quote = ""
                    continue

                if ch in ("\"", "'"):
                    in_string = True
                    quote = ch
                    continue

                if ch == "(":
                    paren_depth += 1
                    saw_paren = True
                    continue
                if ch == ")":
                    if saw_paren:
                        paren_depth -= 1
                        if paren_depth == 0 and end_of_first_call is None:
                            end_of_first_call = idx + 1
                    continue
                if ch == "{":
                    brace_depth += 1
                    continue
                if ch == "}":
                    brace_depth -= 1
                    continue

            return in_string, paren_depth, brace_depth, end_of_first_call

        def _repair_python_call(src: str) -> list[str]:
            candidates: list[str] = []
            stripped = src.strip()
            if not stripped:
                return candidates

            in_string, paren_depth, brace_depth, end_idx = _scan_delimiters(stripped)
            if in_string:
                return candidates

            # If we already have a full call but there's trailing junk, trim it.
            if end_idx is not None and end_idx < len(stripped):
                candidates.append(stripped[:end_idx].strip())

            if paren_depth > 0:
                # Some providers emit tool calls missing the closing ")" (often with a stray "}" suffix).
                candidates.append(stripped + (")" * paren_depth))

                trimmed = stripped.rstrip()
                if trimmed.endswith("}"):
                    trimmed = trimmed.rstrip("}").rstrip()
                in_string2, paren_depth2, _brace_depth2, _end_idx2 = _scan_delimiters(trimmed)
                if not in_string2 and paren_depth2 > 0:
                    candidates.append(trimmed + (")" * paren_depth2))

                # If braces are unbalanced (more '}' than '{'), attempt trimming a few trailing '}'.
                if brace_depth < 0 and stripped.endswith("}"):
                    trim_more = stripped.rstrip()
                    while trim_more.endswith("}") and _scan_delimiters(trim_more)[2] < 0:
                        trim_more = trim_more[:-1].rstrip()
                    in_string3, paren_depth3, _brace_depth3, _end_idx3 = _scan_delimiters(trim_more)
                    if not in_string3 and paren_depth3 > 0:
                        candidates.append(trim_more + (")" * paren_depth3))

            # De-dup while preserving order
            unique: list[str] = []
            seen: set[str] = set()
            for cand in candidates:
                if cand and cand not in seen and cand != stripped:
                    seen.add(cand)
                    unique.append(cand)
            return unique

        def add_call(func_name: str, arguments: dict) -> None:
            try:
                signature = json.dumps(arguments, sort_keys=True, default=str)
            except TypeError:
                signature = str(arguments)
            key = (func_name, signature)
            if key in seen_calls:
                return
            seen_calls.add(key)
            function_calls.append(ToolCallParsed(function=func_name, arguments=arguments))

        # Parse XML tool_call blocks (<tool_call name="..."><parameter ...>...</parameter></tool_call>)
        for match in xml_tool_pattern.finditer(input_str or ""):
            attrs = match.group(1) or ""
            body = match.group(2) or ""
            name_match = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", attrs, re.IGNORECASE)
            if not name_match:
                continue
            raw_name = name_match.group(1)
            resolved_name = self._normalize_tool_name(raw_name, tools_lookup) or raw_name
            args: Dict[str, Any] = {}
            for param_match in xml_param_pattern.finditer(body):
                param_attrs = param_match.group(1) or ""
                param_body = param_match.group(2) or ""
                pname_match = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", param_attrs, re.IGNORECASE)
                if not pname_match:
                    continue
                pname = pname_match.group(1)
                raw_val = html.unescape(param_body).strip()
                if raw_val == "":
                    val: Any = ""
                else:
                    try:
                        val = json.loads(raw_val)
                    except Exception:
                        val = raw_val
                args[pname] = val
            add_call(resolved_name, args)

        def _try_parse_json_block(raw: str) -> tuple[str | None, dict | None]:
            stripped = raw.strip()
            tag_match = re.match(r"^<(?P<tag>[A-Za-z0-9_.-]+)>(?P<body>[\s\S]*)</(?P=tag)>$", stripped)
            tag_name = None
            json_payload = None
            if tag_match:
                tag_name = tag_match.group("tag")
                inner = tag_match.group("body").strip()
                try:
                    json_payload = json.loads(inner)
                except Exception:
                    json_payload = None
            if json_payload is None and stripped.startswith("<"):
                start = stripped.find(">")
                if start != -1:
                    tag_segment = stripped[1:start].strip()
                    body_segment = stripped[start + 1 :]
                    closing_idx = body_segment.rfind("</")
                    if closing_idx != -1:
                        inner = body_segment[:closing_idx].strip()
                        try:
                            json_payload = json.loads(inner)
                            tag_name = tag_segment
                        except Exception:
                            json_payload = None
            if json_payload is None:
                try:
                    json_payload = json.loads(stripped)
                except Exception:
                    json_payload = None
            if isinstance(json_payload, dict):
                func_name = (
                    json_payload.get("function")
                    or json_payload.get("name")
                    or json_payload.get("tool")
                    or tag_name
                )
                params = (
                    json_payload.get("parameters")
                    or json_payload.get("args")
                    or json_payload.get("arguments")
                )
                if params is None and tag_name and func_name == tag_name:
                    reserved = {"function", "name", "tool", "parameters", "args", "arguments"}
                    params = {k: v for k, v in json_payload.items() if k not in reserved}
                if isinstance(func_name, str) and isinstance(params, dict):
                    return func_name, params
            return None, None

        for match in pattern.finditer(input_str):
            call_str = match.group(1).strip()

            json_func, json_args = _try_parse_json_block(call_str)
            if json_func and json_func in tools_lookup and json_args is not None:
                add_call(json_func, json_args)
                continue
            elif json_func and json_args is not None:
                resolved = self._normalize_tool_name(json_func, tools_lookup)
                if resolved:
                    add_call(resolved, json_args)
                    continue

            # Support OpenCode-style: <TOOL_CALL>patch</TOOL_CALL>\n{...json args...}
            bare = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", call_str)
            if bare:
                resolved = self._normalize_tool_name(call_str, tools_lookup)
                if resolved:
                    idx = match.end()
                    while idx < len(input_str) and input_str[idx].isspace():
                        idx += 1
                    if idx < len(input_str) and input_str[idx] == "{":
                        blob = _extract_json_object(input_str, idx)
                        if blob:
                            parsed = None
                            try:
                                parsed = json.loads(blob)
                            except Exception:
                                try:
                                    repaired_json = blob.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
                                    parsed = json.loads(repaired_json)
                                except Exception:
                                    parsed = None
                            if isinstance(parsed, dict):
                                add_call(resolved, parsed)
                                continue

            def _parse_python_call(src: str) -> bool:
                tree = ast.parse(src, mode="exec")
                if not tree.body or not isinstance(tree.body[0], ast.Expr) or not isinstance(tree.body[0].value, ast.Call):
                    return False
                call_node = tree.body[0].value
                function_name = _extract_dotted_name(call_node.func)
                if not function_name:
                    return False
                resolved = self._normalize_tool_name(function_name, tools_lookup)
                if not resolved:
                    return False
                tool_def = tools_lookup[resolved]
                arguments: dict[str, object] = {}
                for i, arg_node in enumerate(call_node.args):
                    if i < len(tool_def.parameters):
                        param_name = tool_def.parameters[i].name
                        arguments[param_name] = ast.literal_eval(arg_node)
                for keyword in call_node.keywords:
                    if not keyword.arg:
                        continue
                    arguments[keyword.arg] = ast.literal_eval(keyword.value)
                add_call(resolved, arguments)
                return True

            try:
                if _parse_python_call(call_str):
                    continue
            except (SyntaxError, ValueError):
                pass

            for repaired_call in _repair_python_call(call_str):
                try:
                    if _parse_python_call(repaired_call):
                        break
                except Exception:
                    continue
            else:
                repaired_call = None
            if repaired_call is not None and function_calls:
                # _parse_python_call succeeded and already added the call.
                continue

            # Best-effort rescue for tool calls where providers decode "\n" into literal
            # newlines inside quoted string arguments (invalid python source).
            repaired = call_str
            if "\n" in repaired or "\r" in repaired:
                repaired = repaired.replace("\r\n", "\n").replace("\r", "\n")
                repaired = repaired.replace("\n", "\\n")
                try:
                    if _parse_python_call(repaired):
                        continue
                except Exception:
                    pass

                for repaired_call in _repair_python_call(repaired):
                    try:
                        if _parse_python_call(repaired_call):
                            break
                    except Exception:
                        continue
                else:
                    repaired_call = None
                if repaired_call is not None and function_calls:
                    continue

            try:
                m = re.match(r"^(?P<fname>[a-zA-Z_]\w*)\((?P<args>[\s\S]*)\)$", call_str)
                if not m:
                    continue
                fname = m.group("fname")
                resolved = self._normalize_tool_name(fname, tools_lookup)
                if not resolved:
                    continue
                args_src = m.group("args")
                fixed_args = re.sub(r"(\b[a-zA-Z_]\w*\s*):", r"\1=", args_src)
                fixed_call = f"{fname}({fixed_args})"
                tree = ast.parse(fixed_call, mode="exec")
                call_node = tree.body[0].value  # type: ignore[index]
                if isinstance(call_node, ast.Call) and isinstance(call_node.func, ast.Name):
                    tool_def = tools_lookup[resolved]
                    arguments: dict[str, object] = {}
                    for i, arg_node in enumerate(call_node.args):
                        if i < len(tool_def.parameters):
                            param_name = tool_def.parameters[i].name
                            arguments[param_name] = ast.literal_eval(arg_node)
                    for keyword in call_node.keywords:
                        if keyword.arg is None:
                            continue
                        arguments[keyword.arg] = ast.literal_eval(keyword.value)
                    add_call(resolved, arguments)
            except Exception:
                continue

        generic_pattern = re.compile(
            r"<(?P<tag>[A-Za-z0-9_.-]+)(?:[^>]*)>\s*(?P<body>{[\s\S]*?})\s*</(?P=tag)>",
            re.IGNORECASE,
        )
        for match in generic_pattern.finditer(input_str):
            tag = match.group("tag")
            if tag.lower() in {"tool_call", "bash"}:
                continue
            body = match.group("body").strip()
            json_func, json_args = _try_parse_json_block(match.group(0))
            if json_func and json_args is not None:
                resolved = self._normalize_tool_name(json_func, tools_lookup)
                if resolved:
                    add_call(resolved, json_args)
                    continue
            # Fallback: treat tag itself as function with parsed JSON body
            resolved = self._normalize_tool_name(tag, tools_lookup)
            if resolved:
                try:
                    parsed_body = json.loads(body)
                except Exception:
                    continue
                if isinstance(parsed_body, dict):
                    add_call(resolved, parsed_body)

        return function_calls
