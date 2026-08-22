# BBOMP-PDR-CLI-001 — BreadBoard root-help and completion surface

Status: PROPOSED (approval gated on snapshot capture; see Preconditions)
Date: 2026-08-19
Owner: Kyle McCleary
Stable IDs governed: OMP-CLI-CMD-COMPLETIONS, OMP-CLI-ROOT-HELP, OMP-CLI-SUB-COMPLETIONS-BASH

## Decision

BreadBoard-specific commands in root help and shell completion are an owned product
divergence. BreadBoard root help and completion output are evaluated against approved
BreadBoard snapshots, not byte equivalence with OMP v17.0.7.

## Preconditions for approval

Capture and hash before flipping Status to APPROVED:
- exact `bb --help` output;
- command inventory;
- Bash completion output;
- any other shipped completion formats;
- behavior when both `bb` and `omp` are installed;
- absence of accidental `omp` alias replacement.

## Frozen-corpus interaction

`P31_STABLE_ID_CLAIM_PROJECTION_R2.tsv` (sha256 3203ee1d32fb54fcce763405815051da62b69af78468d8a839cedd4360165090)
is never rewritten. Prospective dispositions live in the successor overlay
`advisory/P31_PRODUCT_DISPOSITION_OVERLAY_V1.tsv`.

## Supersession

Record successor PDR when replaced.
