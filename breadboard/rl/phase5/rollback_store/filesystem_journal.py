from __future__ import annotations

from ._imports import *
from .models import *
from .publication import *
from .filesystem_base import *

class FilesystemRollbackJournalStore(_PinnedSignedDirectory):
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
            domain="rollback-journal",
            root_fd=root_fd,
        )

    @staticmethod
    def _head_name(rollback_id: str) -> str:
        return f"journal.{_require_id(rollback_id, 'rollback id')}.head"

    @staticmethod
    def _marker_name(rollback_id: str) -> str:
        return f"journal.{_require_id(rollback_id, 'rollback id')}.blocked"

    @staticmethod
    def _request_name(rollback_id: str) -> str:
        return f"journal.{_require_id(rollback_id, 'rollback id')}.request"

    def _request_binding_bytes(self, rollback_id: str, request_digest: str) -> bytes:
        return self._signed_bytes(
            "journal-request-binding",
            {
                "request_digest": request_digest,
                "rollback_id": rollback_id,
                "schema_version": "bb.rl.phase5.rollback-request-binding.v1",
            },
        )

    def _verify_request_binding_locked(
        self, rollback_id: str, request_digest: str
    ) -> bytes:
        name = self._request_name(rollback_id)
        raw = self._read(name)
        expected = self._request_binding_bytes(rollback_id, request_digest)
        if raw is None:
            self._quarantine(name, self._marker_name(rollback_id), rollback_id)
            raise RollbackCorruptionError("rollback journal request binding is missing")
        try:
            self._verify_signed(raw, "journal-request-binding")
        except (RollbackValidationError, RollbackCorruptionError) as error:
            self._quarantine(name, self._marker_name(rollback_id), rollback_id)
            raise RollbackCorruptionError(
                "rollback journal request binding was quarantined"
            ) from error
        if raw != expected:
            raise RollbackIdempotencyConflict(
                "rollback id is already bound to a different request digest"
            )
        return raw

    def _payload_bytes(self, ref: RollbackPayloadRef, payload: bytes) -> bytes:
        return self._signed_bytes(
            "journal-payload",
            {
                "payload_base64": base64.b64encode(payload).decode("ascii"),
                "payload_ref": ref.canonical_object(),
                "schema_version": "bb.rl.phase5.rollback-payload-object.v1",
            },
        )

    def _decode_payload_locked(
        self,
        ref: RollbackPayloadRef,
        *,
        leaf_errors: tuple[RollbackLeafError, ...] = (),
        prior_receipt_digests: tuple[str, ...] = (),
        prior_receipt_refs: tuple[RollbackPayloadRef, ...] = (),
        request: Mapping[str, Any] | None = None,
    ) -> bytes:
        raw = self._read(ref.relative_path)
        try:
            if raw is None:
                raise RollbackCorruptionError(
                    "authoritative rollback payload is missing"
                )
            value = _require_object(
                self._verify_signed(raw, "journal-payload"),
                frozenset(("payload_base64", "payload_ref", "schema_version")),
                "rollback payload object",
            )
            if (
                value["schema_version"] != "bb.rl.phase5.rollback-payload-object.v1"
                or _payload_ref_from_object(value["payload_ref"]) != ref
                or type(value["payload_base64"]) is not str
            ):
                raise RollbackCorruptionError(
                    "authoritative rollback payload binding mismatch"
                )
            try:
                payload = base64.b64decode(value["payload_base64"], validate=True)
            except (ValueError, binascii.Error) as error:
                raise RollbackCorruptionError(
                    "authoritative rollback payload encoding is invalid"
                ) from error
            if ref.kind is RollbackPayloadKind.REQUEST:
                _validate_request_payload(payload, ref.rollback_id, ref.request_digest)
            else:
                _validate_receipt_payload(
                    payload,
                    ref=ref,
                    leaf_errors=leaf_errors,
                    prior_receipt_digests=prior_receipt_digests,
                    prior_receipt_refs=prior_receipt_refs,
                    request=(
                        request
                        if request is not None
                        else (_ for _ in ()).throw(
                            RollbackCorruptionError(
                                "receipt payload request context is missing"
                            )
                        )
                    ),
                    store_root=self.root,
                )
            return payload
        except (
            RollbackValidationError,
            RollbackCorruptionError,
            OSError,
        ) as error:
            self._quarantine(
                ref.relative_path,
                self._marker_name(ref.rollback_id),
                ref.rollback_id,
            )
            raise RollbackCorruptionError(
                "authoritative rollback payload was quarantined"
            ) from error

    def _store_payload_locked(
        self,
        ref: RollbackPayloadRef,
        payload: bytes,
        *,
        leaf_errors: tuple[RollbackLeafError, ...] = (),
        prior_receipt_digests: tuple[str, ...] = (),
        prior_receipt_refs: tuple[RollbackPayloadRef, ...] = (),
        request: Mapping[str, Any] | None = None,
    ) -> None:
        if ref.kind is RollbackPayloadKind.REQUEST:
            _validate_request_payload(payload, ref.rollback_id, ref.request_digest)
        else:
            _validate_receipt_payload(
                payload,
                ref=ref,
                leaf_errors=leaf_errors,
                prior_receipt_digests=prior_receipt_digests,
                prior_receipt_refs=prior_receipt_refs,
                request=(
                    request
                    if request is not None
                    else (_ for _ in ()).throw(
                        RollbackValidationError(
                            "receipt payload request context is missing"
                        )
                    )
                ),
                store_root=self.root,
            )
        existing = self._read(ref.relative_path)
        expected = self._payload_bytes(ref, payload)
        if existing is None:
            self._create_immutable(ref.relative_path, expected)
        elif existing != expected:
            self._decode_payload_locked(
                ref,
                leaf_errors=leaf_errors,
                prior_receipt_digests=prior_receipt_digests,
                prior_receipt_refs=prior_receipt_refs,
                request=request,
            )
            raise RollbackIdempotencyConflict(
                "authoritative rollback payload bytes diverge"
            )
        else:
            self._decode_payload_locked(
                ref,
                leaf_errors=leaf_errors,
                prior_receipt_digests=prior_receipt_digests,
                prior_receipt_refs=prior_receipt_refs,
                request=request,
            )

    def _validate_payload_joins_locked(self, record: RollbackJournalRecord) -> None:
        request_raw = self._decode_payload_locked(record.request_payload_ref)
        request = _validate_request_payload(
            request_raw, record.rollback_id, record.request_digest
        )
        prior_digests: list[str] = []
        prior_refs: list[RollbackPayloadRef] = []
        for receipt in record.phase_receipts:
            for ref in receipt.receipt_refs:
                self._decode_payload_locked(
                    ref,
                    leaf_errors=receipt.leaf_errors,
                    prior_receipt_digests=tuple(prior_digests),
                    prior_receipt_refs=tuple(prior_refs),
                    request=request,
                )
            prior_digests.extend(receipt.receipt_digests)
            prior_refs.extend(receipt.receipt_refs)

    @staticmethod
    def _history_name(record: RollbackJournalRecord) -> str:
        return f"journal.{record.rollback_id}.g{record.generation:020d}.{record.digest[7:]}.history"

    @staticmethod
    def _commit_name(record: RollbackJournalRecord) -> str:
        return (
            f"journal.{record.rollback_id}.g{record.generation:020d}."
            f"{record.digest[7:]}.commit"
        )

    @staticmethod
    def _validate_journal_transition(
        chain: tuple[tuple[RollbackJournalRecord, bytes], ...],
        index: int,
    ) -> None:
        record = chain[index][0]
        if index == 0:
            return
        previous = chain[index - 1][0]
        if record.generation != previous.generation + 1:
            raise RollbackCorruptionError(
                "rollback journal publication generations are not adjacent"
            )
        if record.terminal_quarantine_refs == previous.terminal_quarantine_refs:
            if (
                record.revision != previous.revision + 1
                or record.phase_receipts[:-1] != previous.phase_receipts
            ):
                raise RollbackCorruptionError(
                    "rollback journal semantic transition is invalid"
                )
            return
        if (
            len(record.terminal_quarantine_refs)
            != len(previous.terminal_quarantine_refs) + 1
            or record.terminal_quarantine_refs[:-1] != previous.terminal_quarantine_refs
        ):
            raise RollbackCorruptionError(
                "rollback journal terminal quarantine chain diverged"
            )
        ref = record.terminal_quarantine_refs[-1]
        if (
            ref.successor_generation != previous.generation
            or ref.successor_record_digest != previous.digest
            or ref.predecessor_generation >= ref.successor_generation
        ):
            raise RollbackCorruptionError(
                "rollback journal terminal quarantine successor binding is invalid"
            )
        predecessor = chain[ref.predecessor_generation - 1][0]
        if (
            predecessor.digest != ref.predecessor_record_digest
            or record.rollback_id != predecessor.rollback_id
            or record.request_digest != predecessor.request_digest
            or record.request_payload_ref != predecessor.request_payload_ref
            or record.revision != predecessor.revision
            or record.phase is not predecessor.phase
            or record.phase_receipts != predecessor.phase_receipts
            or record.generation != ref.successor_generation + 1
        ):
            raise RollbackCorruptionError(
                "rollback journal terminal restoration payload is invalid"
            )

    def _read_exact_authority(self, name: str) -> bytes | None:
        self._validate_root()
        try:
            held = _HeldStoreFile.capture(self, name)
        except FileNotFoundError:
            return None
        try:
            held.revalidate(self)
            return held.raw
        finally:
            held.close()

    def _committed_history_locked(
        self, rollback_id: str
    ) -> tuple[tuple[RollbackJournalRecord, bytes], ...]:
        marker = self._marker_name(rollback_id)
        if self._blocked(marker):
            raise RollbackCorruptionError("rollback journal is quarantined")
        head_name = self._head_name(rollback_id)
        suspect = head_name
        try:
            head_raw = self._read_exact_authority(head_name)
            if head_raw is None:
                return ()
            head_record = self._decode(head_raw)
            if (
                head_record.rollback_id != rollback_id
                or head_record.generation > _MAX_ROLLBACK_HISTORY_GENERATIONS
            ):
                raise RollbackCorruptionError(
                    "rollback journal head authority is invalid"
                )
            suspect = self._history_name(head_record)
            history_raw = self._read_exact_authority(suspect)
            if history_raw != head_raw:
                raise RollbackCorruptionError(
                    "rollback journal head and history diverged"
                )
            commit_name = self._commit_name(head_record)
            commit_raw = self._read_exact_authority(commit_name)
            head_is_committed = commit_raw is not None
            if commit_raw is not None:
                suspect = commit_name
                self._verify_commit(
                    commit_raw,
                    identity=rollback_id,
                    generation=head_record.generation,
                    record_digest=head_record.digest,
                )

            reverse_chain: list[tuple[RollbackJournalRecord, bytes]] = []
            aggregate_bytes = 0

            def append_record(
                record: RollbackJournalRecord,
                raw: bytes,
            ) -> None:
                nonlocal aggregate_bytes
                if (
                    len(reverse_chain) >= _MAX_ROLLBACK_HISTORY_GENERATIONS
                    or aggregate_bytes + len(raw) > _MAX_ROLLBACK_HISTORY_BYTES
                ):
                    raise RollbackCorruptionError(
                        "rollback journal committed history bound is exhausted"
                    )
                aggregate_bytes += len(raw)
                reverse_chain.append((record, raw))

            current = head_record
            if head_is_committed:
                append_record(head_record, head_raw)
            while current.generation > 1:
                generation = current.generation - 1
                digest = current.previous_record_digest
                if digest is None:
                    raise RollbackCorruptionError(
                        "rollback journal predecessor authority is missing"
                    )
                suspect = self._journal_version_name(
                    rollback_id,
                    generation,
                    digest,
                    "history",
                )
                raw = self._read_exact_authority(suspect)
                if raw is None:
                    raise RollbackCorruptionError(
                        "rollback journal history disappeared"
                    )
                record = self._decode(raw)
                if (
                    record.rollback_id != rollback_id
                    or record.generation != generation
                    or record.digest != digest
                ):
                    raise RollbackCorruptionError(
                        "rollback journal history identity mismatch"
                    )
                commit_name = self._journal_version_name(
                    rollback_id,
                    generation,
                    digest,
                    "commit",
                )
                suspect = commit_name
                commit_raw = self._read_exact_authority(commit_name)
                if commit_raw is None:
                    raise RollbackCorruptionError("rollback journal commit disappeared")
                self._verify_commit(
                    commit_raw,
                    identity=rollback_id,
                    generation=generation,
                    record_digest=digest,
                )
                append_record(record, raw)
                current = record
            if current.previous_record_digest is not None:
                raise RollbackCorruptionError(
                    "rollback journal initial predecessor is invalid"
                )
            chain = tuple(reversed(reverse_chain))
            for index, (record, _) in enumerate(chain):
                expected_previous = None if index == 0 else chain[index - 1][0].digest
                if record.previous_record_digest != expected_previous:
                    suspect = self._history_name(record)
                    raise RollbackCorruptionError(
                        "rollback journal committed chain diverged"
                    )
                self._validate_journal_transition(chain, index)
            return chain
        except (
            OSError,
            RollbackValidationError,
            RollbackCorruptionError,
        ) as error:
            self._quarantine(suspect, marker, rollback_id)
            if suspect.endswith(".history"):
                message = "rollback journal history was quarantined"
            elif suspect.endswith(".commit"):
                message = "rollback journal commit was quarantined"
            else:
                message = "rollback journal committed authority was quarantined"
            raise RollbackCorruptionError(message) from error

    def _validate_terminal_ref_join_locked(
        self,
        record: RollbackJournalRecord,
    ) -> None:
        rollback_id = record.rollback_id
        try:
            anchors = self._terminal_quarantine_anchors()
            indexed_refs = tuple(
                ref for ref in anchors.values() if ref.rollback_id == rollback_id
            )
            expected_indexed_refs = tuple(
                sorted(
                    record.terminal_quarantine_refs,
                    key=self._terminal_anchor_key,
                )
            )
            if indexed_refs != expected_indexed_refs:
                raise RollbackCorruptionError(
                    "terminal rollback journal and anchor index diverged"
                )
            for ref in record.terminal_quarantine_refs:
                successor_name, tombstone_name = self._rollback_quarantine_names(
                    ref.transaction_id,
                    rollback_id,
                    ref.successor_record_digest,
                )
                if (
                    ref.successor_name != successor_name
                    or ref.tombstone_name != tombstone_name
                ):
                    raise RollbackCorruptionError(
                        "terminal rollback pair name binding is invalid"
                    )
                payload, successor_record, successor_raw = self._terminal_pair_evidence(
                    ref,
                    {
                        "successor": (successor_name, 0),
                        "tombstone": (tombstone_name, 0),
                    },
                )
                self._validate_live_terminal_anchor(
                    ref,
                    payload,
                    successor_record,
                    successor_raw,
                )
        except (
            OSError,
            RollbackValidationError,
            RollbackCorruptionError,
        ) as error:
            self._block_rollback_id(rollback_id)
            raise RollbackCorruptionError(
                "terminal rollback authority was quarantined"
            ) from error

    def _decode(self, raw: bytes) -> RollbackJournalRecord:
        return _journal_from_object(self._verify_signed(raw, "journal-record"))

    def _load_locked(
        self, rollback_id: str
    ) -> tuple[RollbackJournalRecord | None, bytes | None]:
        marker = self._marker_name(rollback_id)
        history = self._committed_history_locked(rollback_id)
        head = self._head_name(rollback_id)
        try:
            raw = self._read_exact_authority(head)
        except (RollbackCorruptionError, OSError) as error:
            self._quarantine(head, marker, rollback_id)
            raise RollbackCorruptionError("rollback journal was quarantined") from error
        if not history:
            if raw is not None:
                try:
                    record = self._decode(raw)
                    if (
                        record.rollback_id != rollback_id
                        or self._read_exact_authority(self._history_name(record)) != raw
                    ):
                        raise RollbackCorruptionError(
                            "uncommitted rollback journal head is invalid"
                        )
                except (
                    RollbackValidationError,
                    RollbackCorruptionError,
                    OSError,
                ) as error:
                    self._quarantine(head, marker, rollback_id)
                    raise RollbackCorruptionError(
                        "rollback journal was quarantined"
                    ) from error
                os.unlink(head, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            return None, None
        current, committed_raw = history[-1]
        self._verify_request_binding_locked(rollback_id, current.request_digest)
        if raw is None:
            self._quarantine(head, marker, rollback_id)
            raise RollbackCorruptionError("rollback journal committed head is missing")
        try:
            head_record = self._decode(raw)
        except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
            self._quarantine(head, marker, rollback_id)
            raise RollbackCorruptionError("rollback journal was quarantined") from error
        if head_record == current and raw == committed_raw:
            self._validate_terminal_ref_join_locked(current)
            self._validate_payload_joins_locked(current)
            return current, raw
        if (
            head_record.generation == current.generation + 1
            and head_record.previous_record_digest == current.digest
            and self._read_exact_authority(self._history_name(head_record)) == raw
            and self._read_exact_authority(self._commit_name(head_record)) is None
        ):
            self._validate_terminal_ref_join_locked(current)
            self._validate_payload_joins_locked(current)
            self._replace(head, committed_raw, raw)
            return current, committed_raw
        self._quarantine(head, marker, rollback_id)
        raise RollbackCorruptionError(
            "rollback journal signed head replay was quarantined"
        )

    def _persist_locked(
        self, record: RollbackJournalRecord, old_payload: bytes | None
    ) -> None:
        signed = self._signed_bytes("journal-record", record.canonical_object())
        self._publish_versioned(
            head_name=self._head_name(record.rollback_id),
            history_name=self._history_name(record),
            commit_name=self._commit_name(record),
            identity=record.rollback_id,
            generation=record.generation,
            record_digest=record.digest,
            signed_record=signed,
            old_head=old_payload,
        )

    def prepare(
        self,
        rollback_id: str,
        request_digest: str,
        request_payload: bytes,
    ) -> RollbackJournalRecord:
        source_capsules: list[_PinnedImmutableSource] = []
        try:
            _validate_request_payload_with_capsules(
                request_payload,
                rollback_id,
                request_digest,
                source_capsules,
            )
            return self._prepare_captured(
                rollback_id,
                request_digest,
                request_payload,
                source_capsules,
            )
        finally:
            for capsule in reversed(source_capsules):
                capsule.close()

    def _prepare_captured(
        self,
        rollback_id: str,
        request_digest: str,
        request_payload: bytes,
        source_capsules: Sequence[_PinnedImmutableSource],
    ) -> RollbackJournalRecord:
        _require_id(rollback_id, "rollback id")
        _require_digest(request_digest, "rollback request digest")
        request_ref = RollbackPayloadRef(
            rollback_id,
            request_digest,
            request_digest,
            RollbackPayloadKind.REQUEST,
            RollbackPhase.PREPARED,
            1,
            0,
            _payload_relative_path(
                rollback_id,
                RollbackPayloadKind.REQUEST,
                RollbackPhase.PREPARED,
                1,
                0,
                request_digest,
            ),
        )
        expected_binding = self._request_binding_bytes(rollback_id, request_digest)
        with self._exclusive():
            current, old_payload = self._load_locked(rollback_id)
            binding_name = self._request_name(rollback_id)
            existing_binding = self._read(binding_name)
            if existing_binding is not None:
                try:
                    self._verify_signed(existing_binding, "journal-request-binding")
                except (
                    RollbackValidationError,
                    RollbackCorruptionError,
                ) as error:
                    self._quarantine(
                        binding_name,
                        self._marker_name(rollback_id),
                        rollback_id,
                    )
                    raise RollbackCorruptionError(
                        "rollback journal request binding was quarantined"
                    ) from error
                if existing_binding != expected_binding:
                    raise RollbackIdempotencyConflict(
                        "rollback id is already bound to a different request digest"
                    )
            _package_callable("_revalidate_source_capsules", _revalidate_source_capsules)(source_capsules)
            if current is not None:
                if (
                    current.request_payload_ref != request_ref
                    or self._decode_payload_locked(request_ref) != request_payload
                ):
                    raise RollbackIdempotencyConflict(
                        "rollback id is already bound to different request bytes"
                    )
                return current
            with self._publication_transaction(
                lambda: _package_callable("_revalidate_source_capsules", _revalidate_source_capsules)(source_capsules)
            ):
                if existing_binding is None:
                    self._create_immutable(binding_name, expected_binding)
                self._store_payload_locked(request_ref, request_payload)
                record = RollbackJournalRecord(
                    rollback_id,
                    request_digest,
                    request_ref,
                    1,
                    0,
                    RollbackPhase.PREPARED,
                    (),
                    None,
                )
                self._persist_locked(record, old_payload)
                return record

    def get(self, rollback_id: str) -> RollbackJournalRecord | None:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            return self._load_locked(rollback_id)[0]

    def get_request_ref(self, rollback_id: str) -> RollbackPayloadRef:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            current, _ = self._load_locked(rollback_id)
            if current is None:
                raise RollbackConflictError("rollback journal is not prepared")
            return current.request_payload_ref

    def get_request(self, rollback_id: str) -> bytes:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            current, _ = self._load_locked(rollback_id)
            if current is None:
                raise RollbackConflictError("rollback journal is not prepared")
            return self._decode_payload_locked(current.request_payload_ref)

    def advance(
        self,
        rollback_id: str,
        *,
        expected_generation: int,
        expected_revision: int,
        phase: RollbackPhase,
        receipt_digests: tuple[str, ...],
        receipt_payloads: tuple[bytes, ...],
        leaf_errors: tuple[RollbackLeafError, ...] = (),
    ) -> RollbackJournalRecord:
        source_capsules: list[_PinnedImmutableSource] = []
        try:
            return self._advance_captured(
                rollback_id,
                expected_generation=expected_generation,
                expected_revision=expected_revision,
                phase=phase,
                receipt_digests=receipt_digests,
                receipt_payloads=receipt_payloads,
                leaf_errors=leaf_errors,
                source_capsules=source_capsules,
            )
        finally:
            for capsule in reversed(source_capsules):
                capsule.close()

    def _advance_captured(
        self,
        rollback_id: str,
        *,
        expected_generation: int,
        expected_revision: int,
        phase: RollbackPhase,
        receipt_digests: tuple[str, ...],
        receipt_payloads: tuple[bytes, ...],
        leaf_errors: tuple[RollbackLeafError, ...] = (),
        source_capsules: list[_PinnedImmutableSource],
    ) -> RollbackJournalRecord:
        _require_id(rollback_id, "rollback id")
        _require_int(expected_generation, "expected journal generation", minimum=1)
        _require_int(expected_revision, "expected journal revision")
        if type(receipt_digests) is not tuple or type(receipt_payloads) is not tuple:
            raise RollbackValidationError(
                "receipt digests and payloads must be exact tuples"
            )
        if (
            not receipt_digests
            or len(receipt_digests) != len(receipt_payloads)
            or any(type(payload) is not bytes for payload in receipt_payloads)
        ):
            raise RollbackValidationError(
                "advance requires one exact payload per receipt digest"
            )
        if len(receipt_payloads) > _MAX_RECEIPT_PAYLOADS:
            raise RollbackValidationError(
                "phase receipt payload count exceeds fixed bound"
            )
        if (
            sum(len(payload) for payload in receipt_payloads)
            > _MAX_AGGREGATE_RECEIPT_PAYLOAD_BYTES
        ):
            raise RollbackValidationError(
                "aggregate phase receipt payload bytes exceed fixed bound"
            )
        for digest, payload in zip(receipt_digests, receipt_payloads, strict=True):
            _require_digest(digest, "phase receipt digest")
            if canonical_digest(payload) != digest:
                raise RollbackValidationError("phase receipt payload digest mismatch")
        with self._exclusive():
            current, old_payload = self._load_locked(rollback_id)
            if current is None:
                raise RollbackConflictError("rollback journal is not prepared")
            replay = (
                current.generation == expected_generation + 1
                and current.revision == expected_revision + 1
                and current.phase is phase
                and bool(current.phase_receipts)
            )
            if not replay and (
                current.generation != expected_generation
                or current.revision != expected_revision
            ):
                raise RollbackConflictError(
                    "rollback journal generation/revision compare-and-swap failed"
                )
            prior_receipts = (
                current.phase_receipts[:-1] if replay else current.phase_receipts
            )
            prior_digests = tuple(
                digest
                for prior_receipt in prior_receipts
                for digest in prior_receipt.receipt_digests
            )
            prior_refs = tuple(
                ref
                for prior_receipt in prior_receipts
                for ref in prior_receipt.receipt_refs
            )
            request_raw = self._decode_payload_locked(current.request_payload_ref)
            request = _validate_request_payload_with_capsules(
                request_raw,
                current.rollback_id,
                current.request_digest,
                source_capsules,
            )
            refs = tuple(
                RollbackPayloadRef(
                    current.rollback_id,
                    current.request_digest,
                    digest,
                    RollbackPayloadKind.PHASE_RECEIPT,
                    phase,
                    expected_generation + 1,
                    expected_revision + 1,
                    _payload_relative_path(
                        current.rollback_id,
                        RollbackPayloadKind.PHASE_RECEIPT,
                        phase,
                        expected_generation + 1,
                        expected_revision + 1,
                        digest,
                    ),
                )
                for digest in receipt_digests
            )
            receipt = RollbackPhaseReceipt(phase, receipt_digests, refs, leaf_errors)
            for ref, payload in zip(refs, receipt_payloads, strict=True):
                _validate_receipt_payload(
                    payload,
                    ref=ref,
                    leaf_errors=leaf_errors,
                    prior_receipt_digests=prior_digests,
                    prior_receipt_refs=prior_refs,
                    request=request,
                    store_root=self.root,
                )
            if replay:
                if current.phase_receipts[-1] != receipt:
                    raise RollbackIdempotencyConflict(
                        "rollback phase replay has divergent receipt bindings"
                    )
                for ref, payload in zip(refs, receipt_payloads, strict=True):
                    if (
                        self._decode_payload_locked(
                            ref,
                            leaf_errors=leaf_errors,
                            prior_receipt_digests=prior_digests,
                            prior_receipt_refs=prior_refs,
                            request=request,
                        )
                        != payload
                    ):
                        raise RollbackIdempotencyConflict(
                            "rollback phase replay has divergent receipt bytes"
                        )
                return current
            if current.phase in _TERMINAL_PHASES:
                raise RollbackConflictError("terminal rollback journal is absorbing")
            if phase is RollbackPhase.QUARANTINED:
                if not leaf_errors:
                    raise RollbackValidationError(
                        "terminal quarantine requires exact leaf errors"
                    )
            else:
                if leaf_errors:
                    raise RollbackValidationError(
                        "only terminal quarantine may persist leaf errors"
                    )
                expected_phase = _PHASE_ORDER[_PHASE_ORDER.index(current.phase) + 1]
                if phase is not expected_phase:
                    raise RollbackConflictError(
                        "rollback journal phase advance is not monotonic"
                    )
            if set(receipt_digests) & set(prior_digests):
                raise RollbackIdempotencyConflict(
                    "receipt payload digest is already committed"
                )
            record = RollbackJournalRecord(
                current.rollback_id,
                current.request_digest,
                current.request_payload_ref,
                current.generation + 1,
                current.revision + 1,
                phase,
                (*current.phase_receipts, receipt),
                current.digest,
                current.terminal_quarantine_refs,
            )
            projected_signed_record = self._signed_bytes(
                "journal-record", record.canonical_object()
            )
            if len(projected_signed_record) > _MAX_RECORD_BYTES:
                raise RollbackValidationError(
                    "projected rollback journal exceeds fixed size bound"
                )
            _package_callable("_revalidate_source_capsules", _revalidate_source_capsules)(source_capsules)
            with self._publication_transaction(
                lambda: _package_callable("_revalidate_source_capsules", _revalidate_source_capsules)(source_capsules)
            ):
                for ref, payload in zip(refs, receipt_payloads, strict=True):
                    self._store_payload_locked(
                        ref,
                        payload,
                        leaf_errors=leaf_errors,
                        prior_receipt_digests=prior_digests,
                        prior_receipt_refs=prior_refs,
                        request=request,
                    )
                self._persist_locked(record, old_payload)
                return record

    def _receipt_context_locked(
        self, record: RollbackJournalRecord, receipt_digest: str
    ) -> tuple[
        RollbackPayloadRef,
        tuple[RollbackLeafError, ...],
        tuple[str, ...],
        tuple[RollbackPayloadRef, ...],
    ]:
        _require_digest(receipt_digest, "phase receipt digest")
        prior: list[str] = []
        prior_refs: list[RollbackPayloadRef] = []
        for receipt in record.phase_receipts:
            for ref in receipt.receipt_refs:
                if ref.payload_digest == receipt_digest:
                    return (
                        ref,
                        receipt.leaf_errors,
                        tuple(prior),
                        tuple(prior_refs),
                    )
            prior.extend(receipt.receipt_digests)
            prior_refs.extend(receipt.receipt_refs)
        raise RollbackConflictError(
            "receipt payload is not committed by this rollback journal"
        )

    def get_receipt_ref(
        self, rollback_id: str, receipt_digest: str
    ) -> RollbackPayloadRef:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            current, _ = self._load_locked(rollback_id)
            if current is None:
                raise RollbackConflictError("rollback journal is not prepared")
            ref, _, _, _ = self._receipt_context_locked(current, receipt_digest)
            return ref

    def get_receipt_payload(self, rollback_id: str, receipt_digest: str) -> bytes:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            current, _ = self._load_locked(rollback_id)
            if current is None:
                raise RollbackConflictError("rollback journal is not prepared")
            ref, leaf_errors, prior_digests, prior_refs = self._receipt_context_locked(
                current, receipt_digest
            )
            request_raw = self._decode_payload_locked(current.request_payload_ref)
            request = _validate_request_payload(
                request_raw, current.rollback_id, current.request_digest
            )
            return self._decode_payload_locked(
                ref,
                leaf_errors=leaf_errors,
                prior_receipt_digests=prior_digests,
                prior_receipt_refs=prior_refs,
                request=request,
            )

    def history(self, rollback_id: str) -> tuple[RollbackJournalRecord, ...]:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            current, _ = self._load_locked(rollback_id)
            if current is None:
                return ()
            return tuple(
                record for record, _ in self._committed_history_locked(rollback_id)
            )

__all__ = ['FilesystemRollbackJournalStore']
