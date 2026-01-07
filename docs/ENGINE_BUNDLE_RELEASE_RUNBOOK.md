# Engine Bundle Release Runbook (Phase 12)

This runbook describes how to publish **engine bundles** (the Python CLI-bridge sidecar packaged as a standalone binary) via **GitHub Releases**, and how to validate that the **BreadBoard CLI/TUI** can auto-download and run them.

The release pipeline is implemented in:
- `.github/workflows/release_engine_bundles.yml`

## What gets published

For each supported OS runner, the workflow builds and uploads:
- `breadboard-engine-<platform>-<arch>.tar.gz` (Linux/macOS)
- `breadboard-engine-<platform>-<arch>.zip` (Windows)
- `manifest.json` (merged from per-runner fragments)

The manifest includes (per-asset):
- `url` (release download URL)
- `sha256` checksum
- `size_bytes`
- `entry` (bundle entrypoint filename)

## Prerequisites

- Your branch/commit contains the engine + bundling scripts (`scripts/build_local_engine_bundle.py`, `scripts/build_engine_onedir.py`, `scripts/build_engine_bundle.py`).
- GitHub Actions runners must be able to install Python deps and run `pyinstaller`.
  - The workflow installs `requirements.txt`, `requirements_web.txt`, and `pyinstaller`.
- Expect large bundles currently (Ray is collected aggressively).

## Release steps (GitHub Actions)

1) Decide the **engine version string**.
   - Example: `0.2.0`
   - This becomes the `manifest.version` and is used for the cache key in the CLI (`~/.breadboard/engine/<version>`).

2) Trigger the workflow **manually**:
   - GitHub → Actions → **Release engine bundles** → Run workflow
   - Inputs:
     - `version`: required (e.g. `0.2.0`)
     - `release_tag`: optional (defaults to `engine-v<version>`)
     - `prerelease`: optional (defaults to `true`)

3) Wait for jobs:
   - `create_release` ensures the release exists.
   - `build_bundles` runs on `ubuntu-latest`, `macos-latest`, `windows-latest` and uploads artifacts.
   - `publish_release_assets` merges per-runner manifests and uploads bundle archives + merged `manifest.json` to the release.

## Validation steps (end-to-end)

After the release completes, validate the bundle works from a clean environment:

1) Set auto-download + manifest URL.
   - The manifest is uploaded as a release asset; its URL is:
     - `https://github.com/<org>/<repo>/releases/download/<tag>/manifest.json`

2) Run `doctor` and a minimal `run`:

```bash
export BREADBOARD_ENGINE_AUTO_DOWNLOAD=1
export BREADBOARD_ENGINE_MANIFEST_URL="https://github.com/<org>/<repo>/releases/download/<tag>/manifest.json"
export BREADBOARD_ENGINE_VERSION="<version>"

# optional: avoid Ray startup in local-only validation
export RAY_SCE_LOCAL_MODE=1

breadboard doctor --config agent_configs/opencode_mock_c_fs.yaml
breadboard run --config agent_configs/opencode_mock_c_fs.yaml "Smoke: say hi and exit."
```

Expected:
- The CLI downloads the correct platform asset, verifies `sha256`, extracts it under `~/.breadboard/engine/<version>/bundle/`, and starts the engine.
- `doctor` reports `status: ok` and prints the engine `protocol_version`.
- `run` creates a session and streams events.

## Troubleshooting

- **Manifest points at wrong URLs**
  - Ensure the workflow uses `BASE_URL` that matches the release tag. The workflow currently sets:
    - `BASE_URL=https://github.com/${{ github.repository }}/releases/download/${{ tag }}`
- **Bundle checksum mismatch**
  - Re-run the workflow; confirm the uploaded assets match the manifest `sha256`.
- **Bundled engine starts but logs warnings**
  - Some warnings are expected during early packaging iterations. For example, Ray-related package-data warnings may appear if a dependency’s data files weren’t collected.
  - Fixes should be made in `scripts/build_engine_onedir.py` (PyInstaller collection flags).

## Notes

- The CLI can also use a local manifest (filesystem path or `file://...`) for developer validation.
- The “full” Ray collection profile is currently the default; a slimmer profile may be introduced later once validated against parity requirements.

