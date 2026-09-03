"""Security substrate: centralized redaction primitives (C-G0c)."""

from .child_environment import (
    build_child_environment,
    contains_provider_credential_value,
    is_provider_credential_env_key,
    initial_provider_credential_keys,
    provider_credential_values,
    provider_credentials_hidden,
    purge_provider_credentials,
    sanitized_process_environment,
)
from .isolation_errors import ProcessIsolationUnavailable
from .launch_policy import build_restricted_process_command
from .credential_boundary import (
    protected_credential_paths,
    register_protected_credential_path,
    validate_workspace_credential_boundary,
)
from .process_policy import (
    ChildEnvironmentPlan,
    ChildProcessLaunchPlan,
    ChildProcessPolicy,
)
from .redaction import (
    REDACTED,
    RedactionProblem,
    clear_registered_secret_values,
    is_secret_key,
    iter_registered_secret_values,
    secret_value_scope,
    scrub_headers,
    scrub_structure,
    scrub_text,
)
from .workspace_files import (
    WorkspaceEntry,
    WorkspaceFileInfo,
    WorkspaceFilesystem,
    WorkspacePathError,
)

__all__ = [
    "build_child_environment",
    "contains_provider_credential_value",
    "is_provider_credential_env_key",
    "initial_provider_credential_keys",
    "provider_credential_values",
    "provider_credentials_hidden",
    "purge_provider_credentials",
    "sanitized_process_environment",
    "ChildEnvironmentPlan",
    "ChildProcessLaunchPlan",
    "ChildProcessPolicy",
    "REDACTED",
    "RedactionProblem",
    "clear_registered_secret_values",
    "ProcessIsolationUnavailable",
    "build_restricted_process_command",
    "protected_credential_paths",
    "register_protected_credential_path",
    "validate_workspace_credential_boundary",
    "is_secret_key",
    "iter_registered_secret_values",
    "secret_value_scope",
    "scrub_headers",
    "scrub_structure",
    "scrub_text",
    "WorkspaceEntry",
    "WorkspaceFileInfo",
    "WorkspaceFilesystem",
    "WorkspacePathError",
]
