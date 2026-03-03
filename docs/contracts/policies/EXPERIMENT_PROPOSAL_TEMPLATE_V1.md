# Experiment Proposal (XP) Template V1

Use this template for capability experiments and benchmark claims that do not require kernel danger-zone changes.

## Header

- `xp_id`: `XP-YYYYMMDD-<slug>`
- `owner`: `<team/person>`
- `status`: `planned | running | completed | canceled`
- `workstream`: `atp | evolake | ctrees | other`
- `related_claim_ids`: `[claim_id, ...]`

## Hypothesis

What measurable change is expected?

## Null Hypothesis / Falsification

How can this fail, and what tests can disprove it?

## Experimental Design

- baseline and treatment definitions
- fixed budgets (tokens / time / cost)
- dataset or fixture snapshots
- random seed policy

## Metrics and Thresholds

- primary metric(s)
- guardrail metric(s) including generalization checks
- success threshold and minimum effect size

## Reproduction Contract

- `reproduction.commands` list
- expected artifacts
- evidence bundle path plan

## Risk Controls

- rollback condition
- overfit detection checks (ablations / negative controls)
- spend attribution fields to emit

## Outcome Log

- result summary
- claim ledger updates
- follow-up decisions (accept/reject/iterate)
