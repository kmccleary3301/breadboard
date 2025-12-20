**How to propose edits (Unified Diff)**

- Generate a valid git unified diff. For new files, use `--- /dev/null` and `+++ b/<path>`.
- Keep hunks small and logically grouped.

Available helpers:
{% for t in tools %}- {{ t.display_name or t.name }} — {{ t.description }}
{% endfor %}



