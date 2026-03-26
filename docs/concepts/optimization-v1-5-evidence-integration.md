# Optimization V1.5 Evidence And Integration

Optimization V1.5 is the first post-V1 optimization program.

V1 finished the substrate:

- typed targets and loci
- datasets and truth packages
- evaluation, diagnostics, and wrongness
- a reflective backend
- promotion and safety gates
- environment-aware runtime context

V1.5 changes the center of gravity.

It is not another substrate expansion. It is the point where BreadBoard starts answering:

- how candidates are compared credibly
- how hidden holds and visibility rules are enforced
- how benchmark packs are represented
- how promotion evidence becomes trustworthy on real harness lanes

## New V1.5 records

The first V1.5 methodology slice adds these records in `agentic_coder_prototype/optimize/benchmark.py`:

- `BenchmarkSplit`
- `BenchmarkRunManifest`
- `CandidateComparisonResult`
- `BenchmarkRunResult`

These are deliberately **non-kernel** optimization-layer records.

They are not new BreadBoard kernel truth.

The promotion-robustness slice then extends the existing promotion layer with:

- `PromotionEvidenceSummary`
- `evaluate_comparison_gate`
- benchmark-aware `promote_candidate(...)`

Those additions stay inside the optimization layer as well. They tighten how benchmark evidence is summarized and how ambiguous comparison outcomes affect promotion.

The fixed-methodology backend-comparison slice then adds:

- `BackendComparisonResult`
- `SingleLocusGreedyBackend`
- `run_single_locus_greedy_backend(...)`

This is still intentionally non-kernel. It exists to compare backend families on the exact same manifests and comparison rules rather than by anecdote.

## Why these records exist

V1 could define and evaluate candidates, but it did not yet define how BreadBoard should say:

- which split was mutation-visible
- which split was comparison-only
- which split was a hidden hold
- which candidates were compared under which protocol
- whether the outcome was a win, tie, non-inferior result, inconclusive result, or blocked result

V1.5 starts making that methodology explicit.

## BenchmarkRunManifest

`BenchmarkRunManifest` defines the benchmark contract for one optimization lane.

It records:

- target and dataset identity
- baseline candidate identity
- environment domain
- evaluator stack
- comparison protocol
- split definitions
- bucket tags
- stochasticity class
- rerun policy
- contamination notes
- promotion relevance

This is the record that makes a benchmark run interpretable.

## CandidateComparisonResult

`CandidateComparisonResult` records parent-vs-child comparison under an explicit protocol.

It records:

- parent and child candidate ids
- protocol id
- compared sample ids
- held-out sample ids
- trial count
- outcome
- rationale
- evidence refs
- metric deltas

Allowed outcomes are intentionally narrow:

- `win`
- `loss`
- `tie`
- `non_inferior`
- `inconclusive`
- `blocked`

This is the first step toward explicit comparison and promotion epistemology.

## BenchmarkRunResult

`BenchmarkRunResult` packages one benchmark run.

It records:

- candidate set
- comparison results
- aggregate metrics
- bucket outcomes
- variance summary
- cost/support/evidence slices
- artifact refs
- promotion readiness summary

This is still methodology-oriented. It is not yet a giant public scoreboard ontology.

## Promotion robustness

V1.5 now hardens promotion on top of the benchmark methodology.

The new `PromotionEvidenceSummary` captures the subset of benchmark evidence that promotion actually needs to reason about:

- comparison ids
- manifest ids
- held-out sample coverage
- regression sample coverage
- compared regression coverage
- stochasticity class
- minimum required reruns
- observed trial count
- outcome counts
- whether the lane still requires explicit review

This summary is attached to promotion evidence metadata rather than becoming a second public truth layer.

## Comparison-aware promotion gating

Promotion is no longer only replay/conformance/support-envelope aware.

When benchmark evidence is present, `evaluate_promotion_gates(...)` now adds a comparison gate that checks:

- whether the candidate has any comparison evidence at all
- whether regression samples were actually covered
- whether stochastic manifests satisfied their rerun policy
- whether outcomes were `blocked`, `inconclusive`, or `tie`
- whether the child actually beat or at least non-inferiorly matched its parent

This matters because V1.5 is trying to answer a harder question than "did the backend prefer this child?" The real question is whether the child is promotable under explicit comparison rules.

The current policy is intentionally conservative:

- missing regression coverage keeps the candidate on the frontier
- stochastic manifests with too few trials keep the candidate on the frontier
- `blocked`, `tie`, and `inconclusive` outcomes keep the candidate on the frontier
- a clear `loss` rejects the candidate
- `win` and `non_inferior` can pass the comparison gate

That keeps promotion evidence honest without inventing broader campaign semantics.

## Backend comparison on fixed methodology

V1.5 now also includes a deliberately narrow backend comparison lane.

The existing reflective backend is compared against a second backend family, `SingleLocusGreedyBackend`, which reuses the same reflection and mutation discipline but only keeps the first proposed repair. That gives BreadBoard a real "smaller search family" baseline without expanding the substrate.

The `BackendComparisonResult` record summarizes:

- which backend families were compared
- which fixed manifests were used
- which benchmark runs belonged to each backend
- per-backend outcome summaries
- the evidence-backed winner
- reproducibility notes tying the comparison to the fixed V1.5 methodology

The canonical comparison builder is:

- `build_backend_comparison_example`
- `build_backend_comparison_example_payload`

This comparison intentionally stays conservative. It uses the same three pilot manifests and shows the reflective backend outperforming the single-locus greedy family on support-sensitive and coding-overlay lanes while matching it on tool-guidance. The point is not to crown a permanent winner; the point is to make backend conclusions reproducible and methodology-bound.

## Pilot lanes

The first V1.5 methodology slice now includes three canonical pilot benchmark packs:

- support-claim + execution-profile heuristic optimization
- tool description / tool-guidance optimization
- bounded coding-harness overlay optimization

The canonical builders are in `agentic_coder_prototype/optimize/examples.py`:

- `build_support_execution_benchmark_example`
- `build_support_execution_benchmark_example_payload`
- `build_tool_guidance_benchmark_example`
- `build_tool_guidance_benchmark_example_payload`
- `build_coding_overlay_benchmark_example`
- `build_coding_overlay_benchmark_example_payload`

Together these examples prove:

- a real benchmark manifest with train/validation/hold/regression splits
- explicit hidden-hold handling
- parent-vs-child paired comparison paths
- support/evidence-sensitive promotion relevance where needed
- one safe wording lane, one BreadBoard-native support/execution lane, and one bounded coding-overlay lane
- comparison-backed promotion behavior for the support/execution lane, including stochastic and inconclusive blocking

## Reading order

For the smallest end-to-end V1.5 walkthrough, read:

1. `build_tool_guidance_benchmark_example`
2. `build_support_execution_benchmark_example`
3. `build_coding_overlay_benchmark_example`
4. `BenchmarkRunManifest`
5. `CandidateComparisonResult`
6. `BenchmarkRunResult`
7. `PromotionEvidenceSummary`
8. `evaluate_comparison_gate`
9. `promote_candidate`
10. `SingleLocusGreedyBackend`
11. `BackendComparisonResult`
12. `build_backend_comparison_example`

That sequence shows the first V1.5 methodology slice across the three pilot target families named in the execution plan.

## What this does not do

This slice does not introduce:

- DARWIN campaign semantics
- island or QD ontology
- online self-tuning
- broad UI workflowization
- public backend scheduler nouns

It keeps the new layer narrow and evidence-led.
