from __future__ import annotations

from ._imports import *
from .models import *
from .publication import *
from .filesystem_base import *

@dataclass(frozen=True, slots=True)
class _QuarantineOperation:
    rollback_id: str
    cause_digest: str
    root_digests: tuple[str, ...]
    affected_digests: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_id(self.rollback_id, "rollback id")
        _require_digest(self.cause_digest, "quarantine cause digest")
        for name, values in (
            ("quarantine roots", self.root_digests),
            ("quarantine affected objects", self.affected_digests),
        ):
            if (
                type(values) is not tuple
                or not values
                or values != tuple(sorted(set(values)))
            ):
                raise RollbackValidationError(f"{name} must be unique and sorted")
            for digest in values:
                _require_digest(digest, name)
        if not set(self.root_digests).issubset(self.affected_digests):
            raise RollbackValidationError(
                "quarantine affected objects must include every root"
            )

    def canonical_object(self) -> dict[str, Any]:
        return {
            "affected_digests": list(self.affected_digests),
            "cause_digest": self.cause_digest,
            "rollback_id": self.rollback_id,
            "root_digests": list(self.root_digests),
            "schema_version": "bb.rl.phase5.dependent-quarantine-operation.v2",
        }

    @property
    def digest(self) -> str:
        return canonical_digest(canonical_json_bytes(self.canonical_object()))


