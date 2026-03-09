# @breadboard/execution-driver-oci

OCI-oriented execution-driver helpers.

Current scope:
- placement selection for OCI, gVisor, and Kata-backed lanes
- sandbox-request construction for OCI-style execution
- participate in planned-execution records with explicit side-effect and evidence expectations
- provide the first real OCI runtime invocation helper behind the shared execution-driver contract

This package does not attempt to own Docker/containerd fleet management. It does provide a real command-executor-backed OCI execution path, which is enough for the scoped V2 TS host/runtime slices.
