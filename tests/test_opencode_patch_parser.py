from types import SimpleNamespace

from agentic_coder_prototype.conductor.patching import apply_patch_operations_direct, normalize_patch_block
from breadboard.opencode_patch import PatchParseError, parse_opencode_patch, apply_update_hunks


def test_parse_update_hunk_accepts_end_of_file_marker() -> None:
    patch = """*** Begin Patch
*** Update File: foo.txt
@@
 line1
-line2
+line2 changed
*** End of File
*** End Patch
"""
    ops = parse_opencode_patch(patch)
    assert len(ops) == 1
    assert ops[0].kind == "update"
    assert ops[0].file_path == "foo.txt"
    assert ops[0].hunks is not None
    assert len(ops[0].hunks) == 1


def test_parse_update_hunk_accepts_move_to() -> None:
    patch = """*** Begin Patch
*** Update File: old/name.txt
*** Move to: renamed/dir/name.txt
@@
 old content
-line2
+line2 changed
*** End Patch
"""
    ops = parse_opencode_patch(patch)
    assert len(ops) == 1
    assert ops[0].kind == "update"
    assert ops[0].file_path == "old/name.txt"
    assert ops[0].move_to == "renamed/dir/name.txt"


def test_apply_update_hunk_ignores_end_of_file_marker_changes() -> None:
    patch = """*** Begin Patch
*** Update File: foo.txt
@@
 line1
-line2
+line2 changed
*** End of File
*** End Patch
"""
    ops = parse_opencode_patch(patch)
    original = "line1\nline2\n"
    updated = apply_update_hunks(original, ops[0].hunks or [])
    assert "line2 changed" in updated


def test_parse_update_hunk_with_only_context_is_allowed() -> None:
    patch = """*** Begin Patch
*** Update File: foo.txt
@@ def f
 line1
*** End of File
*** End Patch
"""
    ops = parse_opencode_patch(patch)
    assert len(ops) == 1
    assert ops[0].hunks is not None
    assert len(ops[0].hunks) == 1


def test_parse_rejects_empty_update_hunk_even_with_end_of_file() -> None:
    patch = """*** Begin Patch
*** Update File: foo.txt
@@
*** End of File
*** End Patch
"""
    try:
        parse_opencode_patch(patch)
    except PatchParseError:
        return
    raise AssertionError("expected PatchParseError")


def test_parse_rejects_no_patch_block() -> None:
    try:
        parse_opencode_patch("not a patch")
    except PatchParseError:
        return
    raise AssertionError("expected PatchParseError")


def test_normalize_patch_block_unwraps_provider_json_input_string() -> None:
    wrapped = (
        '{"input":"*** Begin Patch\\n'
        '*** Add File: hello.txt\\n'
        '+hello\\n'
        '*** End Patch\\n"}'
    )

    normalized = normalize_patch_block(wrapped)

    assert normalized.startswith("*** Begin Patch")
    assert '"input"' not in normalized
    ops = parse_opencode_patch(normalized)
    assert len(ops) == 1
    assert ops[0].file_path == "hello.txt"


def test_normalize_patch_block_treats_raw_blank_hunk_lines_as_context() -> None:
    patch = """*** Begin Patch
*** Update File: src/args.ts
@@
 export function parseArgs(argv: string[]): Args {
   const result: Args = {
     name: 'world',
     shout: false,
   };

   for (let index = 0; index < argv.length; index += 1) {
     const current = argv[index];
-    if (current === '--name' && index + 1 < argv.length) {
-      result.name = argv[index];
+    if (current === '--name' && index + 1 < argv.length) {
+      result.name = argv[index + 1];
+      index += 1;
     } else if (current === '--shout') {
       result.shout = true;
     }
   }

*** End Patch
"""

    normalized = normalize_patch_block(patch)

    assert "\n \n" in normalized
    ops = parse_opencode_patch(normalized)
    assert len(ops) == 1
    assert ops[0].kind == "update"
    assert ops[0].file_path == "src/args.ts"


def test_direct_patch_fallback_reports_context_mismatch(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "args.ts").write_text(
        'export function parseArgs(argv: string[]) { return { name: "world" }; }\n',
        encoding="utf-8",
    )

    class _Remote:
        def __init__(self, fn):
            self._fn = fn

        def remote(self, *args, **kwargs):
            return self._fn(*args, **kwargs)

    def _read_text(path):
        return {"content": open(path, encoding="utf-8").read()}

    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        _ray_get=lambda value: value,
        sandbox=SimpleNamespace(read_text=_Remote(_read_text)),
    )
    patch = """*** Begin Patch
*** Update File: src/args.ts
@@
 export interface Args {
   name: string;
   shout: boolean;
 }
*** End Patch
"""

    result = apply_patch_operations_direct(conductor, patch)

    assert result is not None
    assert result["ok"] is False
    assert "context not found" in result["stderr"]
    assert "src/args.ts" in result["stderr"]
