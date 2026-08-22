from __future__ import annotations

from ._imports import *
from .models import *
from .publication import *
from .filesystem_base import *

class FilesystemActiveApprovedTupleStore(_PinnedSignedDirectory):
    _HEAD = "active-approved.head"
    _MARKER = "active-approved.blocked"

    def __init__(
        self,
        root: str | Path,
        *,
        authority_key: bytes,
        root_fd: int | None = None,
    ) -> None:
        super().__init__(
            root,
            authority_key=authority_key,
            domain="active-approved-tuple",
            root_fd=root_fd,
        )

    @staticmethod
    def _history_name(state: ActiveApprovedTupleState) -> str:
        return f"active-approved.g{state.generation:020d}.{state.digest[7:]}.history"

    @staticmethod
    def _commit_name(state: ActiveApprovedTupleState) -> str:
        return f"active-approved.g{state.generation:020d}.{state.digest[7:]}.commit"

    @staticmethod
    def _operation_name(operation_id: str) -> str:
        return f"active-operation.{_require_id(operation_id, 'active tuple operation id')}.request"

    def _committed_history_locked(
        self,
    ) -> tuple[tuple[ActiveApprovedTupleState, bytes], ...]:
        if self._blocked(self._MARKER):
            raise RollbackCorruptionError("active approved tuple is quarantined")
        history_pattern = re.compile(
            r"^active-approved\.g(\d{20})\.([0-9a-f]{64})\.history$"
        )
        commit_pattern = re.compile(
            r"^active-approved\.g(\d{20})\.([0-9a-f]{64})\.commit$"
        )
        histories: dict[
            tuple[int, str], tuple[ActiveApprovedTupleState, bytes, str]
        ] = {}
        for name in sorted(
            item
            for item in self._bounded_root_names()
            if item.startswith("active-approved.g") and item.endswith(".history")
        ):
            try:
                match = history_pattern.fullmatch(name)
                if match is None:
                    raise RollbackCorruptionError(
                        "active tuple history name is invalid"
                    )
                raw = self._read(name)
                if raw is None:
                    raise RollbackCorruptionError("active tuple history disappeared")
                state = self._decode(raw)
                key = (int(match.group(1)), "sha256:" + match.group(2))
                if (
                    state.generation != key[0]
                    or state.digest != key[1]
                    or key in histories
                ):
                    raise RollbackCorruptionError(
                        "active tuple history identity mismatch"
                    )
                histories[key] = (state, raw, name)
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(name, self._MARKER, "active-approved")
                raise RollbackCorruptionError(
                    "active tuple history was quarantined"
                ) from error
        committed: dict[int, tuple[ActiveApprovedTupleState, bytes]] = {}
        for name in sorted(
            item
            for item in self._bounded_root_names()
            if item.startswith("active-approved.g") and item.endswith(".commit")
        ):
            try:
                match = commit_pattern.fullmatch(name)
                if match is None:
                    raise RollbackCorruptionError("active tuple commit name is invalid")
                generation = int(match.group(1))
                digest = "sha256:" + match.group(2)
                raw = self._read(name)
                if raw is None:
                    raise RollbackCorruptionError("active tuple commit disappeared")
                self._verify_commit(
                    raw,
                    identity="active-approved",
                    generation=generation,
                    record_digest=digest,
                )
                history = histories.get((generation, digest))
                if history is None or generation in committed:
                    raise RollbackCorruptionError(
                        "active tuple committed tip is invalid"
                    )
                committed[generation] = (history[0], history[1])
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(name, self._MARKER, "active-approved")
                raise RollbackCorruptionError(
                    "active tuple commit was quarantined"
                ) from error
        if not committed:
            return ()
        generations = tuple(sorted(committed))
        if generations != tuple(range(1, generations[-1] + 1)):
            self._quarantine(self._HEAD, self._MARKER, "active-approved")
            raise RollbackCorruptionError("active tuple committed history has a gap")
        chain = tuple(committed[generation] for generation in generations)
        for index, (state, _) in enumerate(chain):
            expected_previous = None if index == 0 else chain[index - 1][0].digest
            if state.previous_state_digest != expected_previous:
                self._quarantine(
                    self._history_name(state), self._MARKER, "active-approved"
                )
                raise RollbackCorruptionError("active tuple committed chain diverged")
            binding_name = self._operation_name(state.operation_id)
            expected_binding = self._signed_bytes(
                "active-operation-binding",
                {
                    "approved_tuple_digest": state.approved_tuple.tuple_digest,
                    "expected_generation": state.generation - 1
                    if state.generation > 1
                    else None,
                    "operation_id": state.operation_id,
                    "schema_version": "bb.rl.phase5.active-operation-binding.v1",
                },
            )
            try:
                binding = self._read(binding_name)
                if binding is None:
                    raise RollbackCorruptionError("active operation binding is missing")
                self._verify_signed(binding, "active-operation-binding")
                if binding != expected_binding:
                    raise RollbackCorruptionError("active operation binding diverged")
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(binding_name, self._MARKER, "active-approved")
                raise RollbackCorruptionError(
                    "active operation binding was quarantined"
                ) from error
        return chain

    def _decode(self, raw: bytes) -> ActiveApprovedTupleState:
        return _active_state_from_object(self._verify_signed(raw, "active-state"))

    def _load_locked(self) -> tuple[ActiveApprovedTupleState | None, bytes | None]:
        history = self._committed_history_locked()
        try:
            raw = self._read(self._HEAD)
        except (RollbackCorruptionError, OSError) as error:
            self._quarantine(self._HEAD, self._MARKER, "active-approved")
            raise RollbackCorruptionError(
                "active approved tuple was quarantined"
            ) from error
        if not history:
            if raw is not None:
                try:
                    state = self._decode(raw)
                    if self._read(self._history_name(state)) != raw:
                        raise RollbackCorruptionError(
                            "uncommitted active tuple head is invalid"
                        )
                except (
                    RollbackValidationError,
                    RollbackCorruptionError,
                    OSError,
                ) as error:
                    self._quarantine(self._HEAD, self._MARKER, "active-approved")
                    raise RollbackCorruptionError(
                        "active approved tuple was quarantined"
                    ) from error
                os.unlink(self._HEAD, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            return None, None
        current, committed_raw = history[-1]
        if raw is None:
            self._quarantine(self._HEAD, self._MARKER, "active-approved")
            raise RollbackCorruptionError(
                "active approved tuple committed head is missing"
            )
        try:
            head_state = self._decode(raw)
        except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
            self._quarantine(self._HEAD, self._MARKER, "active-approved")
            raise RollbackCorruptionError(
                "active approved tuple was quarantined"
            ) from error
        if head_state == current and raw == committed_raw:
            return current, raw
        if (
            head_state.generation == current.generation + 1
            and head_state.previous_state_digest == current.digest
            and self._read(self._history_name(head_state)) == raw
            and self._read(self._commit_name(head_state)) is None
        ):
            self._replace(self._HEAD, committed_raw, raw)
            return current, committed_raw
        self._quarantine(self._HEAD, self._MARKER, "active-approved")
        raise RollbackCorruptionError("active tuple signed head replay was quarantined")

    def get(self) -> ActiveApprovedTupleState | None:
        with self._exclusive():
            return self._load_locked()[0]

    def compare_and_swap(
        self,
        expected_generation: int | None,
        approved_tuple: ActiveApprovedTuple,
        operation_id: str,
    ) -> ActiveApprovedTupleState:
        if expected_generation is not None:
            _require_int(expected_generation, "expected active generation", minimum=1)
        if type(approved_tuple) is not ActiveApprovedTuple:
            raise RollbackValidationError("active approved tuple must be exact")
        _require_id(operation_id, "active tuple operation id")
        binding = self._signed_bytes(
            "active-operation-binding",
            {
                "approved_tuple_digest": approved_tuple.tuple_digest,
                "expected_generation": expected_generation,
                "operation_id": operation_id,
                "schema_version": "bb.rl.phase5.active-operation-binding.v1",
            },
        )
        with self._exclusive():
            current, old_payload = self._load_locked()
            binding_name = self._operation_name(operation_id)
            existing_binding = self._read(binding_name)
            if existing_binding is not None:
                try:
                    self._verify_signed(existing_binding, "active-operation-binding")
                except (RollbackValidationError, RollbackCorruptionError) as error:
                    self._quarantine(binding_name, self._MARKER, "active-approved")
                    raise RollbackCorruptionError(
                        "active operation binding was quarantined"
                    ) from error
                if existing_binding != binding:
                    raise RollbackIdempotencyConflict(
                        "active tuple operation id is bound to a different request"
                    )
                for state, _ in self._committed_history_locked():
                    if state.operation_id == operation_id:
                        return state
            actual_generation = current.generation if current is not None else None
            if actual_generation != expected_generation:
                raise RollbackConflictError(
                    "active approved tuple generation compare-and-swap failed"
                )
            if existing_binding is None:
                self._create_immutable(binding_name, binding)
            state = ActiveApprovedTupleState(
                generation=1 if current is None else current.generation + 1,
                approved_tuple=approved_tuple,
                operation_id=operation_id,
                previous_state_digest=current.digest if current is not None else None,
            )
            signed = self._signed_bytes("active-state", state.canonical_object())
            self._publish_versioned(
                head_name=self._HEAD,
                history_name=self._history_name(state),
                commit_name=self._commit_name(state),
                identity="active-approved",
                generation=state.generation,
                record_digest=state.digest,
                signed_record=signed,
                old_head=old_payload,
            )
            return state

    def history(self) -> tuple[ActiveApprovedTupleHistoryEntry, ...]:
        with self._exclusive():
            current, _ = self._load_locked()
            if current is None:
                return ()
            return tuple(
                ActiveApprovedTupleHistoryEntry(state, state.digest)
                for state, _ in self._committed_history_locked()
            )

__all__ = ['FilesystemActiveApprovedTupleStore']
