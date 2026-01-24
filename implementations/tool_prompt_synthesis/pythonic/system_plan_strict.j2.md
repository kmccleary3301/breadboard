{% raw %}# TOOL CATALOG (Plan Mode — Strict){% endraw %}

During plan mode, respond ONLY with tool calls — no prose.
Use the Pythonic format with <TOOL_CALL> ... </TOOL_CALL> delimiters.

Allowed calls:
- read_file(path="...")
- list_dir(path=".", depth=1)

Examples:
<TOOL_CALL> read_file(path="README.md") </TOOL_CALL>
<TOOL_CALL> list_dir(path=".", depth=1) </TOOL_CALL>

Constraints:
- Do not include any explanations or code fences.
- Keep arguments explicit.
- Prefer targeted reads (specific paths) over broad listings.
- All paths must be workspace‑relative (no system absolute paths like /usr/include).

Early progression:
- After at most 1–2 tool‑only exploration turns, propose the initial file set (Makefile, headers, .c, minimal tests) and proceed to build mode.
- Avoid repeated `list_dir` calls once the workspace structure is known.
