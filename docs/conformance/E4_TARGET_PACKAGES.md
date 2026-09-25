# E4 target packages

BreadBoard publishes six current E4 family entries at the top level of
`agent_configs/`. The `2026-09-10` filename date records the catalog refresh, not the
upstream capture date. Exact source versions and claim limits remain explicit in each
file and in `agent_configs/README.md`.

## Current sources

| Family | Accepted source | Package or source asset |
|---|---|---|
| Codex CLI | 0.139.0 with `gpt-5.5`; read-only capture probe | `config/e4_targets/codex/0.139.0/` |
| Claude Code | 2.1.63 static-package/replay capture | `config/e4_targets/claude_code/2.1.63/` |
| OpenCode | 1.2.17 static-package/replay capture | `config/e4_targets/opencode/1.2.17/` |
| oh-my-opencode | 3.10.0, commit `5137df72d8fab3fec609c82f91387db8e3b13825` | `config/e4_targets/oh_my_opencode/3.10.0/` |
| Oh My Pi | `@oh-my-pi/pi-coding-agent@16.2.13`, commit `5356713eae60e67ee64d9b02e3b5e377d248ee7f` | `config/e4_targets/oh_my_pi/16.2.13/` |
| Pi | `@mariozechner/pi-coding-agent@0.57.1` | `config/e4_targets/pi/0.57.1/` |

The Codex labels identify different evidence scopes. The 0.107.0 directory is the
historical GPT-5.1 package snapshot; 0.110.0 collaboration fixtures are historical
replay evidence; 0.139.0 with GPT-5.5 is the accepted read-only capture lane. None may
be relabeled as another.

## Package forms

Oh My Pi and Pi are installed target packages. Their `target.json` files use
`bb.e4.target.v1`; their `harness.yaml` files use `bb.e4.target_config.v1`. Load them
through `breadboard_engine.e4_targets.load_e4_target`. Their top-level catalog files
are readable target-config projections, not BreadBoard agent-config CLI inputs.

Codex, Claude Code, OpenCode, and oh-my-opencode use standalone BreadBoard agent
configs backed by tracked prompt/reference packages. The Codex 0.139.0 package
intentionally contains only the exact prompt source needed by the accepted probe; it
does not fabricate an installed-target descriptor or a broad parity profile.

Mini SWE Agent 2.4.6 is an additional source-backed target at
`config/e4_targets/mini_swe_agent/2.4.6/`, pinned to
`a83fcae82d2a08f0ee0c688f9d137b3566c097f8`. It uses target/config v2, an empty
caller input object, native `bash`, the `DefaultAgent` loop and an explicit
non-streaming Chat profile. Its renderer and native response consumer are
`breadboard.mini-swe-agent.v2.4.6`. Native shell execution requires the admitted
Python tool closure; pricing requires the installed LiteLLM 1.101.0 catalog with
`LITELLM_LOCAL_MODEL_COST_MAP=True` before import. Package/compiler support is not
whole-episode qualification, official grading or a full-profile parity claim.

Hermes Agent 2026.9.11 is pinned to source commit
`939e45c91d751fadd94dcd1b873ac3cb44846213` under
`config/e4_targets/hermes_agent/2026.9.11/`. Its replay trace records each raw
model-emitted tool call, including invalid names and duplicate samples; only
native-prepared valid actions can dispatch. The Hermes comparator derives the
supplier workspace from the source-declared system-prompt cwd/workspace root
or a consistent recorded `runtime.cwd`, and uses the candidate's recorded
`runtime.cwd`. Missing or conflicting declarations fail comparison. This
projection does not establish rerun3 parity or authorize rewriting prompt text.

## Compiler ownership and admission

`breadboard.product.harness.resolution.compile_e4_harness` connects a verified
`E4TargetPackage` to the product Harness Lock, published configuration artifacts,
sealed bundle/closure and server compiler. It takes the target inputs, runtime
configuration, `FilesystemCAS` and `CompileOptions`. Runtime configuration cannot
replace target-owned prompts, tools, modes or loop behavior. Native target tools
require `provider_tools.use_native: true`.

The helper publishes the exact `DependencyClosureManifest.canonical_bytes()` to
CAS with `artifact_id=closure.closure_digest` and `media_type="application/json"`.
Other composition authors must publish the same original closure. The artifact
ID is a semantic-digest alias, not the payload byte digest: the serialized object
also includes its `closure_digest`. Production loading checks both CAS byte
integrity and the parsed canonical closure identity, bundle binding, provenance
and member authority. A store written before producers published this alias is
loaded by rebuilding the closure from compiled provenance, with edge ordinals in
provenance order; it is admitted only if the rebuilt closure reproduces the
compiled closure digest exactly. A declared order that provenance cannot express,
such as Mini's target assets, fails that check, so such stores need the published
closure. F2 production authoring input v2 therefore requires an explicit
`authority.config_closure` artifact.

