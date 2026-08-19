# Contributing

## Deletion policy

Breadboard deletion work follows the WS-H rule: static zero-importer evidence is
a starting point, not deletion proof. Before removing or quarantining a Python
module:

- Confirm that a current deletion audit or narrow scan has no static inbound
  imports for the exact module path.
- Run `scripts/check_deleted_path_references.py` with the repository-relative
  path being deleted. Live references in docs, tests, scripts, workflows,
  dynamic imports, and package entry points block deletion.
- Run the owner's smoke test or package import smoke.
- Quarantine an ambiguous module under its owning legacy area for one tranche.

The deleted-path audit recognizes historical commands only in digest-bound
records listed by an applicable `--immutable-snapshot-manifest`. Snapshot
manifests may authorize individual files under
`docs/conformance/evidence_snapshots/`. The deletion-audit source separately
names the one external historical record it accepts; its exact path and digest
are stored in `config/deletion_audit/immutable_historical_records.v1.json`.
Other `docs_tmp` files cannot be exempted. Every manifest must be tracked, list
an individual file, and match that file's SHA-256 digest. A directory name, an
unlisted file, or a digest mismatch does not exempt a reference. Do not rewrite
historical records to make a deletion audit pass.

For example:

```bash
python scripts/check_deleted_path_references.py \
  --deleted-path scripts/example/removed_builder.py \
  --immutable-snapshot-manifest docs/conformance/evidence_snapshots/phase_20/MANIFEST.json \
  --immutable-snapshot-manifest docs/conformance/evidence_snapshots/phase16_er_progress/snapshot_manifest.json \
  --immutable-snapshot-manifest config/deletion_audit/immutable_historical_records.v1.json
```

The command exits nonzero for every live reference. Record the blocker instead
of relying on restoration from Git.

Run the separate advisory zero-importer check when adding or moving Python
modules:

```bash
python scripts/check_zero_importer_advisory.py --json
```

This advisory remains non-blocking. A new zero-importer module needs an explicit
owner, entry point, test/import smoke, or deletion-audit coverage before a later
WS-H pass treats it as safe to prune.
