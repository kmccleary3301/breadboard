# Generalization Impact Template V1

Use this template for any change that may affect kernel generalization, parity behavior, or extension-boundary guarantees.

## 1) Change Summary

- Change ID:
- PR link:
- Owner:
- Date:
- Scope:

## 2) Surfaces Touched

- Kernel contracts touched:
- Extension contracts touched:
- Request-body parity surfaces touched:
- Tool-schema parity surfaces touched:
- Replay/evidence surfaces touched:

## 3) Generalization Risk Assessment

- Does this change increase coupling between kernel and extension surfaces?
- Does this change alter default behavior when extensions are disabled?
- Does this change reduce portability across harness emulation profiles?
- Does this change require new profile-specific exceptions?

## 4) Required Evidence

- Import-boundary checks:
- Endpoint-gating checks:
- Request-body hash checks:
- Tool-schema hash checks:
- Replay/evidence validation:

## 5) Compatibility and Migration

- Compatibility classification (`breaking|additive|internal`):
- Version bump required:
- Migration path:
- Rollback path:

## 6) Decision

- Reviewer(s):
- Decision:
- Conditions:
- Follow-up items:
