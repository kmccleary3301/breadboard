"""Typed dependency boundary for provider credential selection and issuance."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True, slots=True)
class CredentialSelector:
    """Optional account identity used to select one provider credential."""

    account_id: str | None = None
    credential_id: str | None = None
    label: str | None = None
    alias: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("account_id", self.account_id),
                ("credential_id", self.credential_id),
                ("label", self.label),
                ("alias", self.alias),
            )
            if value
        }


@dataclass(frozen=True, slots=True)
class CredentialOrigin:
    """Secret-free provenance for the credential selected by one operation."""

    kind: str
    account_id: str | None = None
    credential_id: str | None = None
    env_var: str | None = None
    source: str | None = None
    binding_kind: str | None = None
    binding_reason: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("kind", self.kind),
                ("account_id", self.account_id),
                ("credential_id", self.credential_id),
                ("env_var", self.env_var),
                ("source", self.source),
                ("binding_kind", self.binding_kind),
                ("binding_reason", self.binding_reason),
            )
            if value
        }




class CredentialAuthority(Protocol):
    """Dependency used by provider execution to inspect and lease credentials."""

    def get_credential_origin(
        self,
        provider_id: str,
        *,
        session_id: str = "",
        account_selector: CredentialSelector | Mapping[str, object] | None = None,
        environment_key: str | None = None,
        environment: Mapping[str, object] | None = None,
    ) -> dict[str, str] | None:
        """Return secret-free provenance for the selected provider credential."""

    def execution_material(
        self,
        provider_id: str,
        *,
        session_id: str = "",
        endpoint_id: str = "",
        account_selector: CredentialSelector | Mapping[str, object] | None = None,
        environment_key: str | None = None,
        environment: Mapping[str, object] | None = None,
        minimum_validity_ms: int = 0,
    ) -> AbstractContextManager[dict[str, object] | None]:
        """Lease scoped execution material and release it on context exit."""
