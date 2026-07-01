# Contributing

## Deletion policy

Breadboard deletion work follows the WS-H rule: static zero-importer evidence is
a starting point, not deletion proof. Do not remove or quarantine a Python module
until all of the following are true:

- The current deletion audit or a fresh narrow scan shows no static inbound
  imports for the exact module path.
- Built-in `grep`/`ast_grep` checks show no unresolved string references,
  dynamic import references, docs references, tests, scripts, workflow entries,
  or package entry points for the module path, dotted module name, and stem.
- Any owner-specific smoke test or import smoke for the package still passes.
- Ambiguous modules are quarantined under the owning legacy area for one tranche
  instead of deleted.

If a target still has dynamic/string/docs/test references, leave it in place and
record the blocker evidence. Deletion waves must not rely on restoring from git as
the safety mechanism.

Run the advisory zero-importer check when adding or moving Python modules:

```bash
python scripts/check_zero_importer_advisory.py --json
```

The check is non-blocking by design. A new zero-importer module means the module
needs an explicit owner, an entry point, a test/import smoke, or deletion-audit
coverage before a later WS-H pass treats it as safe to prune.
