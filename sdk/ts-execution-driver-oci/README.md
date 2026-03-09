# @breadboard/execution-driver-oci

OCI-oriented execution-driver helpers.

Current scope:
- placement selection for OCI, gVisor, and Kata-backed lanes
- sandbox-request construction for OCI-style execution
- participate in planned-execution records with explicit side-effect and evidence expectations
- provide the first real OCI runtime invocation helper behind the shared execution-driver contract

This package does not yet implement real Docker/containerd calls. It defines the contract-consistent helper layer those drivers should share.
