# @breadboard/execution-driver-remote

Delegated/remote execution-driver package for BreadBoard's V2 TypeScript runtime.

Current scope:
- remote/delegated placement compatibility
- remote sandbox-request construction
- adapter-driven remote execution boundary
- fetch-backed HTTP execution adapter for delegated sandbox requests
- explicit non-claim of direct sandbox implementation

This package exists so delegated execution remains behind the shared execution-driver contract rather than leaking backend-specific request shapes into the kernel.
