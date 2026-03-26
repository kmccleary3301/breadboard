# DARWIN Stage-4 Registry Integrity Note

Date: 2026-03-20
Status: registry audit note
References:
- `artifacts/darwin/stage4/family_program/family_registry_v0.json`
- `artifacts/darwin/stage4/family_program/family_promotion_report_v0.json`

## Note

The current registry is internally consistent with the promotion report:

- promoted families remain marked `promoted`
- withheld families remain marked `withheld`
- transfer eligibility is explicit on every family row
- evidence refs and decision refs are present on every family row

## Important read

`promotion_class` and `lifecycle_status` are not synonyms.

A family may remain `promoted` with a narrower replay or selector posture than a pure `promotion_ready` reading would imply. The canonical interpretation is therefore:

- `lifecycle_status` controls canonical family state
- `promotion_class` controls the evidentiary quality class

This distinction is deliberate and remains acceptable for the current bounded Stage-4 program.
