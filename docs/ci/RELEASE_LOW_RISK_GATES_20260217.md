# Release Low-Risk Gates Validation (2026-02-17)

This report records objective low-risk launch gate checks completed after repo
restore, using reproducible commands.

## Scope

- Repo/docs/proof/media validation only.
- No destructive operations.
- Runtime commands executed only with explicit safe workspace and/or preflight.

## Gate 1: Contract Export Freshness

Command:

```bash
python scripts/export_cli_bridge_contracts.py
```

Result: PASS

- OpenAPI regenerated:
  - `docs/contracts/cli_bridge/openapi.json`
- JSON schemas regenerated:
  - `docs/contracts/cli_bridge/schemas/`

## Gate 2: Proof Bundle Command Path

Commands:

```bash
bash scripts/phase12_live_smoke.sh
python scripts/log_reduce.py logging/20260218-001713_agent_ws_smoke_local --turn-limit 2 --tool-only > docs/media/proof/launch_v1/log_reduce_sample_v1.txt
```

Result: PASS

- Smoke script passed (`[phase12-smoke] ok`).
- Run completed with safe explicit workspace:
  - `/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/agent_ws_smoke_local`
- Reduced log sample written:
  - `docs/media/proof/launch_v1/log_reduce_sample_v1.txt`
- Proof bundle present:
  - `docs/media/proof/bundles/launch_proof_bundle_v1.zip`

## Gate 3: Core Launch Docs Link Stability

Validation target files:

- `README.md`
- `docs/RELEASE_LANDING_V1.md`

Result: PASS

- Markdown doc links discovered: 37
- Missing links: 0

## Gate 4: Branding Hash Manifest Currency

Generated checksum manifest:

- `docs/media/branding/checksums.sha256`

Result: PASS

- Includes all current v1 branding assets and docs-side branding media files.
- Canonical SVG hash retained:
  - `6f09c6b9013bcf5135a8e02953f397b0682bf9d46ad3435aacf4e2f800846c85`

## Notes

- This report does not cover external channel feedback gates.
- This report does not claim broad parity/completeness beyond documented coverage.

