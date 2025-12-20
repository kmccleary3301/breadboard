from tool_calling.workspace_tracker import WorkspaceStateTracker


def test_workspace_tracker_tracks_create_read_modify(tmp_path):
    ws = WorkspaceStateTracker(str(tmp_path))

    # Simulate create_file call
    create_call = {"function": "create_file", "arguments": {"path": "foo.txt"}}
    ws.track_operation(create_call, {"status": "ok"})
    ctx = ws.get_context_for_prompt()
    assert "foo.txt" in ctx["files_created_this_session"]

    # Simulate read_file call
    read_call = {"function": "read_file", "arguments": {"path": "foo.txt"}}
    ws.track_operation(read_call, {"content": ""})
    ctx = ws.get_context_for_prompt()
    assert "foo.txt" in ctx["files_read_this_session"]

    # Simulate unified patch modifying foo.txt
    patch_text = """--- a/foo.txt
+++ b/foo.txt
@@ -1,1 +1,1 @@
-old
+new
"""
    patch_call = {"function": "apply_unified_patch", "arguments": {"patch": patch_text}}
    ws.track_operation(patch_call, {"status": "applied"})
    ctx = ws.get_context_for_prompt()
    assert "foo.txt" in ctx["files_modified_this_session"]


