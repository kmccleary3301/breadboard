# DARWIN External-Safe Invalidity and Contamination Note

Date: 2026-03-19
Status: active note for the external-safe evidence tranche
Primary issue: `breadboard_repo_darwin_phase1_20260313-ovi`

## Purpose

This note defines the invalidity and contamination posture that must remain visible in every external-safe DARWIN artifact for the current tranche.

## Required visible caveats

- invalid-comparison rows must remain visible
- budget mismatch must remain visible
- evaluator-comparability limits must remain visible
- topology mismatch or descriptive-only transfer status must remain visible
- replay dependence must remain visible
- local-cost accounting limits must remain visible

## Current material caveats

- `lane.repo_swe` contains an invalid comparison due to `budget_class_mismatch`
- the bounded harness→research transfer remains replay-stable but descriptive-only for external-safe purposes
- `lane.atp` remains audit-only and is not a proving lane in this tranche
- cost-facing comparative surfaces remain partial because external billing is unavailable and local cost is often exact-local-zero

## Rule

If a reviewer would need a caveat to avoid overreading the result, the caveat must appear in the memo, packet, or reviewer summary.
