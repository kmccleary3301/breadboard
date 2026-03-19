# DARWIN Model Routing Policy V0

Date: 2026-03-19
Status: active internal routing policy
Scope: DARWIN-only planning and execution surfaces

## Purpose

This document sets the default inference-routing policy for DARWIN work without changing unrelated repository-wide defaults.

The goal is to standardize future DARWIN real-inference tranches around a low-cost default model and a cheaper bulk model while preserving a stronger tier for high-consequence reasoning.

## Default DARWIN model tiers

### Default low-cost model

Use:

- `openai/gpt-5.4-mini`

or, where routed through OpenRouter:

- `openrouter/openai/gpt-5.4-mini`

Role:

- default DARWIN search and mutation inference
- default iterative agentic inference inside DARWIN tranches
- default model for routine comparative experiments unless a tranche explicitly overrides it

## Bulk / low-cost model

Use:

- `openai/gpt-5.4-nano`

or, where routed through OpenRouter:

- `openrouter/openai/gpt-5.4-nano`

Role:

- bulk extraction
- classification
- cheap retries
- large matrix or batch operations
- low-risk worker roles where the tranche favors cost efficiency over top-end reasoning quality

## Higher-consequence tier

DARWIN may still use a stronger model tier for:

- planner-grade strategy synthesis
- difficult policy or tranche reviews
- final arbitration on ambiguous experimental reads
- exceptional cases where `gpt-5.4-mini` is measurably insufficient

This document does not set a single mandatory higher-tier route. That remains tranche-specific.

## Routing rules

### Use `gpt-5.4-mini` by default when:

- the task is DARWIN-local
- model inference is required
- the work is not a massive bulk operation
- there is no explicit tranche-level override

### Use `gpt-5.4-nano` when:

- the job is high-volume
- the job is bulk or matrix-oriented
- the task is low-risk and heavily repeated
- the work is primarily extraction, filtering, or cheap candidate generation

### Do not treat `gpt-5.4-nano` as the default for:

- tranche planning
- policy synthesis
- ambiguous comparative interpretation
- high-stakes transfer or lineage decisions

## Current DARWIN reality

Most recent DARWIN work has been:

- additive artifact work
- local evaluator work
- policy and comparative semantics work

That means this routing policy mainly affects:

- future real-inference DARWIN tranches
- future search and mutation expansion
- any future DARWIN-specific provider/config surfaces

It does **not** imply that the whole repository must be retuned immediately.

## Boundary

- this is a DARWIN-local policy only
- it does not rewrite repo-wide provider defaults
- it does not retroactively alter existing Phase-1 or ATP accounting docs
- it should be applied only where DARWIN work introduces or updates model-routing choices
