# E4 capture-a-harness cookbook v2

This guide covers the current path from a working harness configuration to an E4 lane backed by a lane-def v2 capture adapter. Scratch capture comes first. Accepted-root regeneration goes through `scripts/e4_parity/regen.py`.

## What the harness config proves

`agent_configs/templates/minimal_harness.v2.yaml` is a useful starting point for a small harness configuration. A config that validates and boots proves that the harness can load it. It does not define an E4 capture by itself.

An E4 lane also needs a capture implementation that can turn declared source material into the governed artifact roles for that lane. Use an active `capture_adapter` from `contracts/kernel/registries/e4_adapters.v1.json` only when its documented inputs and output packet match the target. If none does, add and register an adapter before authoring the lane. Arbitrary target capture is not config-only.

For a lane that captures the minimal harness, list the copied config, its prompt file, and any target capture records among `capture.inputs` only when the selected adapter consumes those files. The adapter still has to emit the complete governed packet; pointing a lane at the config is not a capture implementation.

## Author the lane definition

Begin with the scaffold front door, `scripts/e4_parity/scaffold_e4_target_lane.py`. Its default output stays under `../docs_tmp/phase_15/lane_scaffolds`:

```bash
.venv/bin/python scripts/e4_parity/scaffold_e4_target_lane.py \
  --lane-id my_target_feature_lane \
  --config-id my_target_feature_lane_v1 \
  --target-family my_target \
  --target-version 1.0.0 \
  --emit-lane-def
```

Review the draft, replace its scaffold-only values with target-observed inputs and assertions, then copy the lane definition to `config/e4_lanes/<lane_id>.yaml`. It must use `schema_version: bb.e4.lane_def.v2`. Keep `status: scaffolded` and `points: 0` while the lane is being assembled.

The lane definition must state:

- stable `lane_id`, versioned `config_id`, `target_family`, and `target_version`;
- `capture.strategy: adapter`, an active `capture.adapter`, and every real source path under `capture.inputs`;
- a registered `normalize.translator` and the adapter-specific `normalize.config`;
- a registered `compare.comparator` with typed, uniquely named assertions;
- exact behaviors, surfaces, and exclusions under `claim`;
- `artifacts_root` and the C4 `reverify_command`;
- `run`, `provenance`, and `acceptance` when the lane reaches `claimed` or `accepted` state.

Treat the registry entry as an executable contract. Its `metadata.impl` names the callable used by `run_lane.py`, and its `metadata.config_keys` describes the data the adapter consumes. Existing adapter-specific lane definitions are the reference for fields such as record builders, role paths, frozen source archives, and semantic assertions.

The canonical inventory row in `docs/conformance/e4_lane_inventory.json` must agree with the lane definition before the lane validator can pass. The validator also checks that `artifacts_root` exists, the support claim named by `reverify_command` exists and has matching scope anchors, and the reverify command names a JSON output. These are promotion bindings, not substitutes for a scratch capture.

## Validate the definition

`validate_lane.py` accepts either a lane ID or a lane-definition path. During authoring, use the path so the report identifies the exact draft under review:

```bash
.venv/bin/python scripts/authoring/validate_lane.py \
  --lane config/e4_lanes/my_target_feature_lane.yaml \
  --json ../docs_tmp/phase_19/scratch/my_target_feature_lane/lane_validation.json
```

`--json` takes an output path for this CLI. Exit code 0 means every required check passed; exit code 1 means at least one check failed. Review the named checks rather than treating schema validity alone as promotion readiness. A new draft remains red until its canonical inventory and governed claim bindings are present.

Run the same validator by lane ID after those bindings are in place:

```bash
.venv/bin/python scripts/authoring/validate_lane.py \
  --lane my_target_feature_lane \
  --json ../docs_tmp/phase_19/scratch/my_target_feature_lane/promotion_validation.json
```

## Capture into scratch

`run_lane.py --lane` resolves a lane ID from `config/e4_lanes/`; it does not accept a lane-definition path. Its capture stage loads the registered adapter and passes the lane definition, the matching inventory row, the scratch output root, and `promote_accepted=False`.

```bash
.venv/bin/python scripts/e4_parity/run_lane.py \
  --lane my_target_feature_lane \
  --stage capture \
  --out ../docs_tmp/phase_19/scratch/my_target_feature_lane/out \
  --json
```

For `run_lane.py`, `--out` is the scratch artifact root and `--json` prints the result to stdout. The adapter must declare scratch-output support; the runner refuses a scratch adapter that does not. A scratch capture must write only below `--out` and must not refresh accepted bindings.

Review the returned packet report and every produced artifact role. For a migrated accepted lane, hash or byte-diff each scratch role against the corresponding accepted role. Resolve every difference before crossing the promotion boundary. For a genuinely new lane, review the exact proposed accepted-root delta, schema validation, secret scan, comparator result, support scope, and C4 bindings.

## Cross the promotion boundary

Promotion starts only when all of the following are true:

1. The scratch capture completed successfully and wrote no accepted path.
2. Every required artifact role is present; migrated roles are byte-identical unless an accepted change explicitly authorizes the listed delta.
3. The lane definition, adapter registry entry, canonical inventory row, claim scope, and reverify command agree.
4. `validate_lane.py` reports `ok: true` for the lane ID.
5. The reviewed lane state, points, support claim, evidence manifest, CT row, and score binding describe the same scope.

The regeneration DAG owns accepted-root promotion. It invokes lane capture with promotion enabled and defers shared binding refresh until the correct DAG stage. Reserve direct `run_lane.py --promote-accepted` use for that orchestrated path; an ordinary scratch run always supplies `--out` and omits promotion flags.

The DAG runs implementation stages such as `generate_lane_inventory.py` and `validate_e4_c4_chain.py`; these scripts are not alternate front doors. Use `evidence_roots.py` to lint scratch-root references before promotion. Support claims are generator-owned. Do not overwrite support claims by hand; regenerate them through `scripts/e4_parity/regen.py`.

Inspect the regeneration plan before writing accepted roots:

```bash
.venv/bin/python scripts/e4_parity/regen.py explain \
  --json ../docs_tmp/phase_19/scratch/my_target_feature_lane/regen_plan.json
```

Run accepted regeneration through the single front door:

```bash
.venv/bin/python scripts/e4_parity/regen.py run \
  --json ../docs_tmp/phase_19/scratch/my_target_feature_lane/regen_run.json
```

Then prove the governed watch set reaches a byte-identical fixed point:

```bash
.venv/bin/python scripts/e4_parity/regen.py fixed-point \
  --json ../docs_tmp/phase_19/scratch/my_target_feature_lane/fixed_point.json
```

If regeneration fails, classify the captured run log before changing data or code:

```bash
.venv/bin/python scripts/e4_parity/regen.py classify \
  --log ../docs_tmp/phase_19/scratch/my_target_feature_lane/regen_run.json \
  --json ../docs_tmp/phase_19/scratch/my_target_feature_lane/regen_failure.json
```

Use the classification to distinguish a stale pin from a semantic failure. Refresh only through `regen.py`; do not call its implementation module or individual regeneration scripts as alternate front doors.
