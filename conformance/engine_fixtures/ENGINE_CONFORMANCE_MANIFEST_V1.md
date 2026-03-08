# Engine Conformance Manifest V1

## Purpose

This document describes the intended role of the engine conformance manifest.

The engine conformance manifest is the multi-engine counterpart to the target-harness freeze manifest.

Its purpose is to record, for shared-kernel conformance claims:

- which engine implementation is being evaluated
- against which contract version
- on which scenario or fixture lane
- with which comparator class
- with which evidence paths
- with which exemptions or support-tier limits

---

## Why this matters

Without a tracked manifest, engine conformance claims will drift into informal status reporting.

That is not acceptable for a system that intends to support:

- multiple interpreters
- replay-sensitive semantics
- parity-sensitive tool rendering
- host integration claims

---

## First-pass row shape

Each manifest row should eventually record at least:

- engine family
- engine ref or commit
- contract version
- scenario id
- support tier
- comparator class
- evidence paths
- exemptions
- notes

---

## Comparator classes

Initial comparator classes to support:

- shape-equal
- normalized-trace-equal
- model-visible-equal
- workspace-side-effects-equal
- projection-equal

These should be chosen intentionally per fixture family rather than treated as one universal equality mode.

---

## Relationship to existing E4 discipline

The project already knows how to do this kind of governance for target harnesses:

- freeze exact versions
- record evidence paths
- declare strictness
- audit drift

The engine conformance manifest should bring that same rigor to Python-vs-TS and contract-vs-engine questions.