class FilesystemDependentQuarantineStore(_PinnedSignedDirectory):
    _GLOBAL_MARKER = "dependent-index.blocked"

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
            domain="dependent-quarantine",
            root_fd=root_fd,
        )

    @staticmethod
    def _key(object_ref: ImmutableObjectRef) -> str:
        if type(object_ref) is not ImmutableObjectRef:
            raise RollbackValidationError("dependent object ref must be exact")
        return object_ref.identity_digest[7:]

    @classmethod
    def _head_name(cls, object_ref: ImmutableObjectRef) -> str:
        return f"dependent.{cls._key(object_ref)}.head"

    @classmethod
    def _marker_name(cls, object_ref: ImmutableObjectRef) -> str:
        return f"dependent.{cls._key(object_ref)}.blocked"

    @classmethod
    def _history_name(cls, record: DependentOwnershipRecord) -> str:
        return (
            f"dependent.{cls._key(record.ownership.object_ref)}."
            f"g{record.generation:020d}.{record.digest[7:]}.history"
        )

    @classmethod
    def _commit_name(cls, record: DependentOwnershipRecord) -> str:
        return (
            f"dependent.{cls._key(record.ownership.object_ref)}."
            f"g{record.generation:020d}.{record.digest[7:]}.commit"
        )

    @staticmethod
    def _operation_name(rollback_id: str) -> str:
        return f"quarantine.{_require_id(rollback_id, 'rollback id')}.request"

    @staticmethod
    def _operation_marker(rollback_id: str) -> str:
        return f"quarantine.{_require_id(rollback_id, 'rollback id')}.blocked"

    @staticmethod
    def _operation_complete_name(rollback_id: str) -> str:
        return f"quarantine.{_require_id(rollback_id, 'rollback id')}.complete"

    @staticmethod
    def _registration_name(registration_id: str) -> str:
        return (
            f"registration."
            f"{_require_id(registration_id, 'dependent registration id')}.request"
        )

    def _registration_binding_bytes(self, ownership: DependentOwnership) -> bytes:
        return self._signed_bytes(
            "dependent-registration-binding",
            {
                "object_identity_digest": ownership.object_ref.identity_digest,
                "ownership_digest": ownership.digest,
                "registration_id": ownership.registration_id,
                "schema_version": "bb.rl.phase5.dependent-registration-binding.v1",
            },
        )

    def _decode(self, raw: bytes) -> DependentOwnershipRecord:
        return _dependent_record_from_object(
            self._verify_signed(raw, "dependent-record")
        )

    def _assert_global_unblocked(self) -> None:
        if self._blocked(self._GLOBAL_MARKER):
            raise RollbackCorruptionError("dependent ownership index is quarantined")

    def _committed_history_locked(
        self, object_ref: ImmutableObjectRef
    ) -> tuple[tuple[DependentOwnershipRecord, bytes], ...]:
        self._assert_global_unblocked()
        marker = self._marker_name(object_ref)
        if self._blocked(marker):
            raise RollbackCorruptionError("dependent ownership record is quarantined")
        key = self._key(object_ref)
        prefix = f"dependent.{key}.g"
        history_pattern = re.compile(
            rf"^dependent\.{key}\.g(\d{{20}})\.([0-9a-f]{{64}})\.history$"
        )
        commit_pattern = re.compile(
            rf"^dependent\.{key}\.g(\d{{20}})\.([0-9a-f]{{64}})\.commit$"
        )
        histories: dict[
            tuple[int, str], tuple[DependentOwnershipRecord, bytes, str]
        ] = {}
        for name in sorted(
            item
            for item in self._bounded_root_names()
            if item.startswith(prefix) and item.endswith(".history")
        ):
            try:
                match = history_pattern.fullmatch(name)
                if match is None:
                    raise RollbackCorruptionError("dependent history name is invalid")
                raw = self._read(name)
                if raw is None:
                    raise RollbackCorruptionError("dependent history disappeared")
                record = self._decode(raw)
                record_key = (int(match.group(1)), "sha256:" + match.group(2))
                if (
                    record.ownership.object_ref != object_ref
                    or record.generation != record_key[0]
                    or record.digest != record_key[1]
                    or record_key in histories
                ):
                    raise RollbackCorruptionError("dependent history identity mismatch")
                histories[record_key] = (record, raw, name)
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(name, marker, object_ref.identity_digest)
                raise RollbackCorruptionError(
                    "dependent history was quarantined"
                ) from error
        committed: dict[int, tuple[DependentOwnershipRecord, bytes]] = {}
        for name in sorted(
            item
            for item in self._bounded_root_names()
            if item.startswith(prefix) and item.endswith(".commit")
        ):
            try:
                match = commit_pattern.fullmatch(name)
                if match is None:
                    raise RollbackCorruptionError("dependent commit name is invalid")
                generation = int(match.group(1))
                digest = "sha256:" + match.group(2)
                raw = self._read(name)
                if raw is None:
                    raise RollbackCorruptionError("dependent commit disappeared")
                self._verify_commit(
                    raw,
                    identity=object_ref.identity_digest,
                    generation=generation,
                    record_digest=digest,
                )
                history = histories.get((generation, digest))
                if history is None or generation in committed:
                    raise RollbackCorruptionError("dependent committed tip is invalid")
                committed[generation] = (history[0], history[1])
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(name, marker, object_ref.identity_digest)
                raise RollbackCorruptionError(
                    "dependent commit was quarantined"
                ) from error
        if not committed:
            return ()
        latest_generation = max(committed)
        latest = committed[latest_generation]
        chain: list[tuple[DependentOwnershipRecord, bytes]] = [latest]
        cursor = latest[0]
        while cursor.previous_record_digest is not None:
            predecessor = histories.get(
                (cursor.generation - 1, cursor.previous_record_digest)
            )
            if predecessor is None:
                self._quarantine(
                    self._history_name(cursor), marker, object_ref.identity_digest
                )
                raise RollbackCorruptionError(
                    "dependent committed predecessor is missing"
                )
            chain.append((predecessor[0], predecessor[1]))
            cursor = predecessor[0]
        chain.reverse()
        if (
            chain[0][0].generation != 1
            or chain[-1][0].generation != latest_generation
            or any(record.ownership != chain[0][0].ownership for record, _ in chain)
        ):
            self._quarantine(
                self._history_name(latest[0]), marker, object_ref.identity_digest
            )
            raise RollbackCorruptionError("dependent committed chain diverged")
        by_generation = {record.generation: record for record, _ in chain}
        if any(
            generation not in by_generation or by_generation[generation] != record
            for generation, (record, _) in committed.items()
        ):
            self._quarantine(
                self._history_name(latest[0]), marker, object_ref.identity_digest
            )
            raise RollbackCorruptionError("dependent committed history forked")
        ownership = chain[0][0].ownership
        binding_name = self._registration_name(ownership.registration_id)
        expected_binding = self._registration_binding_bytes(ownership)
        try:
            binding = self._read(binding_name)
            if binding is None:
                raise RollbackCorruptionError(
                    "dependent registration binding is missing"
                )
            self._verify_signed(binding, "dependent-registration-binding")
            if binding != expected_binding:
                raise RollbackCorruptionError("dependent registration binding diverged")
        except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
            self._quarantine(binding_name, marker, object_ref.identity_digest)
            raise RollbackCorruptionError(
                "dependent registration binding was quarantined"
            ) from error
        return tuple(chain)

    def _load_locked(
        self, object_ref: ImmutableObjectRef
    ) -> tuple[DependentOwnershipRecord | None, bytes | None]:
        marker = self._marker_name(object_ref)
        history = self._committed_history_locked(object_ref)
        name = self._head_name(object_ref)
        try:
            raw = self._read(name)
        except (RollbackCorruptionError, OSError) as error:
            self._quarantine(name, marker, object_ref.identity_digest)
            raise RollbackCorruptionError(
                "dependent ownership record was quarantined"
            ) from error
        if not history:
            if raw is not None:
                try:
                    record = self._decode(raw)
                    if (
                        record.ownership.object_ref != object_ref
                        or self._read(self._history_name(record)) != raw
                    ):
                        raise RollbackCorruptionError(
                            "uncommitted dependent head is invalid"
                        )
                except (
                    RollbackValidationError,
                    RollbackCorruptionError,
                    OSError,
                ) as error:
                    self._quarantine(name, marker, object_ref.identity_digest)
                    raise RollbackCorruptionError(
                        "dependent ownership record was quarantined"
                    ) from error
                os.unlink(name, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            return None, None
        current, committed_raw = history[-1]
        if raw is None:
            self._quarantine(name, marker, object_ref.identity_digest)
            raise RollbackCorruptionError("dependent committed head is missing")
        try:
            head_record = self._decode(raw)
        except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
            self._quarantine(name, marker, object_ref.identity_digest)
            raise RollbackCorruptionError(
                "dependent ownership record was quarantined"
            ) from error
        if head_record == current and raw == committed_raw:
            return current, raw
        if (
            head_record.generation > current.generation
            and head_record.ownership == current.ownership
            and self._read(self._history_name(head_record)) == raw
            and self._read(self._commit_name(head_record)) is None
        ):
            self._replace(name, committed_raw, raw)
            return current, committed_raw
        self._quarantine(name, marker, object_ref.identity_digest)
        raise RollbackCorruptionError("dependent signed head replay was quarantined")

    def _publish_records_locked(
        self,
        records: Sequence[DependentOwnershipRecord],
        old_payload: bytes | None,
    ) -> DependentOwnershipRecord:
        if not records:
            raise RollbackValidationError("dependent publication requires records")
        signed_by_digest: dict[str, bytes] = {}
        for record in records:
            signed = self._signed_bytes("dependent-record", record.canonical_object())
            signed_by_digest[record.digest] = signed
            self._create_immutable(self._history_name(record), signed)
        final = records[-1]
        self._publish_versioned(
            head_name=self._head_name(final.ownership.object_ref),
            history_name=self._history_name(final),
            commit_name=self._commit_name(final),
            identity=final.ownership.object_ref.identity_digest,
            generation=final.generation,
            record_digest=final.digest,
            signed_record=signed_by_digest[final.digest],
            old_head=old_payload,
        )
        return final

    def _all_locked(self) -> tuple[DependentOwnershipRecord, ...]:
        self._assert_global_unblocked()
        names = sorted(self._bounded_root_names())
        for name in names:
            if name.startswith("dependent.") and name.endswith(".blocked"):
                if self._blocked(name):
                    raise RollbackCorruptionError(
                        "dependent ownership index contains a quarantined identity"
                    )
        records: list[DependentOwnershipRecord] = []
        head_pattern = re.compile(r"^dependent\.([0-9a-f]{64})\.head$")
        for name in (
            item
            for item in names
            if item.startswith("dependent.") and item.endswith(".head")
        ):
            match = head_pattern.fullmatch(name)
            if match is None:
                self._quarantine(name, self._GLOBAL_MARKER, "dependent-index")
                raise RollbackCorruptionError(
                    "dependent ownership filename was quarantined"
                )
            try:
                raw = self._read(name)
                if raw is None:
                    continue
                decoded = self._decode(raw)
                if self._key(decoded.ownership.object_ref) != match.group(1):
                    raise RollbackCorruptionError(
                        "dependent filename identity mismatch"
                    )
                record, _ = self._load_locked(decoded.ownership.object_ref)
                if record is None:
                    raise RollbackCorruptionError(
                        "dependent committed record disappeared"
                    )
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                identity = "sha256:" + match.group(1)
                marker = f"dependent.{match.group(1)}.blocked"
                self._quarantine(name, marker, identity)
                raise RollbackCorruptionError(
                    "dependent ownership index was quarantined"
                ) from error
            records.append(record)
        return tuple(records)

    def _operation_from_raw(self, raw: bytes, rollback_id: str) -> _QuarantineOperation:
        payload = _require_object(
            self._verify_signed(raw, "quarantine-operation"),
            frozenset(
                (
                    "affected_digests",
                    "cause_digest",
                    "rollback_id",
                    "root_digests",
                    "schema_version",
                )
            ),
            "dependent quarantine operation",
        )
        if (
            payload["schema_version"]
            != "bb.rl.phase5.dependent-quarantine-operation.v2"
            or payload["rollback_id"] != rollback_id
        ):
            raise RollbackCorruptionError(
                "dependent quarantine operation identity mismatch"
            )
        return _QuarantineOperation(
            payload["rollback_id"],
            payload["cause_digest"],
            tuple(_require_tuple(payload["root_digests"], "quarantine roots")),
            tuple(
                _require_tuple(
                    payload["affected_digests"], "quarantine affected objects"
                )
            ),
        )

    def _operations_locked(
        self,
    ) -> tuple[tuple[_QuarantineOperation, bool], ...]:
        names = sorted(self._bounded_root_names())
        blocked_pattern = re.compile(
            r"^quarantine\.([A-Za-z0-9][A-Za-z0-9._-]{0,127})\.blocked$"
        )
        for name in names:
            match = blocked_pattern.fullmatch(name)
            if match is not None and self._blocked(name):
                raise RollbackCorruptionError(
                    "dependent quarantine operation is blocked"
                )
        request_pattern = re.compile(
            r"^quarantine\.([A-Za-z0-9][A-Za-z0-9._-]{0,127})\.request$"
        )
        operations: list[tuple[_QuarantineOperation, bool]] = []
        for name in (
            item
            for item in names
            if item.startswith("quarantine.") and item.endswith(".request")
        ):
            match = request_pattern.fullmatch(name)
            if match is None:
                self._quarantine(name, self._GLOBAL_MARKER, "dependent-index")
                raise RollbackCorruptionError(
                    "dependent quarantine request filename is invalid"
                )
            rollback_id = match.group(1)
            try:
                raw = self._read(name)
                if raw is None:
                    raise RollbackCorruptionError(
                        "dependent quarantine request disappeared"
                    )
                operation = self._operation_from_raw(raw, rollback_id)
                complete_name = self._operation_complete_name(rollback_id)
                complete_raw = self._read(complete_name)
                complete = complete_raw is not None
                if complete_raw is not None:
                    payload = _require_object(
                        self._verify_signed(
                            complete_raw, "quarantine-operation-complete"
                        ),
                        frozenset(
                            ("operation_digest", "rollback_id", "schema_version")
                        ),
                        "dependent quarantine completion",
                    )
                    if payload != {
                        "operation_digest": operation.digest,
                        "rollback_id": rollback_id,
                        "schema_version": "bb.rl.phase5.dependent-quarantine-complete.v1",
                    }:
                        raise RollbackCorruptionError(
                            "dependent quarantine completion mismatch"
                        )
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(name, self._operation_marker(rollback_id), rollback_id)
                raise RollbackCorruptionError(
                    "dependent quarantine operation was quarantined"
                ) from error
            operations.append((operation, complete))
        return tuple(operations)

    def register(self, ownership: DependentOwnership) -> DependentOwnershipRecord:
        if type(ownership) is not DependentOwnership:
            raise RollbackValidationError("dependent ownership must be exact")
        binding_name = self._registration_name(ownership.registration_id)
        binding = self._registration_binding_bytes(ownership)
        with self._exclusive():
            self._assert_global_unblocked()
            existing_binding = self._read(binding_name)
            if existing_binding is not None:
                try:
                    self._verify_signed(
                        existing_binding, "dependent-registration-binding"
                    )
                except (RollbackValidationError, RollbackCorruptionError) as error:
                    self._quarantine(
                        binding_name, self._GLOBAL_MARKER, "dependent-index"
                    )
                    raise RollbackCorruptionError(
                        "dependent registration binding was quarantined"
                    ) from error
                if existing_binding != binding:
                    raise RollbackIdempotencyConflict(
                        "dependent registration id is bound to different ownership"
                    )
            current, old_payload = self._load_locked(ownership.object_ref)
            if current is not None and current.ownership != ownership:
                raise RollbackConflictError(
                    "immutable object is already bound to different ownership"
                )
            all_records = self._all_locked()
            by_identity = {
                item.ownership.object_ref.identity_digest: item for item in all_records
            }
            operations = self._operations_locked()
            inherited: dict[tuple[str, str], tuple[str, ...]] = {}
            for parent in ownership.parent_refs:
                parent_state = by_identity.get(parent.identity_digest)
                if parent_state is None:
                    raise RollbackConflictError(
                        "dependent parent must be registered before its child"
                    )
                for receipt in parent_state.quarantine_receipts:
                    inherited[(receipt.rollback_id, receipt.cause_digest)] = (
                        receipt.causal_root_digests
                    )
                for operation, _ in operations:
                    if parent.identity_digest in operation.affected_digests:
                        inherited[(operation.rollback_id, operation.cause_digest)] = (
                            operation.root_digests
                        )
            if existing_binding is None:
                self._create_immutable(binding_name, binding)
            record = (
                current
                if current is not None
                else DependentOwnershipRecord(1, ownership, True, True, (), None)
            )
            publications: list[DependentOwnershipRecord] = []
            if current is None:
                publications.append(record)
            for rollback_id, cause_digest in sorted(inherited):
                if any(
                    item.rollback_id == rollback_id
                    and item.cause_digest == cause_digest
                    for item in record.quarantine_receipts
                ):
                    continue
                receipt = DependentQuarantineReceipt(
                    rollback_id,
                    cause_digest,
                    ownership.object_ref,
                    ownership.digest,
                    inherited[(rollback_id, cause_digest)],
                    record.generation + 1,
                )
                record = DependentOwnershipRecord(
                    record.generation + 1,
                    ownership,
                    False,
                    False,
                    (*record.quarantine_receipts, receipt),
                    record.digest,
                )
                publications.append(record)
            if not publications:
                return record
            return self._publish_records_locked(publications, old_payload)

    def get(self, object_ref: ImmutableObjectRef) -> DependentOwnershipRecord | None:
        with self._exclusive():
            return self._load_locked(object_ref)[0]

    def list_owned(
        self,
        *,
        approved_tuple_digest: str | None = None,
        episode_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[DependentOwnershipRecord, ...]:
        if approved_tuple_digest is not None:
            _require_digest(approved_tuple_digest, "approved tuple digest")
        if episode_id is not None:
            _require_id(episode_id, "episode id")
        if run_id is not None:
            _require_id(run_id, "run id")
        with self._exclusive():
            self._operations_locked()
            return tuple(
                record
                for record in self._all_locked()
                if (
                    approved_tuple_digest is None
                    or record.ownership.approved_tuple_digest == approved_tuple_digest
                )
                and (episode_id is None or record.ownership.episode_id == episode_id)
                and (run_id is None or record.ownership.run_id == run_id)
            )

    def quarantine_causal(
        self,
        rollback_id: str,
        cause_digest: str,
        root_refs: tuple[ImmutableObjectRef, ...],
    ) -> tuple[DependentQuarantineReceipt, ...]:
        _require_id(rollback_id, "rollback id")
        _require_digest(cause_digest, "dependent quarantine cause digest")
        if (
            type(root_refs) is not tuple
            or not root_refs
            or any(type(item) is not ImmutableObjectRef for item in root_refs)
        ):
            raise RollbackValidationError("causal quarantine roots must be exact")
        root_digests = tuple(sorted({item.identity_digest for item in root_refs}))
        if len(root_digests) != len(root_refs):
            raise RollbackValidationError("causal quarantine roots must be unique")
        with self._exclusive():
            if self._blocked(self._operation_marker(rollback_id)):
                raise RollbackCorruptionError(
                    "dependent quarantine operation is blocked"
                )
            records = self._all_locked()
            by_identity = {
                record.ownership.object_ref.identity_digest: record
                for record in records
            }
            missing = set(root_digests) - set(by_identity)
            if missing:
                raise RollbackConflictError("causal quarantine root is not registered")
            affected = set(root_digests)
            changed = True
            while changed:
                changed = False
                for identity, record in by_identity.items():
                    if identity in affected:
                        continue
                    if {
                        parent.identity_digest
                        for parent in record.ownership.parent_refs
                    } & affected:
                        affected.add(identity)
                        changed = True
            computed_operation = _QuarantineOperation(
                rollback_id,
                cause_digest,
                root_digests,
                tuple(sorted(affected)),
            )
            operation_name = self._operation_name(rollback_id)
            existing_operation = self._read(operation_name)
            if existing_operation is None:
                operation = computed_operation
                self._create_immutable(
                    operation_name,
                    self._signed_bytes(
                        "quarantine-operation", operation.canonical_object()
                    ),
                )
            else:
                try:
                    operation = self._operation_from_raw(
                        existing_operation, rollback_id
                    )
                except (RollbackValidationError, RollbackCorruptionError) as error:
                    self._quarantine(
                        operation_name,
                        self._operation_marker(rollback_id),
                        rollback_id,
                    )
                    raise RollbackCorruptionError(
                        "dependent quarantine operation was quarantined"
                    ) from error
                if (
                    operation.cause_digest != cause_digest
                    or operation.root_digests != root_digests
                ):
                    raise RollbackIdempotencyConflict(
                        "rollback id is bound to a different dependent quarantine request"
                    )
                if set(operation.affected_digests) - set(by_identity):
                    self._quarantine(
                        operation_name,
                        self._operation_marker(rollback_id),
                        rollback_id,
                    )
                    raise RollbackCorruptionError(
                        "dependent quarantine affected ownership is missing"
                    )
            receipts: list[DependentQuarantineReceipt] = []
            for identity in operation.affected_digests:
                current = by_identity[identity]
                prior = next(
                    (
                        item
                        for item in current.quarantine_receipts
                        if item.rollback_id == rollback_id
                        and item.cause_digest == cause_digest
                    ),
                    None,
                )
                if prior is not None:
                    receipts.append(prior)
                    continue
                receipt = DependentQuarantineReceipt(
                    rollback_id,
                    cause_digest,
                    current.ownership.object_ref,
                    current.ownership.digest,
                    root_digests,
                    current.generation + 1,
                )
                updated = DependentOwnershipRecord(
                    current.generation + 1,
                    current.ownership,
                    False,
                    False,
                    (*current.quarantine_receipts, receipt),
                    current.digest,
                )
                old_payload = self._read(self._head_name(current.ownership.object_ref))
                if old_payload is None:
                    raise RollbackCorruptionError(
                        "dependent ownership disappeared during quarantine"
                    )
                self._publish_records_locked((updated,), old_payload)
                by_identity[identity] = updated
                receipts.append(receipt)
            complete_payload = self._signed_bytes(
                "quarantine-operation-complete",
                {
                    "operation_digest": operation.digest,
                    "rollback_id": rollback_id,
                    "schema_version": "bb.rl.phase5.dependent-quarantine-complete.v1",
                },
            )
            self._create_immutable(
                self._operation_complete_name(rollback_id),
                complete_payload,
            )
            return tuple(receipts)

    def _assert_eligible(self, object_ref: ImmutableObjectRef, *, export: bool) -> None:
        with self._exclusive():
            record, _ = self._load_locked(object_ref)
            if record is None:
                raise DependentIneligibleError("dependent object is not registered")
            identity = object_ref.identity_digest
            if any(
                not complete and identity in operation.affected_digests
                for operation, complete in self._operations_locked()
            ):
                raise DependentIneligibleError(
                    "dependent object has an incomplete quarantine intent"
                )
            eligible = record.export_eligible if export else record.promotion_eligible
            if not eligible:
                purpose = "export" if export else "promotion"
                raise DependentIneligibleError(
                    f"dependent object is quarantined for {purpose}"
                )

    def assert_promotion_eligible(self, object_ref: ImmutableObjectRef) -> None:
        self._assert_eligible(object_ref, export=False)

    def assert_export_eligible(self, object_ref: ImmutableObjectRef) -> None:
        self._assert_eligible(object_ref, export=True)

    @contextmanager
    def read_fence(
        self,
    ) -> Iterator[tuple[DependentOwnershipRecord, ...]]:
        with self._exclusive():
            operations = self._operations_locked()
            if any(not complete for _, complete in operations):
                raise DependentIneligibleError(
                    "dependent quarantine operation is incomplete"
                )
            yield self._all_locked()

__all__ = ['FilesystemDependentQuarantineStore', '_QuarantineOperation']
