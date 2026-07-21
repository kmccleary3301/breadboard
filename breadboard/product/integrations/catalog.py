"""Stable integration discovery and capability-probe boundary.

The catalog is deliberately instance-owned: integrations become visible only when
an application explicitly registers a conforming adapter.  This keeps discovery
independent from target-specific registries and makes an unknown adapter fail
closed during preflight.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable


_KIND_VALUES = {"provider_adapter", "tool_executor", "host_driver", "capture_adapter", "artifact_store"}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SCHEMA_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RFC3339_RE = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})[Tt]"
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})(?:\.[0-9]+)?"
    r"(?:[Zz]|(?P<offset_sign>[+-])(?P<offset_hour>[0-9]{2}):(?P<offset_minute>[0-9]{2}))$"
)
_LEAP_SECOND_DATES = frozenset({
    "1972-06-30", "1972-12-31", "1973-12-31", "1974-12-31", "1975-12-31",
    "1976-12-31", "1977-12-31", "1978-12-31", "1979-12-31", "1981-06-30",
    "1982-06-30", "1983-06-30", "1985-06-30", "1987-12-31", "1989-12-31",
    "1990-12-31", "1992-06-30", "1993-06-30", "1994-06-30", "1995-12-31",
    "1997-06-30", "1998-12-31", "2005-12-31", "2008-12-31", "2012-06-30",
    "2015-06-30", "2016-12-31",
})


def _is_rfc3339_timestamp(value: object) -> bool:
    if not isinstance(value, str) or (match := _RFC3339_RE.fullmatch(value)) is None:
        return False
    parts = {name: int(match[name] or 0) for name in ("year", "month", "day", "hour", "minute", "second", "offset_hour", "offset_minute")}
    year, month, day = parts["year"], parts["month"], parts["day"]
    month_days = (31, 29 if year % 400 == 0 or year % 4 == 0 and year % 100 != 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if not 1 <= month <= 12 or not 1 <= day <= month_days[month - 1]:
        return False
    if parts["hour"] > 23 or parts["minute"] > 59 or parts["second"] > 60 or parts["offset_hour"] > 23 or parts["offset_minute"] > 59:
        return False
    if parts["second"] < 60:
        return True
    if year == 0:
        return False
    offset_sign = 1 if match["offset_sign"] == "+" else -1 if match["offset_sign"] == "-" else 0
    try:
        utc_minute = datetime(year, month, day, parts["hour"], parts["minute"], 59) - timedelta(minutes=offset_sign * (parts["offset_hour"] * 60 + parts["offset_minute"]))
    except OverflowError:
        return False
    return utc_minute.hour == 23 and utc_minute.minute == 59 and utc_minute.date().isoformat() in _LEAP_SECOND_DATES


class IntegrationError(ValueError):
    """Base error raised by the integration boundary."""


class UnknownIntegrationError(IntegrationError, KeyError):
    """Raised when a caller asks for an integration that was not registered."""


class IncompatibleAdapterError(IntegrationError):
    """Raised when an adapter does not implement the frozen integration port."""


class ProjectDeclarationError(IntegrationError):
    """Raised when a project integration declaration cannot be trusted."""


def _validate_metadata_arrays(*groups: object) -> None:
    for group in groups:
        if not isinstance(group, (list, tuple)) or any(not isinstance(value, str) or not value.strip() for value in group):
            raise IntegrationError("integration metadata must contain non-empty strings")
        if len(set(group)) != len(group):
            raise IntegrationError("integration metadata must contain unique strings")


@runtime_checkable
class IntegrationAdapter(Protocol):
    descriptor: "IntegrationDescriptor"

    def probe(self) -> "ProbeReport": ...


@dataclass(frozen=True)
class IntegrationDescriptor:
    schema_version: str
    integration_id: str
    kind: str
    contract_version: str
    implementation_id: str
    capabilities: tuple[str, ...] = ()
    configuration_schema_id: str | None = None
    secret_reference_names: tuple[str, ...] = ()
    status: str = "available"
    probe_evidence_sha256: str | None = None
    effects: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != "bb.integration_descriptor.v1":
            raise IntegrationError("unsupported integration descriptor schema")
        if not _ID_RE.fullmatch(self.integration_id) or not self.implementation_id:
            raise IntegrationError("integration identities must be non-empty and stable")
        if self.kind not in _KIND_VALUES:
            raise IntegrationError(f"unknown integration kind: {self.kind}")
        if not self.contract_version or self.status not in {"available", "unavailable"}:
            raise IntegrationError("invalid integration descriptor")
        if self.configuration_schema_id is not None and not _SCHEMA_ID_RE.fullmatch(self.configuration_schema_id):
            raise IntegrationError("invalid configuration schema id")
        _validate_metadata_arrays(self.capabilities, self.effects, self.permissions, self.secret_reference_names)
        if self.probe_evidence_sha256 is not None and not _HASH_RE.fullmatch(self.probe_evidence_sha256):
            raise IntegrationError("probe evidence must be a sha256 digest")

    def to_record(self) -> dict[str, Any]:
        """Return exactly the public descriptor record (never runtime config)."""
        return {
            "schema_version": self.schema_version,
            "integration_id": self.integration_id,
            "kind": self.kind,
            "contract_version": self.contract_version,
            "implementation_id": self.implementation_id,
            "capabilities": list(self.capabilities),
            "configuration_schema_id": self.configuration_schema_id,
            "secret_reference_names": list(self.secret_reference_names),
            "status": self.status,
            "probe_evidence_sha256": self.probe_evidence_sha256,
        }


@dataclass(frozen=True)
class ProbeReport:
    schema_version: str
    report_id: str
    integration_id: str
    kind: str
    implementation_id: str
    status: str
    capabilities: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    error: str | None = None
    checked_at_utc: str = ""
    def __post_init__(self) -> None:
        if self.schema_version != "bb.capability_probe_report.v1" or not _ID_RE.fullmatch(self.report_id):
            raise IntegrationError("invalid capability probe report identity")
        if not _ID_RE.fullmatch(self.integration_id) or self.kind not in _KIND_VALUES or not self.implementation_id:
            raise IntegrationError("invalid capability probe subject")
        if not _is_rfc3339_timestamp(self.checked_at_utc):
            raise IntegrationError("capability probe must include an RFC3339 checked_at_utc")
        _validate_metadata_arrays(self.capabilities, self.effects, self.permissions)
        if self.status not in {"available", "unavailable"}:
            raise IntegrationError("invalid capability probe status")
        if self.status == "available" and self.error is not None:
            raise IntegrationError("available probe cannot contain an error")
        if self.status == "unavailable" and not self.error:
            raise IntegrationError("unavailable probe requires an error")

    @property
    def identity(self) -> dict[str, str]:
        return {
            "integration_id": self.integration_id,
            "kind": self.kind,
            "implementation_id": self.implementation_id,
        }

    def to_record(self) -> dict[str, Any]:
        # Identity is intentionally allow-listed.  Runtime objects, credentials,
        # endpoints, and exception payloads must never enter a public record.
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "integration_id": self.integration_id,
            "kind": self.kind,
            "implementation_id": self.implementation_id,
            "identity": self.identity,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "effects": list(self.effects),
            "permissions": list(self.permissions),
            "error": self.error,
            "checked_at_utc": self.checked_at_utc,
        }


class IntegrationCatalog:
    """Explicit, deterministic catalog for conforming integration adapters."""

    def __init__(self, adapters: Iterable[IntegrationAdapter] = ()) -> None:
        self._adapters: dict[str, IntegrationAdapter] = {}
        self._register_many(adapters)

    @staticmethod
    def _validate_adapter(adapter: IntegrationAdapter) -> IntegrationDescriptor:
        descriptor = getattr(adapter, "descriptor", None)
        probe = getattr(adapter, "probe", None)
        if not isinstance(descriptor, IntegrationDescriptor) or not callable(probe):
            raise IncompatibleAdapterError("adapter must expose a descriptor and probe()")
        return descriptor

    def _register_many(self, adapters: Iterable[IntegrationAdapter]) -> list[IntegrationDescriptor]:
        pending: dict[str, IntegrationAdapter] = {}
        descriptors: list[IntegrationDescriptor] = []
        for adapter in adapters:
            descriptor = self._validate_adapter(adapter)
            if descriptor.integration_id in self._adapters or descriptor.integration_id in pending:
                raise IntegrationError(f"integration already registered: {descriptor.integration_id}")
            pending[descriptor.integration_id] = adapter
            descriptors.append(descriptor)
        self._adapters.update(pending)
        return descriptors

    def register(self, adapter: IntegrationAdapter) -> IntegrationDescriptor:
        return self._register_many((adapter,))[0]

    def list(self, *, kind: str | None = None) -> list[IntegrationDescriptor]:
        values = (adapter.descriptor for adapter in self._adapters.values())
        if kind is not None and kind not in _KIND_VALUES:
            raise IntegrationError(f"unknown integration kind: {kind}")
        return sorted((item for item in values if kind is None or item.kind == kind), key=lambda item: item.integration_id)

    def get(self, integration_id: str) -> IntegrationAdapter:
        try:
            return self._adapters[integration_id]
        except KeyError as exc:
            raise UnknownIntegrationError(integration_id) from exc

    def probe(self, integration_id: str | None = None) -> ProbeReport | list[ProbeReport]:
        adapters = [self.get(integration_id)] if integration_id is not None else [self._adapters[key] for key in sorted(self._adapters)]
        reports: list[ProbeReport] = []
        for adapter in adapters:
            try:
                report = adapter.probe()
                if not isinstance(report, ProbeReport):
                    raise IncompatibleAdapterError("probe() must return ProbeReport")
                if (
                    report.integration_id != adapter.descriptor.integration_id
                    or report.kind != adapter.descriptor.kind
                    or report.implementation_id != adapter.descriptor.implementation_id
                ):
                    raise IncompatibleAdapterError("probe identity does not match descriptor")
            except Exception as exc:
                report = ProbeReport(
                    "bb.capability_probe_report.v1",
                    _report_id(adapter.descriptor.integration_id),
                    adapter.descriptor.integration_id,
                    adapter.descriptor.kind,
                    adapter.descriptor.implementation_id,
                    "unavailable",
                    error=type(exc).__name__,
                    checked_at_utc=_now(),
                )
            reports.append(report)
        return reports[0] if integration_id is not None else reports

    def load_capture_adapters(self, *, group: str = "breadboard.capture_adapters") -> list[IntegrationDescriptor]:
        from .capture import load_capture_entry_points

        return self._register_many(load_capture_entry_points(group=group))

    def preflight(
        self,
        declarations: Sequence[Mapping[str, Any]],
        *,
        project_root: str,
        adapters: Mapping[str, IntegrationAdapter] | None = None,
    ) -> list[IntegrationDescriptor]:
        from .capture import resolve_local_capture_declaration

        resolved = [
            resolve_local_capture_declaration(
                declaration,
                project_root=project_root,
                adapters=adapters,  # type: ignore[arg-type]
            )
            for declaration in declarations
        ]
        return self._register_many(resolved)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _report_id(integration_id: str) -> str:
    return "probe:" + integration_id




def probe_for(
    descriptor: IntegrationDescriptor,
    *,
    capabilities: Iterable[str] | None = None,
    effects: Iterable[str] | None = None,
    permissions: Iterable[str] | None = None,
    error: str | None = None,
) -> ProbeReport:
    status = "unavailable" if error else descriptor.status
    report = ProbeReport(
        "bb.capability_probe_report.v1",
        _report_id(descriptor.integration_id),
        descriptor.integration_id,
        descriptor.kind,
        descriptor.implementation_id,
        status,
        tuple(sorted(set(capabilities or descriptor.capabilities))),
        tuple(sorted(set(effects or descriptor.effects))),
        tuple(sorted(set(permissions or descriptor.permissions))),
        error=error,
        checked_at_utc=_now(),
    )
    return report
