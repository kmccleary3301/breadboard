<!-- ASSISTANT -->
*** Begin Patch
*** Add File: README.md
@@
+# Dry Run Workspace
+This workspace is created by the dry-run harness.
@@
*** End Patch
<!-- END -->

<!-- ASSISTANT -->
*** Begin Patch
*** Add File: notes/hello.txt
@@
+hello world
@@
*** End Patch
<!-- END -->

<!-- ASSISTANT -->
<TOOL_CALL> list_dir(path=".", depth=1) </TOOL_CALL>
<!-- END -->

<!-- ASSISTANT -->
<TOOL_CALL> read_file(path="notes/hello.txt") </TOOL_CALL>
<!-- END -->

<!-- ASSISTANT -->
<TOOL_CALL> mark_task_complete() </TOOL_CALL>
<!-- END -->