`E4TargetPolicyProjection.from_compiled` reads compiler output; it does not load or
render package files. Headless and SWE consumers select equivalent projections
from the composition's verified pinned artifacts. `load_pinned_compiler` performs
read-only acquisition, not admission or runtime activation. The selected final
plan remains authoritative and is checked before provider sampling.

The headless entrypoint selects the target before reading provider or composition
credentials. It binds the same composition-reference bytes across that preflight
and composition loading, and rejects a changed pinned-manifest set before starting
the service. A target/version/input rejection must not depend on credential access.

Compiler 1.2.0 binds the target descriptor, indexed bytes, input frame, generated
members, schema documents and lowering implementation. Recompile changed inputs;
do not relabel old manifests or receipts. Historical package bytes remain unchanged.
Pi 0.57.1 has supported legacy lowering. Loading the Oh My Pi 16.2.13 package does
not imply that its runtime semantics are implemented.

Captured configuration YAML uses the compiler's bounded parser, including
duplicate-key, alias, depth and node rejection. Historical target v1 keeps its
YAML 1.1 scalar interpretation within those bounds. Target v2 uses the strict
JSON-compatible scalar dialect; this does not relax ordinary agent-config parsing.

## Versioned inputs and serialization

`bb.e4.target.v2` and `bb.e4.target_config.v2` are closed contracts. Configuration
owns renderer, policy, input and materialization declarations; the descriptor
binds those bytes. Each input names its `value_schema`, producer, lifetime,
source reference and omission rule. Required/default/null declarations must be
consistent with that actual JSON Schema. Runtime-produced fields cannot be
supplied as caller bootstrap values.
Nested property schemas are checked even when their parent omits `required`.
The bounded schema vocabulary keeps standard JSON Schema keyword spellings.
V2 descriptor asset digests use the `sha256:` prefix. The shared v1 index and
historical v1 descriptor digest forms remain unchanged.

Headless request v1 remains text-only and pairs with target v1. Explicit
`bb.rl.headless-run-request.v2` accepts JSON values and pairs only with target v2.
`bb.rl.headless-run-request.v3` requires an explicit, closed
`provider.request_policy` and supports either target version. Target v1 still
requires non-empty text inputs; target v2 retains its declared JSON schemas.
Requests v1/v2 omit `request_policy` and use the historical streaming policy.
Missing, null, empty, false and zero are distinct. `bind_e4_target_inputs` validates
the selected declaration without filling omitted values. V1 frames retain JCS
encoding; v2/v3 byte identity preserves nested object order and numeric representation,
with target v2 outer fields ordered by the declaration.
This frame records supplied inputs, not constructor execution. An omitted
`omission: default` field stays absent; its declared default remains bound in the
verified configuration. An admitted source renderer applies omission/default
rules at its declared phase. Unsupported renderers are rejected rather than
executed; loading their declarations does not claim default behavior.
The installed distribution requires Pydantic `>=2.13.5,<3`, the supported
dependency floor for both the typed headless request and the public API models.

`breadboard.product.harness.targets.serialize_e4_target` takes `descriptor_path`,
descriptor fields **without** the derived `assets` table, complete configuration
and exact asset bytes. It emits the declared configuration asset, descriptor and
other members, verifies them through `read_e4_target`, and returns immutable
members relative to `config/e4_targets/` plus an additive `index_delta`. It does not
write files or replace the installed index. Review and merge that fragment while
preserving existing entries.

Parsing or serializing a v2 package does not grant runtime support. Unimplemented
renderers and required capabilities raise `E4TargetCapabilityError`; sealed
compilation reports `e4_runtime_capabilities_unsupported`. No new campaign target
is indexed by this compiler cutover. Source recipes and their complete runtime
implementations belong to their own profile changes.

## Rules

1. Keep package assets tracked in-repo; do not rely on ignored source trees.
2. Preserve historical dossiers byte-for-byte under `agent_configs/deprecated/`.
3. Keep scenario-specific replay assertions in overlays under `agent_configs/misc/`.
4. Mint a new package version when upstream source changes.
5. State capture-only, replay-only, and unsupported surfaces without broadening them.
6. Follow [the dossier style guide](E4_DOSSIER_STYLE_GUIDE_V1.md) for public files.
