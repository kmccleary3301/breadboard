# Engine Install Strategy (Draft)

This document defines the **no‑bundling** install strategy for the BreadBoard engine.

## Goals
- Engine remains a **pure Python** service (FastAPI + Ray).
- No Python bundling or frozen executables.
- CLI manages the engine lifecycle and installation.

## Proposed Flow

### 1) Managed venv
The CLI creates and manages a dedicated virtual environment:

```
~/.breadboard/engine/venv
```

### 2) Pinned engine versions
The CLI installs the engine from PyPI using a pinned version that matches the CLI’s protocol:

```
pip install breadboard-engine==<version>
```

### 3) Upgrade command
`breadboard upgrade` should:

1. Upgrade the CLI (via its install method).
2. Upgrade the engine in the managed venv.
3. Restart the engine if it’s running.

### CLI command hints (future)

```
breadboard engine install
breadboard engine upgrade
breadboard engine status
```

## CLI contract (draft)

### `breadboard engine install`
Expected behavior:
- Creates managed venv at `~/.breadboard/engine/venv`.
- Installs pinned engine wheel matching CLI protocol.
- Emits a short summary (engine version, venv path, python path).

Suggested flags:
- `--python /path/to/python` (override interpreter)
- `--version X.Y.Z` (install specific engine version)
- `--force` (recreate venv)

### `breadboard engine status`
Expected output:
- engine version + protocol
- managed venv path
- python executable path
- running status + pid (if available)

### 4) Local override for devs
Maintainers can override the engine path via config for source‑based development:

```
engine_path: /path/to/breadboard_repo
```

## Non‑Goals
- No standalone engine binaries.
- No auto‑install to global site‑packages.

## Status
Draft — executable once CLI packaging is finalized.
