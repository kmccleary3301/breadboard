# Lean snapshot shell tooling

These scripts are the host-side shell helpers used to build and activate Lean/Mathlib Firecracker snapshots.

- `fc_create_lean_snapshot_vsock.sh` builds a warm Lean snapshot with vsock support.
- `run_lean_snapshot_diag.sh` runs a bounded diagnostic build under `/tmp`.
- `run_lean_snapshot_diag_stable.sh` runs a bounded build into the repo-local `lean_snapshot/` area through a short alias path.
- `activate_lean_snapshot.sh` exports a selected snapshot into the current shell and can run the ATP Firecracker smoke/bench checks.
- `build_lean_snapshot_pool.sh` builds multiple independent snapshot directories for concurrent restore/load tests.

The scripts are repo-localized:

- they resolve the repository root from `scripts/lean_snapshot/`,
- they default to storing snapshots under `lean_snapshot/` at the repo root,
- they avoid hardcoding the outer `ray_SCE` directory layout.

Most builder operations still require `sudo` because they use loop-mount, chroot, and Firecracker host resources.
