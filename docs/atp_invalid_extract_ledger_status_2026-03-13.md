# ATP Invalid Extract Ledger Status — 2026-03-13

The invalid/unsound extract set is now centralized in:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/invalid_extract_ledger_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/invalid_extract_ledger_v1.md`

Source of truth:

- `scripts/build_hilbert_comparison_packs_v2.py`

Current audited invalid tasks:

- `mathd_numbertheory_780`
- `aime_1984_p5`
- `amc12a_2019_p12`
- `mathd_algebra_77`

Use this ledger for Pack K and later tranche construction instead of scattering exclusions across status notes.
