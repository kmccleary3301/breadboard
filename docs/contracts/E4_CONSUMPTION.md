# E4 consumption guide

This guide shows the read paths that landed after the accepted E4 packet. Run commands from the repository checkout root.

## Python: compile and finalize records

Use compilers for domain records. Use `finalize_record` at the boundary when a builder assembles a raw primitive payload.

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
import json
from pathlib import Path

from agentic_coder_prototype.compilation.effective_config_graph import compile_effective_config_graph
from agentic_coder_prototype.compilation.primitive_records import finalize_record, get_spec

example = json.loads(Path('contracts/kernel/examples/capability_registry_minimal.json').read_text())
example.pop('schema_version')
registry = finalize_record(get_spec('bb.capability_registry.v1'), example)
assert registry['schema_version'] == 'bb.capability_registry.v1'

graph = compile_effective_config_graph(
    graph_id='doc_effective_config_graph',
    layers=[
        {
            'layer_id': 'defaults',
            'source_kind': 'default',
            'scope': 'doc',
            'precedence': 0,
            'source_ref': 'doc://defaults',
            'values': {'model': {'name': 'default-model'}, 'sandbox': {'mode': 'read-only'}},
        },
        {
            'layer_id': 'project',
            'source_kind': 'project',
            'scope': 'doc',
            'precedence': 10,
            'source_ref': 'doc://project',
            'values': {'model': {'name': 'project-model'}},
        },
    ],
    merge_policy={'policy_id': 'doc', 'strategy': 'deep-merge', 'conflict_resolution': 'highest-precedence'},
    migrations=[{'migration_id': 'doc', 'from_version': 'doc', 'to_version': 'bb.effective_config_graph.v1', 'applied': True}],
)
assert graph['schema_version'] == 'bb.effective_config_graph.v1'
assert any(item['path'] == 'model.name' and item['value'] == 'project-model' for item in graph['effective_values'])
print(registry['schema_version'], graph['schema_version'], graph['graph_hash'])
PY
```

## HTTP: read the accepted E4 state

The FastAPI app exposes E4 under `/v1/e4`. The block starts a local server, uses `curl`, then stops the server.

```bash
PYTHONPATH=. .venv/bin/python -m uvicorn agentic_coder_prototype.api.cli_bridge.app:create_app --factory --host 127.0.0.1 --port 18764 >/tmp/e4-consumption-api.log 2>&1 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
for attempt in 1 2 3 4 5; do
  curl -fsS http://127.0.0.1:18764/v1/e4/health >/tmp/e4-doc-health.json 2>/dev/null && break
  sleep 1
done
curl -fsS 'http://127.0.0.1:18764/v1/e4/lanes?status=accepted' | .venv/bin/python -m json.tool >/tmp/e4-doc-lanes.json
curl -fsS 'http://127.0.0.1:18764/v1/e4/claims?accepted=true' | .venv/bin/python -m json.tool >/tmp/e4-doc-claims.json
kill "$server_pid"
```

## TypeScript: generated types and validators

Generated bindings live under `@breadboard/kernel-contracts/generated` after `npm run generate`.

```bash
cd sdk/ts-kernel-contracts && npm run -s generate && npm run -s typecheck
```

A consumer can pair a generated validator with a generated type. This example uses the package source tree directly so it works before publishing.

```bash
cd sdk/ts-kernel-contracts && npm test
```

## Add a lane

New lanes start as scaffolded drafts under `docs_tmp/phase_15/lane_scaffolds`. The draft output is not accepted evidence; it gives the builder, comparator, inventory row, and command shape that the D-workstream promotes after validation.

```bash
.venv/bin/python scripts/e4_parity/scaffold_e4_target_lane.py \
  --lane-id sample_doc_lane \
  --config-id sample_doc_config \
  --target-family oh_my_pi \
  --target-version oh_my_pi_pi_coding_agent_example \
  --provider-model no-provider \
  --sandbox-mode read-only \
  --run-id sample_doc_run \
  --emit-inventory-row \
  --emit-builder-skeleton \
  --emit-comparator-skeleton \
  --dry-run-json >/tmp/e4-doc-scaffold.json
```

Promotion rule: keep the scaffold under `docs_tmp` until the capture, replay, comparator, support claim, evidence manifest, node gate, catalog entry, and score row all validate. Accepted evidence stays hash-addressed through the catalog and support claim chain.
