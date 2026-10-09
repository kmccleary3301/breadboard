"""Append-only compaction records and model-view projection.

The full model-facing history is never rewritten. Each compaction appends a
:class:`CompactionRecord`; the request view is rebuilt from the full history
plus records, the way OMP rebuilds context from ``CompactionEntry``
(``firstKeptEntryId``) entries. Indices refer to positions in the full,
append-only message list.

A *boundary* record replaces ``messages[head:first_kept_index]`` with its
``summary_messages`` (or a provider-native replay marker). An *edit* record
replaces individual messages at or after the active boundary (shake, prune,
image dropping) without moving the boundary.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .transcript import is_tool_result, leading_system_count, role_of, tool_call_ids

NATIVE_MARKER_KEY = "bb_native_compaction"
RECORD_SCHEMA = "bb.compaction_record.v1"


def _freeze(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False))


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NativeCompaction:
    """Opaque provider-native compaction payload.

    ``items`` are replayed verbatim to the same provider API and model
    (OpenAI Responses ``compaction`` output items, Anthropic ``compaction``
    content blocks). Other targets use the record's readable summary.
    """

    provider: str
    api: str
    model: str
    items: Tuple[Mapping[str, Any], ...]
    token_estimate: int = 0

    def usable_for(self, target: Optional["ProjectionTarget"]) -> bool:
        return (
            target is not None
            and target.provider == self.provider
            and target.api == self.api
            and target.model == self.model
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "api": self.api,
            "model": self.model,
            "items": [_freeze(item) for item in self.items],
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NativeCompaction":
        return cls(
            provider=str(raw["provider"]),
            api=str(raw["api"]),
            model=str(raw["model"]),
            items=tuple(_freeze(item) for item in raw.get("items") or ()),
            token_estimate=int(raw.get("token_estimate") or 0),
        )


@dataclass(frozen=True)
class ProjectionTarget:
    """The provider route a request view is built for."""

    provider: str
    api: str
    model: str


@dataclass(frozen=True)
class MessageEdit:
    index: int
    message: Mapping[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "message": _freeze(self.message)}


@dataclass(frozen=True)
class CompactionRecord:
    record_id: str
    sequence: int
    method: str
    reason: str
    created_at: str
    tokens_before: int
    history_length: int
    """Length of the full history when the record was made."""
    first_kept_index: Optional[int] = None
    summary: Optional[str] = None
    short_summary: Optional[str] = None
    summary_messages: Tuple[Mapping[str, Any], ...] = ()
    native: Optional[NativeCompaction] = None
    edits: Tuple[MessageEdit, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)
    tokens_after: Optional[int] = None
    warning: Optional[str] = None

    @property
    def is_boundary(self) -> bool:
        return self.first_kept_index is not None

    @property
    def readable(self) -> bool:
        return self.is_boundary and bool(self.summary_messages)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": RECORD_SCHEMA,
            "record_id": self.record_id,
            "sequence": self.sequence,
            "method": self.method,
            "reason": self.reason,
            "created_at": self.created_at,
            "tokens_before": self.tokens_before,
            "history_length": self.history_length,
            "first_kept_index": self.first_kept_index,
            "summary": self.summary,
            "short_summary": self.short_summary,
            "summary_messages": [_freeze(m) for m in self.summary_messages],
            "native": self.native.to_dict() if self.native else None,
            "edits": [edit.to_dict() for edit in self.edits],
            "details": _freeze(dict(self.details)),
            "tokens_after": self.tokens_after,
            "warning": self.warning,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CompactionRecord":
        if raw.get("schema_version") != RECORD_SCHEMA:
            raise ValueError("compaction record schema_version is invalid")
        return cls(
            record_id=str(raw["record_id"]),
            sequence=int(raw["sequence"]),
            method=str(raw["method"]),
            reason=str(raw["reason"]),
            created_at=str(raw["created_at"]),
            tokens_before=int(raw["tokens_before"]),
            history_length=int(raw["history_length"]),
            first_kept_index=raw.get("first_kept_index"),
            summary=raw.get("summary"),
            short_summary=raw.get("short_summary"),
            summary_messages=tuple(_freeze(m) for m in raw.get("summary_messages") or ()),
            native=NativeCompaction.from_dict(raw["native"]) if raw.get("native") else None,
            edits=tuple(
                MessageEdit(int(edit["index"]), _freeze(edit["message"])) for edit in raw.get("edits") or ()
            ),
            details=_freeze(dict(raw.get("details") or {})),
            tokens_after=raw.get("tokens_after"),
            warning=raw.get("warning"),
        )


def record_id_for(payload: Mapping[str, Any]) -> str:
    """Content-derived record id so replays produce identical ids."""
    return "cmp_" + _digest(payload)[:24]


class CompactionStateError(ValueError):
    pass


class CompactionState:
    """Append-only compaction ledger for one session's model history."""

    def __init__(self, records: Sequence[CompactionRecord] = ()) -> None:
        self._records: List[CompactionRecord] = []
        for record in records:
            self._records.append(record)

    @property
    def records(self) -> Tuple[CompactionRecord, ...]:
        return tuple(self._records)

    @property
    def next_sequence(self) -> int:
        return len(self._records)

    def latest_boundary(self) -> Optional[CompactionRecord]:
        for record in reversed(self._records):
            if record.is_boundary:
                return record
        return None

    def latest_readable_boundary(self) -> Optional[CompactionRecord]:
        for record in reversed(self._records):
            if record.readable:
                return record
        return None

    def kept_start(self, messages: Sequence[Mapping[str, Any]]) -> int:
        boundary = self.latest_boundary()
        if boundary is None:
            return leading_system_count(messages)
        return int(boundary.first_kept_index)

    def validate(self, record: CompactionRecord, messages: Sequence[Mapping[str, Any]]) -> None:
        if record.sequence != self.next_sequence:
            raise CompactionStateError("compaction record sequence is not next")
        if record.history_length != len(messages):
            raise CompactionStateError("compaction record was made for a different history length")
        head = leading_system_count(messages)
        previous = self.latest_boundary()
        if record.is_boundary:
            first = int(record.first_kept_index)
            if first < head or first > len(messages):
                raise CompactionStateError("first_kept_index is outside the compactable range")
            if previous is not None and first < int(previous.first_kept_index):
                raise CompactionStateError("compaction boundary moved backwards")
            if first < len(messages) and is_tool_result(messages[first]):
                raise CompactionStateError("compaction boundary orphans a tool result")
            if not record.summary_messages and record.native is None:
                raise CompactionStateError("boundary record has neither summary nor native payload")
        elif not record.edits:
            raise CompactionStateError("edit record has no edits")
        floor = int(record.first_kept_index) if record.is_boundary else self.kept_start(messages)
        for edit in record.edits:
            if edit.index < floor or edit.index >= len(messages):
                raise CompactionStateError("edit index is outside the kept history")
            original = messages[edit.index]
            if role_of(edit.message) != role_of(original):
                raise CompactionStateError("edit changes message role")
            if edit.message.get("tool_call_id") != original.get("tool_call_id"):
                raise CompactionStateError("edit changes tool_call_id")
            if tool_call_ids(edit.message) != tool_call_ids(original):
                raise CompactionStateError("edit changes tool calls")

    def append(self, record: CompactionRecord, messages: Sequence[Mapping[str, Any]]) -> None:
        self.validate(record, messages)
        self._records.append(record)

    def _active_boundary(self, target: Optional[ProjectionTarget]) -> Optional[CompactionRecord]:
        for record in reversed(self._records):
            if not record.is_boundary:
                continue
            if record.native is not None and record.native.usable_for(target):
                return record
            if record.readable:
                return record
        return None

    def project(
        self,
        messages: Sequence[Mapping[str, Any]],
        target: Optional[ProjectionTarget] = None,
    ) -> List[Dict[str, Any]]:
        """Build the request view for ``target`` from the full history."""
        head = leading_system_count(messages)
        boundary = self._active_boundary(target)
        start = int(boundary.first_kept_index) if boundary is not None else head
        edits: Dict[int, Mapping[str, Any]] = {}
        for record in self._records:
            for edit in record.edits:
                edits[edit.index] = edit.message
        view: List[Dict[str, Any]] = [copy.deepcopy(dict(m)) for m in messages[:head]]
        if boundary is not None:
            if boundary.native is not None and boundary.native.usable_for(target):
                view.append(
                    {
                        "role": "user",
                        "content": boundary.summary or "",
                        NATIVE_MARKER_KEY: {"record_id": boundary.record_id, **boundary.native.to_dict()},
                    }
                )
            else:
                view.extend(copy.deepcopy(dict(m)) for m in boundary.summary_messages)
        for index in range(start, len(messages)):
            view.append(copy.deepcopy(dict(edits.get(index, messages[index]))))
        return view

    def to_list(self) -> List[Dict[str, Any]]:
        return [record.to_dict() for record in self._records]

    @classmethod
    def from_list(cls, raw: Sequence[Mapping[str, Any]]) -> "CompactionState":
        records = [CompactionRecord.from_dict(item) for item in raw]
        for expected, record in enumerate(records):
            if record.sequence != expected:
                raise CompactionStateError("compaction record sequence is not contiguous")
        return cls(records)


def strip_native_markers(messages: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Drop the replay marker key for runtimes that cannot replay it."""
    out: List[Dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        item.pop(NATIVE_MARKER_KEY, None)
        out.append(item)
    return out
