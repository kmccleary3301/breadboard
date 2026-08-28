"""Public facade and direct-script entrypoint for process isolation."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__:
    from .credential_boundary import (
        protected_credential_paths,
        register_protected_credential_path,
        validate_workspace_credential_boundary,
    )
    from .isolation_errors import ProcessIsolationUnavailable
    from .launch_policy import build_restricted_process_command
    from .linux_isolation import main
else:
    _security_root = Path(__file__).resolve().parent
    _package_root = _security_root.parent.parent
    if str(_package_root) not in sys.path:
        sys.path.insert(0, str(_package_root))
    from breadboard_engine.security.credential_boundary import (
        protected_credential_paths,
        register_protected_credential_path,
        validate_workspace_credential_boundary,
    )
    from breadboard_engine.security.isolation_errors import ProcessIsolationUnavailable
    from breadboard_engine.security.launch_policy import (
        build_restricted_process_command,
    )
    from breadboard_engine.security.linux_isolation import main

__all__ = [
    "ProcessIsolationUnavailable",
    "build_restricted_process_command",
    "protected_credential_paths",
    "register_protected_credential_path",
    "validate_workspace_credential_boundary",
]


if __name__ == "__main__":
    raise SystemExit(main())
