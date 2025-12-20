<!-- ASSISTANT -->
*** Begin Patch
*** Add File: bad.py
@@
+def bad_func(:
+    pass
+# SYNTAX_ERROR
@@
*** End Patch
<!-- END -->

<!-- ASSISTANT -->
```patch
diff --git a/bad.py b/bad.py
index e69de29..0000000 100644
--- a/bad.py
+++ b/bad.py
@@ -1,3 +1,5 @@
 def bad_func(:
     pass
 # SYNTAX_ERROR
+def another_bad(
+    print("oops")
```
<!-- END -->

<!-- ASSISTANT -->
bad.py
<<<<<<< SEARCH
pass
=======
pass SYNTAX_ERROR
>>>>>>> REPLACE
<!-- END -->

<!-- ASSISTANT -->
<TOOL_CALL> mark_task_complete() </TOOL_CALL>
<!-- END -->


