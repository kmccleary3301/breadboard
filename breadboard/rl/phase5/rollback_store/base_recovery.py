from __future__ import annotations

from ._imports import *
from .models import *
from .publication import *

class _PinnedSignedDirectoryRecovery:
    @contextmanager
    def _publication_transaction(
        self,
        revalidate: Any,
    ) -> Iterator[_PublicationTransaction]:
        if self._publication_tx is not None:
            raise RollbackCorruptionError(
                "nested rollback publication transaction is forbidden"
            )
        transaction = _PublicationTransaction(self, revalidate)
        self._publication_tx = transaction
        try:
            yield transaction
        except BaseException as operation_error:
            try:
                transaction.rollback()
            except BaseException as rollback_error:
                raise rollback_error from operation_error
            raise
        finally:
            self._publication_tx = None


    def __init__(
        self,
        root: str | Path,
        *,
        authority_key: bytes,
        domain: str,
        root_fd: int | None = None,
    ) -> None:
        if type(authority_key) is not bytes or len(authority_key) < 32:
            raise RollbackValidationError(
                "rollback authority key must be at least 32 bytes"
            )
        _require_id(domain, "rollback store domain")
        requested = Path(root)
        if root_fd is None:
            if requested.exists() and requested.is_symlink():
                raise RollbackCorruptionError("rollback store root cannot be a symlink")
            requested.mkdir(mode=0o700, parents=True, exist_ok=True)
            resolved = requested.resolve(strict=True)
            if requested.absolute() != resolved:
                raise RollbackCorruptionError(
                    "rollback store root cannot use a path alias"
                )
            self.root = resolved
        else:
            self.root = requested.absolute()
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        self._root_fd = (
            os.dup(root_fd) if root_fd is not None else os.open(self.root, flags)
        )
        self._root_stat = os.fstat(self._root_fd)
        if (
            self._root_stat.st_uid != os.geteuid()
            or self._root_stat.st_gid != os.getegid()
        ):
            os.close(self._root_fd)
            raise RollbackCorruptionError(
                "rollback store root owner is not the effective owner"
            )
        self._owner = (self._root_stat.st_uid, self._root_stat.st_gid)
        self._quarantine_fd = -1
        self._terminal_fd = -1
        self._terminal_stat: os.stat_result | None = None
        self._quarantine_stat: os.stat_result | None = None
        self._lock_fd = -1
        self._authority_key = authority_key
        self._domain = domain
        self._thread_lock = threading.RLock()
        self._publication_tx: _PublicationTransaction | None = None
        self._cleanup_forward_active = False
        self._cleanup_pending_checkpoint_factory: Any | None = None
        self._cleanup_recovery_replace_boundary: str | None = None
        self._cleanup_recovery_replace_temp: str | None = None
        self._cleanup_recovery_replace_destination: str | None = None
        self._cleanup_recovery_replace_proof: dict[str, object] | None = None
        self._cleanup_recovery_checkpoint: Any | None = None
        self._cleanup_resumed_forward = False
        self._closed = False
        try:
            self._validate_dir_mode(self._root_stat, "rollback store root")
            try:
                os.mkdir(".quarantine", mode=0o700, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            except FileExistsError:
                pass
            self._quarantine_fd = os.open(".quarantine", flags, dir_fd=self._root_fd)
            self._quarantine_stat = os.fstat(self._quarantine_fd)
            self._validate_dir_mode(
                self._quarantine_stat,
                "rollback quarantine directory",
            )
            if self._domain == "rollback-journal":
                try:
                    os.mkdir(
                        _ROLLBACK_TERMINAL_DIRECTORY,
                        mode=0o700,
                        dir_fd=self._root_fd,
                    )
                    os.fsync(self._root_fd)
                except FileExistsError:
                    pass
                self._terminal_fd = os.open(
                    _ROLLBACK_TERMINAL_DIRECTORY,
                    flags,
                    dir_fd=self._root_fd,
                )
                self._terminal_stat = os.fstat(self._terminal_fd)
                self._validate_dir_mode(
                    self._terminal_stat,
                    "rollback terminal directory",
                )
            self._lock_fd = self._open_regular(
                ".store.lock", os.O_RDWR | os.O_CREAT, 0o600
            )
            fcntl.flock(self._root_fd, fcntl.LOCK_EX)
            try:
                self._validate_root()
                self._cleanup_abandoned_temps()
            finally:
                fcntl.flock(self._root_fd, fcntl.LOCK_UN)
        except BaseException:
            for descriptor in (
                self._lock_fd,
                self._terminal_fd,
                self._quarantine_fd,
                self._root_fd,
            ):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            raise


    def _validate_dir_mode(
        self,
        value: os.stat_result,
        name: str,
    ) -> None:
        if (
            not stat.S_ISDIR(value.st_mode)
            or stat.S_IMODE(value.st_mode) != 0o700
            or (value.st_uid, value.st_gid) != self._owner
        ):
            raise RollbackCorruptionError(
                f"{name} must be a trusted-owner 0700 directory"
            )


    @staticmethod
    def _journal_version_name(
        rollback_id: str,
        generation: int,
        record_digest: str,
        suffix: str,
    ) -> str:
        return f"journal.{rollback_id}.g{generation:020d}.{record_digest[7:]}.{suffix}"


    @staticmethod
    def _rollback_quarantine_names(
        transaction_id: str,
        rollback_id: str,
        successor_record_digest: str,
    ) -> tuple[str, str]:
        rollback_identity_digest = canonical_digest(
            _require_id(rollback_id, "rollback id").encode()
        )[7:]
        base = (
            f"rollback-quarantine.{rollback_identity_digest}."
            f"{transaction_id}.{successor_record_digest[7:]}"
        )
        return f"{base}.successor", f"{base}.tombstone"


    @staticmethod
    def _decode_recovery_identity(
        value: object,
        name: str,
    ) -> tuple[int, int, int, int, int, int, int, int]:
        if (
            type(value) is not list
            or len(value) != 8
            or any(type(item) is not int or item < 0 for item in value)
        ):
            raise RollbackCorruptionError(f"{name} recovery identity is invalid")
        return tuple(value)


    def _rollback_intent_bytes(
        self,
        transaction_id: str,
        previous_raw: bytes,
        successor_raw: bytes,
    ) -> bytes:
        previous = _journal_from_object(
            self._verify_signed(previous_raw, "journal-record")
        )
        successor = _journal_from_object(
            self._verify_signed(successor_raw, "journal-record")
        )
        if (
            successor.rollback_id != previous.rollback_id
            or successor.generation != previous.generation + 1
            or successor.previous_record_digest != previous.digest
        ):
            raise RollbackCorruptionError(
                "rollback transaction records are not exact successors"
            )
        return self._signed_bytes(
            "publication-rollback-intent",
            {
                "domain": self._domain,
                "prior_generation": previous.generation,
                "prior_raw_sha256": canonical_digest(previous_raw),
                "prior_record_digest": previous.digest,
                "relationship": "exact-successor",
                "rollback_id": previous.rollback_id,
                "schema_version": ("bb.rl.phase5.publication-rollback-intent.v1"),
                "successor_generation": successor.generation,
                "successor_raw_sha256": canonical_digest(successor_raw),
                "successor_record_digest": successor.digest,
                "transaction_id": transaction_id,
                "state": "active",
                "prior_commit_identity": None,
                "prior_history_identity": None,
                "quarantine_name": None,
                "successor_quarantine_identity": None,
            },
        )


    def _rollback_replaced_head(
        self,
        head_name: str,
        previous_raw: bytes,
        transaction_id: str,
    ) -> None:
        successor_raw = self._read(head_name)
        if successor_raw is None:
            raise RollbackCorruptionError(
                "rollback transaction successor head is missing"
            )
        if successor_raw == previous_raw:
            return
        intent_name = f".{self._domain}.{transaction_id}.transaction-rollback"
        intent_raw = self._rollback_intent_bytes(
            transaction_id,
            previous_raw,
            successor_raw,
        )
        self._create_immutable(intent_name, intent_raw)
        capsule = self._preflight_transaction_rollback_intent(intent_name)
        try:
            self._recover_transaction_rollback(capsule)
        finally:
            capsule.close()


    def _preflight_transaction_rollback_intent(
        self,
        name: str,
        *,
        recovery_directory_fd: int | None = None,
    ) -> _RollbackRecoveryCapsule:
        if self._domain != "rollback-journal":
            raise RollbackCorruptionError(
                "transaction rollback intent has no recovery authority"
            )
        match = re.fullmatch(
            rf"\.{re.escape(self._domain)}\.([0-9a-f]{{32}})\."
            r"transaction-rollback",
            name,
        )
        if match is None:
            raise RollbackCorruptionError("transaction rollback intent name is invalid")
        transaction_id = match.group(1)
        held: list[_HeldStoreFile] = []

        def recovery_location(candidate_name: str) -> int | None:
            root_exists = self._read(candidate_name) is not None
            if recovery_directory_fd is None:
                return None if root_exists else -1
            try:
                os.stat(
                    candidate_name,
                    dir_fd=recovery_directory_fd,
                    follow_symlinks=False,
                )
                staged_exists = True
            except FileNotFoundError:
                staged_exists = False
            if root_exists and staged_exists:
                raise RollbackCorruptionError(
                    "rollback recovery authority is duplicated"
                )
            if staged_exists:
                return recovery_directory_fd
            return None if root_exists else -1

        def recovery_exists(candidate_name: str) -> bool:
            return recovery_location(candidate_name) != -1

        try:
            intent_file = _HeldStoreFile.capture(
                self,
                name,
                directory_fd=recovery_directory_fd,
            )
            held.append(intent_file)
            intent = _require_object(
                self._verify_signed(
                    intent_file.raw,
                    "publication-rollback-intent",
                ),
                frozenset(
                    (
                        "domain",
                        "prior_generation",
                        "prior_raw_sha256",
                        "prior_record_digest",
                        "relationship",
                        "rollback_id",
                        "schema_version",
                        "prior_commit_identity",
                        "prior_history_identity",
                        "quarantine_name",
                        "successor_generation",
                        "successor_raw_sha256",
                        "successor_record_digest",
                        "successor_quarantine_identity",
                        "transaction_id",
                        "state",
                    )
                ),
                "publication rollback intent",
            )
            if (
                intent["schema_version"]
                != "bb.rl.phase5.publication-rollback-intent.v1"
                or intent["domain"] != self._domain
                or intent["transaction_id"] != transaction_id
                or intent["relationship"] != "exact-successor"
            ):
                raise RollbackCorruptionError(
                    "publication rollback intent binding is invalid"
                )
            intent_state = intent["state"]
            if intent_state not in (
                "active",
                "cleanup_pending",
                "quarantined",
            ):
                raise RollbackCorruptionError(
                    "publication rollback intent state is invalid"
                )
            rollback_id = intent["rollback_id"]
            _require_id(rollback_id, "rollback id")
            prior_generation = intent["prior_generation"]
            successor_generation = intent["successor_generation"]
            _require_int(prior_generation, "prior generation", minimum=1)
            _require_int(
                successor_generation,
                "successor generation",
                minimum=2,
            )
            if successor_generation != prior_generation + 1:
                raise RollbackCorruptionError(
                    "publication rollback generations are not adjacent"
                )
            for field_name in (
                "prior_raw_sha256",
                "prior_record_digest",
                "successor_raw_sha256",
                "successor_record_digest",
            ):
                _require_digest(intent[field_name], field_name)
            quarantine_name, tombstone_name = self._rollback_quarantine_names(
                transaction_id,
                rollback_id,
                intent["successor_record_digest"],
            )
            terminal_identities: (
                tuple[
                    tuple[int, int, int, int, int, int, int, int],
                    tuple[int, int, int, int, int, int, int, int],
                    tuple[int, int, int, int, int, int, int, int],
                ]
                | None
            ) = None
            if intent_state == "quarantined":
                if intent["quarantine_name"] != quarantine_name:
                    raise RollbackCorruptionError(
                        "rollback quarantine name binding is invalid"
                    )
                terminal_identities = (
                    self._decode_recovery_identity(
                        intent["prior_history_identity"],
                        "prior history",
                    ),
                    self._decode_recovery_identity(
                        intent["prior_commit_identity"],
                        "prior commit",
                    ),
                    self._decode_recovery_identity(
                        intent["successor_quarantine_identity"],
                        "successor quarantine",
                    ),
                )
            elif any(
                intent[field_name] is not None
                for field_name in (
                    "prior_commit_identity",
                    "prior_history_identity",
                    "quarantine_name",
                    "successor_quarantine_identity",
                )
            ):
                raise RollbackCorruptionError(
                    "non-terminal rollback intent has quarantine bindings"
                )
            prior_history_name = self._journal_version_name(
                rollback_id,
                prior_generation,
                intent["prior_record_digest"],
                "history",
            )
            predecessor = _HeldStoreFile.capture(
                self,
                prior_history_name,
            )
            held.append(predecessor)
            if canonical_digest(predecessor.raw) != intent["prior_raw_sha256"]:
                raise RollbackCorruptionError(
                    "publication rollback predecessor history is invalid"
                )
            prior = _journal_from_object(
                self._verify_signed(
                    predecessor.raw,
                    "journal-record",
                )
            )
            if (
                prior.rollback_id != rollback_id
                or prior.generation != prior_generation
                or prior.digest != intent["prior_record_digest"]
            ):
                raise RollbackCorruptionError(
                    "publication rollback predecessor binding is invalid"
                )
            prior_commit_name = self._journal_version_name(
                rollback_id,
                prior_generation,
                prior.digest,
                "commit",
            )
            predecessor_commit = _HeldStoreFile.capture(
                self,
                prior_commit_name,
            )
            held.append(predecessor_commit)
            self._verify_commit(
                predecessor_commit.raw,
                identity=rollback_id,
                generation=prior_generation,
                record_digest=prior.digest,
            )
            if terminal_identities is not None and (
                predecessor.identity != terminal_identities[0]
                or predecessor_commit.identity != terminal_identities[1]
            ):
                raise RollbackCorruptionError(
                    "rollback quarantine predecessor identity changed"
                )
            successor_history_name = self._journal_version_name(
                rollback_id,
                successor_generation,
                intent["successor_record_digest"],
                "history",
            )
            successor_commit_name = self._journal_version_name(
                rollback_id,
                successor_generation,
                intent["successor_record_digest"],
                "commit",
            )
            if (
                self._read(successor_history_name) is not None
                or self._read(successor_commit_name) is not None
            ):
                raise RollbackCorruptionError(
                    "publication rollback successor is committed or staged"
                )
            head_name = f"journal.{rollback_id}.head"
            displaced_name = f".{self._domain}.{transaction_id}.displaced-head"
            candidate_name = f".{self._domain}.{transaction_id}.prior-candidate"
            head_exists = self._read(head_name) is not None
            displaced_exists = recovery_exists(displaced_name)
            quarantine_exists = self._read(quarantine_name) is not None
            if self._read(tombstone_name) is not None:
                raise RollbackCorruptionError(
                    "terminal rollback tombstone conflicts with active intent"
                )
            successor: _HeldStoreFile | None = None
            installed_head: _HeldStoreFile | None = None
            if intent_state == "active":
                if quarantine_exists:
                    raise RollbackCorruptionError(
                        "active rollback has terminal quarantine authority"
                    )
                if head_exists and not displaced_exists:
                    head_file = _HeldStoreFile.capture(self, head_name)
                    held.append(head_file)
                    if (
                        canonical_digest(head_file.raw)
                        != intent["successor_raw_sha256"]
                    ):
                        raise RollbackCorruptionError(
                            "publication rollback head state conflicts"
                        )
                    successor = head_file
                    state = "successor_at_head"
                elif displaced_exists:
                    successor_location = recovery_location(displaced_name)
                    assert successor_location != -1
                    successor = _HeldStoreFile.capture(
                        self,
                        displaced_name,
                        directory_fd=successor_location,
                    )
                    held.append(successor)
                    if not head_exists:
                        state = "successor_displaced"
                    else:
                        installed_head = _HeldStoreFile.capture(
                            self,
                            head_name,
                        )
                        held.append(installed_head)
                        if installed_head.raw != predecessor.raw:
                            raise RollbackCorruptionError(
                                "publication rollback head state conflicts"
                            )
                        state = "prior_installed"
                else:
                    raise RollbackCorruptionError(
                        "publication rollback target head is missing"
                    )
            else:
                if not head_exists:
                    raise RollbackCorruptionError(
                        "terminal rollback prior head is missing"
                    )
                installed_head = _HeldStoreFile.capture(
                    self,
                    head_name,
                )
                held.append(installed_head)
                if installed_head.raw != predecessor.raw:
                    raise RollbackCorruptionError(
                        "terminal rollback prior head is invalid"
                    )
                if displaced_exists == quarantine_exists:
                    raise RollbackCorruptionError(
                        "terminal rollback successor authority is ambiguous"
                    )
                successor_path = displaced_name if displaced_exists else quarantine_name
                successor_directory_fd = (
                    recovery_location(displaced_name) if displaced_exists else None
                )
                assert successor_directory_fd != -1
                successor = _HeldStoreFile.capture(
                    self,
                    successor_path,
                    directory_fd=successor_directory_fd,
                )
                held.append(successor)
                if intent_state == "quarantined":
                    if displaced_exists or terminal_identities is None:
                        raise RollbackCorruptionError(
                            "quarantined rollback physical state is invalid"
                        )
                    if successor.identity != terminal_identities[2]:
                        raise RollbackCorruptionError(
                            "quarantined successor identity changed"
                        )
                    state = "quarantined_pending_move"
                else:
                    state = (
                        "cleanup_pending_with_displaced"
                        if displaced_exists
                        else "cleanup_pending_with_quarantine"
                    )
            if successor is not None:
                if canonical_digest(successor.raw) != intent["successor_raw_sha256"]:
                    raise RollbackCorruptionError(
                        "publication rollback successor bytes mismatch"
                    )
                successor_record = _journal_from_object(
                    self._verify_signed(
                        successor.raw,
                        "journal-record",
                    )
                )
                if (
                    successor_record.rollback_id != rollback_id
                    or successor_record.generation != successor_generation
                    or successor_record.digest != intent["successor_record_digest"]
                    or successor_record.previous_record_digest != prior.digest
                ):
                    raise RollbackCorruptionError(
                        "publication rollback target is not exact successor"
                    )
            candidate = None
            if recovery_exists(candidate_name):
                if state != "successor_displaced":
                    raise RollbackCorruptionError(
                        "publication rollback candidate conflicts"
                    )
                candidate_location = recovery_location(candidate_name)
                assert candidate_location != -1
                candidate = _HeldStoreFile.capture(
                    self,
                    candidate_name,
                    directory_fd=candidate_location,
                )
                held.append(candidate)
                if candidate.raw != predecessor.raw:
                    raise RollbackCorruptionError(
                        "publication rollback candidate is invalid"
                    )
            return _RollbackRecoveryCapsule(
                transaction_id=transaction_id,
                intent=intent_file,
                predecessor=predecessor,
                predecessor_commit=predecessor_commit,
                successor=successor,
                installed_head=installed_head,
                head_name=head_name,
                displaced_name=displaced_name,
                candidate_name=candidate_name,
                quarantine_name=quarantine_name,
                tombstone_name=tombstone_name,
                successor_history_name=successor_history_name,
                successor_commit_name=successor_commit_name,
                state=state,
                candidate=candidate,
            )
        except BaseException as error:
            for item in reversed(held):
                item.close()
            raise RollbackCorruptionError(
                "abandoned transaction rollback intent is invalid"
            ) from error


    def _revalidate_recovery_capsule(
        self,
        capsule: _RollbackRecoveryCapsule,
        *,
        successor_path: str | None,
        candidate_path: str | None = None,
    ) -> None:
        capsule.intent.revalidate(self)
        capsule.predecessor.revalidate(self)
        capsule.predecessor_commit.revalidate(self)
        if capsule.successor is not None:
            assert successor_path is not None
            capsule.successor.revalidate(
                self,
                path_name=successor_path,
            )
        if capsule.candidate is not None:
            capsule.candidate.revalidate(
                self,
                path_name=(
                    capsule.candidate_name if candidate_path is None else candidate_path
                ),
            )
        if capsule.installed_head is not None:
            capsule.installed_head.revalidate(
                self,
                path_name=capsule.head_name,
            )
        self._verify_signed(
            capsule.intent.raw,
            "publication-rollback-intent",
        )
        predecessor = _journal_from_object(
            self._verify_signed(
                capsule.predecessor.raw,
                "journal-record",
            )
        )
        self._verify_commit(
            capsule.predecessor_commit.raw,
            identity=predecessor.rollback_id,
            generation=predecessor.generation,
            record_digest=predecessor.digest,
        )
        if (
            self._read(capsule.successor_history_name) is not None
            or self._read(capsule.successor_commit_name) is not None
        ):
            raise RollbackCorruptionError(
                "publication rollback successor authority appeared"
            )


    def _cleanup_pending_intent_bytes(
        self,
        capsule: _RollbackRecoveryCapsule,
    ) -> bytes:
        payload = dict(
            _require_object(
                self._verify_signed(
                    capsule.intent.raw,
                    "publication-rollback-intent",
                ),
                frozenset(
                    (
                        "domain",
                        "prior_generation",
                        "prior_raw_sha256",
                        "prior_record_digest",
                        "relationship",
                        "rollback_id",
                        "schema_version",
                        "prior_commit_identity",
                        "prior_history_identity",
                        "quarantine_name",
                        "state",
                        "successor_generation",
                        "successor_raw_sha256",
                        "successor_record_digest",
                        "successor_quarantine_identity",
                        "transaction_id",
                    )
                ),
                "publication rollback intent",
            )
        )
        if payload["state"] != "active":
            raise RollbackCorruptionError(
                "rollback cleanup transition requires active intent"
            )
        payload["state"] = "cleanup_pending"
        return self._signed_bytes(
            "publication-rollback-intent",
            payload,
        )


    def _quarantined_intent_bytes(
        self,
        capsule: _RollbackRecoveryCapsule,
    ) -> bytes:
        payload = dict(
            self._verify_signed(
                capsule.intent.raw,
                "publication-rollback-intent",
            )
        )
        if payload["state"] != "cleanup_pending":
            raise RollbackCorruptionError(
                "terminal quarantine requires cleanup-pending intent"
            )
        assert capsule.successor is not None
        payload.update(
            {
                "prior_commit_identity": list(capsule.predecessor_commit.identity),
                "prior_history_identity": list(capsule.predecessor.identity),
                "quarantine_name": capsule.quarantine_name,
                "state": "quarantined",
                "successor_quarantine_identity": list(capsule.successor.identity),
            }
        )
        return self._signed_bytes(
            "publication-rollback-intent",
            payload,
        )


    def _publish_terminal_restoration(
        self,
        capsule: _RollbackRecoveryCapsule,
    ) -> RollbackJournalRecord:
        assert capsule.successor is not None
        predecessor = _journal_from_object(
            self._verify_signed(capsule.predecessor.raw, "journal-record")
        )
        successor = _journal_from_object(
            self._verify_signed(capsule.successor.raw, "journal-record")
        )
        ref = RollbackTerminalQuarantineRef(
            capsule.transaction_id,
            predecessor.rollback_id,
            predecessor.generation,
            predecessor.digest,
            successor.generation,
            successor.digest,
            canonical_digest(capsule.successor.raw),
            capsule.quarantine_name,
            capsule.tombstone_name,
            canonical_digest(capsule.intent.raw),
        )
        restoration = RollbackJournalRecord(
            predecessor.rollback_id,
            predecessor.request_digest,
            predecessor.request_payload_ref,
            successor.generation + 1,
            predecessor.revision,
            predecessor.phase,
            predecessor.phase_receipts,
            successor.digest,
            (*predecessor.terminal_quarantine_refs, ref),
        )
        signed_restoration = self._signed_bytes(
            "journal-record",
            restoration.canonical_object(),
        )
        current_head = self._read(capsule.head_name)
        anchor_key = (
            f"{canonical_digest(ref.rollback_id.encode())[7:]}."
            f"{ref.transaction_id}.{ref.successor_record_digest[7:]}"
        )
        indexed = anchor_key in self._terminal_quarantine_anchors()
        pending = self._terminal_anchor_pending(ref)
        if not indexed and pending is None:
            if current_head == signed_restoration:
                raise RollbackCorruptionError(
                    "terminal restoration has no pending-forward authority"
                )
            self._cleanup_recovery_fault("pending_anchor.before_publish")
            self._ensure_terminal_anchor_pending(ref)
            self._cleanup_recovery_fault("pending_anchor.after_publish")
        if current_head == signed_restoration:
            if self._read(self._history_name(restoration)) != signed_restoration:
                raise RollbackCorruptionError(
                    "terminal restoration history is incomplete"
                )
            expected_commit = self._commit_bytes(
                restoration.rollback_id,
                restoration.generation,
                restoration.digest,
            )
            current_commit = self._read(self._commit_name(restoration))
            anchors = self._terminal_quarantine_anchors()
            if current_commit is None:
                if anchor_key in anchors:
                    raise RollbackCorruptionError(
                        "terminal restoration committed authority disappeared"
                    )
                self._cleanup_recovery_fault("restoration_commit.before_publish")
                self._create_immutable(
                    self._commit_name(restoration),
                    expected_commit,
                )
                os.fsync(self._root_fd)
                self._cleanup_recovery_fault("restoration_commit.after_durable")
            elif current_commit != expected_commit:
                raise RollbackCorruptionError("terminal restoration commit is invalid")
            self._cleanup_recovery_fault("terminal_anchor.before_publish")
            self._publish_terminal_anchor(ref)
            self._cleanup_recovery_fault("terminal_anchor.after_publish")
            return restoration
        if current_head not in (
            capsule.predecessor.raw,
            capsule.successor.raw,
        ):
            raise RollbackCorruptionError(
                "terminal restoration canonical head conflicts"
            )
        publication_transaction = self._publication_tx
        self._publication_tx = None
        try:
            self._cleanup_recovery_fault("successor_history.before_publish")
            self._create_immutable(
                capsule.successor_history_name,
                capsule.successor.raw,
            )
            self._cleanup_recovery_fault("successor_history.after_publish")
            self._cleanup_recovery_fault("successor_commit.before_publish")
            self._create_immutable(
                capsule.successor_commit_name,
                self._commit_bytes(
                    successor.rollback_id,
                    successor.generation,
                    successor.digest,
                ),
            )
            self._cleanup_recovery_fault("successor_commit.after_publish")
            self._cleanup_recovery_fault("restoration_head.before_publish")
            self._cleanup_recovery_replace_boundary = "restoration_head"
            try:
                self._publish_versioned(
                    head_name=capsule.head_name,
                    history_name=self._history_name(restoration),
                    commit_name=self._commit_name(restoration),
                    identity=restoration.rollback_id,
                    generation=restoration.generation,
                    record_digest=restoration.digest,
                    signed_record=signed_restoration,
                    old_head=current_head,
                )
            finally:
                self._cleanup_recovery_replace_boundary = None
                self._cleanup_recovery_replace_temp = None
                self._cleanup_recovery_replace_destination = None
            self._cleanup_recovery_fault("restoration_head.after_publish")
            self._cleanup_recovery_fault("terminal_anchor.before_publish")
            self._publish_terminal_anchor(ref)
            self._cleanup_recovery_fault("terminal_anchor.after_publish")
        finally:
            self._publication_tx = publication_transaction
        return restoration


    def _mark_rollback_cleanup_pending(
        self,
        capsule: _RollbackRecoveryCapsule,
    ) -> None:
        cleanup_raw = self._cleanup_pending_intent_bytes(capsule)
        intent_directory_fd = capsule.intent.path_directory_fd
        self._replace_at(
            intent_directory_fd,
            capsule.intent.name,
            cleanup_raw,
            capsule.intent.raw,
            capsule.intent,
        )
        capsule.state = "cleanup_pending_with_displaced"
        replacement = _HeldStoreFile.capture(
            self,
            capsule.intent.name,
            directory_fd=intent_directory_fd,
        )
        prior_intent = capsule.intent
        capsule.intent = replacement
        prior_intent.close()


    def _finish_rollback_cleanup(
        self,
        capsule: _RollbackRecoveryCapsule,
        *,
        successor_path: str,
        candidate_path: str | None = None,
    ) -> None:
        self._revalidate_recovery_capsule(
            capsule,
            successor_path=successor_path,
            candidate_path=candidate_path,
        )
        if self._read(capsule.head_name) != capsule.predecessor.raw:
            raise RollbackCorruptionError(
                "cleanup-pending rollback predecessor is invalid"
            )
        assert capsule.successor is not None
        if successor_path == capsule.displaced_name:
            self._assert_rollback_quarantine_capacity(
                capsule.quarantine_name,
                len(capsule.successor.raw),
            )
            successor_directory_fd = capsule.successor.path_directory_fd
            self._cleanup_recovery_fault("successor_quarantine.before_move")
            _package_callable("_rename_noreplace_between", _rename_noreplace_between)(
                capsule.displaced_name,
                capsule.quarantine_name,
                successor_directory_fd,
                self._terminal_fd,
            )
            os.fsync(successor_directory_fd)
            os.fsync(self._terminal_fd)
            os.fsync(self._root_fd)
            self._cleanup_recovery_fault("successor_quarantine.after_durable")
            capsule.successor.path_directory_fd = self._terminal_fd
            capsule.successor.refresh_path_identity(
                self,
                capsule.quarantine_name,
            )
            capsule.state = "cleanup_pending_with_quarantine"
        elif successor_path != capsule.quarantine_name:
            raise RollbackCorruptionError("cleanup-pending successor path is invalid")
        self._revalidate_recovery_capsule(
            capsule,
            successor_path=capsule.quarantine_name,
            candidate_path=candidate_path,
        )
        terminal_raw = self._quarantined_intent_bytes(capsule)
        intent_directory_fd = capsule.intent.path_directory_fd
        self._replace_at(
            intent_directory_fd,
            capsule.intent.name,
            terminal_raw,
            capsule.intent.raw,
            capsule.intent,
        )
        capsule.state = "quarantined_pending_move"
        replacement = _HeldStoreFile.capture(
            self,
            capsule.intent.name,
            directory_fd=intent_directory_fd,
        )
        prior_intent = capsule.intent
        capsule.intent = replacement
        prior_intent.close()
        self._revalidate_recovery_capsule(
            capsule,
            successor_path=capsule.quarantine_name,
            candidate_path=candidate_path,
        )
        self._cleanup_recovery_fault("terminal_tombstone.before_move")
        _package_callable("_rename_noreplace_between", _rename_noreplace_between)(
            capsule.intent.name,
            capsule.tombstone_name,
            capsule.intent.path_directory_fd,
            self._terminal_fd,
        )
        os.fsync(self._terminal_fd)
        os.fsync(self._root_fd)
        self._cleanup_recovery_fault("terminal_tombstone.after_durable")
        capsule.intent.path_directory_fd = self._terminal_fd
        capsule.intent.refresh_path_identity(
            self,
            capsule.tombstone_name,
        )
        capsule.intent.name = capsule.tombstone_name
        capsule.state = "terminal_complete"
        self._revalidate_recovery_capsule(
            capsule,
            successor_path=capsule.quarantine_name,
            candidate_path=candidate_path,
        )
        self._cleanup_recovery_fault("restoration.before_publish")
        self._publish_terminal_restoration(capsule)
        self._cleanup_recovery_fault("restoration.after_publish")


    def _recover_transaction_rollback(
        self,
        capsule: _RollbackRecoveryCapsule,
    ) -> None:
        if capsule.state == "successor_at_head":
            successor_path = capsule.head_name
        elif capsule.state in (
            "cleanup_pending_with_quarantine",
            "quarantined_pending_move",
            "terminal_complete",
        ):
            successor_path = capsule.quarantine_name
        else:
            successor_path = capsule.displaced_name
        self._revalidate_recovery_capsule(
            capsule,
            successor_path=successor_path,
        )
        if capsule.state in (
            "cleanup_pending_with_displaced",
            "cleanup_pending_with_quarantine",
        ):
            self._finish_rollback_cleanup(
                capsule,
                successor_path=successor_path,
            )
            return
        if capsule.state == "quarantined_pending_move":
            self._cleanup_recovery_fault("terminal_tombstone.before_move")
            _package_callable("_rename_noreplace_between", _rename_noreplace_between)(
                capsule.intent.name,
                capsule.tombstone_name,
                capsule.intent.path_directory_fd,
                self._terminal_fd,
            )
            os.fsync(self._terminal_fd)
            os.fsync(self._root_fd)
            self._cleanup_recovery_fault("terminal_tombstone.after_durable")
            capsule.intent.path_directory_fd = self._terminal_fd
            capsule.intent.refresh_path_identity(
                self,
                capsule.tombstone_name,
            )
            capsule.intent.name = capsule.tombstone_name
            capsule.state = "terminal_complete"
            self._revalidate_recovery_capsule(
                capsule,
                successor_path=capsule.quarantine_name,
            )
            self._cleanup_recovery_fault("restoration.before_publish")
            self._publish_terminal_restoration(capsule)
            self._cleanup_recovery_fault("restoration.after_publish")
            return
        if capsule.state == "prior_installed":
            self._mark_rollback_cleanup_pending(capsule)
            self._finish_rollback_cleanup(
                capsule,
                successor_path=capsule.displaced_name,
            )
            return
        displaced_by_operation = False
        installed_prior = False
        candidate_created = False
        candidate_source_fd = self._root_fd
        try:
            if capsule.state == "successor_at_head":
                self._cleanup_recovery_fault("successor_displacement.before_move")
                _package_callable("_rename_noreplace", _rename_noreplace)(
                    capsule.head_name,
                    capsule.displaced_name,
                    self._root_fd,
                )
                os.fsync(self._root_fd)
                self._cleanup_recovery_fault("successor_displacement.after_durable")
                displaced_by_operation = True
                successor_path = capsule.displaced_name
                capsule.successor.refresh_path_identity(
                    self,
                    capsule.displaced_name,
                )
            self._revalidate_recovery_capsule(
                capsule,
                successor_path=successor_path,
            )
            if capsule.candidate is None:
                self._cleanup_recovery_fault("prior_candidate.before_publish")
                self._create_immutable(
                    capsule.candidate_name,
                    capsule.predecessor.raw,
                )
                self._cleanup_recovery_fault("prior_candidate.after_publish")
                capsule.candidate = _HeldStoreFile.capture(
                    self,
                    capsule.candidate_name,
                )
                candidate_created = True
            assert capsule.candidate is not None
            candidate_source_fd = capsule.candidate.path_directory_fd
            self._cleanup_recovery_fault("prior_head.before_publish")
            if candidate_source_fd == self._root_fd:
                _package_callable("_rename_noreplace", _rename_noreplace)(
                    capsule.candidate_name,
                    capsule.head_name,
                    self._root_fd,
                )
            else:
                _package_callable("_rename_noreplace_between", _rename_noreplace_between)(
                    capsule.candidate_name,
                    capsule.head_name,
                    candidate_source_fd,
                    self._root_fd,
                )
            os.fsync(self._root_fd)
            self._cleanup_recovery_fault("prior_head.after_durable")
            assert capsule.candidate is not None
            capsule.candidate.path_directory_fd = self._root_fd
            capsule.candidate.refresh_path_identity(
                self,
                capsule.head_name,
            )
            installed_prior = True
            self._revalidate_recovery_capsule(
                capsule,
                successor_path=capsule.displaced_name,
                candidate_path=capsule.head_name,
            )
            if self._read(capsule.head_name) != capsule.predecessor.raw:
                raise RollbackCorruptionError(
                    "installed rollback predecessor is invalid"
                )
            self._mark_rollback_cleanup_pending(capsule)
            self._finish_rollback_cleanup(
                capsule,
                successor_path=capsule.displaced_name,
                candidate_path=capsule.head_name,
            )
        except _CleanupInjectedCrash:
            raise
        except BaseException:
            if capsule.state.startswith(("cleanup_pending", "quarantined", "terminal")):
                raise
            if installed_prior:
                assert capsule.candidate is not None
                _package_callable("_rename_noreplace_between", _rename_noreplace_between)(
                    capsule.head_name,
                    capsule.candidate_name,
                    self._root_fd,
                    candidate_source_fd,
                )
                assert capsule.successor is not None
                _package_callable("_rename_noreplace_between", _rename_noreplace_between)(
                    capsule.displaced_name,
                    capsule.head_name,
                    capsule.successor.path_directory_fd,
                    self._root_fd,
                )
                capsule.candidate.path_directory_fd = candidate_source_fd
                os.fsync(self._root_fd)
                if candidate_created:
                    os.unlink(
                        capsule.candidate_name,
                        dir_fd=self._root_fd,
                    )
                    os.fsync(self._root_fd)
            elif displaced_by_operation:
                _package_callable("_rename_noreplace", _rename_noreplace)(
                    capsule.displaced_name,
                    capsule.head_name,
                    self._root_fd,
                )
                os.fsync(self._root_fd)
                if candidate_created:
                    try:
                        os.unlink(
                            capsule.candidate_name,
                            dir_fd=self._root_fd,
                        )
                    except FileNotFoundError:
                        pass
                    os.fsync(self._root_fd)
            raise


    def _rollback_quarantine_inventory(
        self,
    ) -> dict[str, dict[str, tuple[str, int]]]:
        pattern = re.compile(
            r"^rollback-quarantine\.([0-9a-f]{64})\."
            r"([0-9a-f]{32})\.([0-9a-f]{64})\."
            r"(successor|tombstone)$"
        )
        inventory: dict[str, dict[str, tuple[str, int]]] = {}
        aggregate_bytes = 0
        artifact_count = 0
        with os.scandir(self._terminal_fd) as entries:
            for entry in entries:
                artifact_count += 1
                if artifact_count > _MAX_ROLLBACK_QUARANTINE_ARTIFACTS:
                    raise RollbackCorruptionError(
                        "rollback quarantine artifact bound is exhausted"
                    )
                name = entry.name
                match = pattern.fullmatch(name)
                if match is None:
                    raise RollbackCorruptionError(
                        "rollback quarantine artifact name is invalid"
                    )
                value = entry.stat(follow_symlinks=False)
                suffix = match.group(4)
                size_limit = (
                    _MAX_RECORD_BYTES
                    if suffix == "successor"
                    else _MAX_ROLLBACK_QUARANTINE_TOMBSTONE_BYTES
                )
                if (
                    not stat.S_ISREG(value.st_mode)
                    or stat.S_IMODE(value.st_mode) != 0o600
                    or value.st_nlink != 1
                    or (value.st_uid, value.st_gid) != self._owner
                    or value.st_size <= 0
                    or value.st_size > size_limit
                ):
                    raise RollbackCorruptionError(
                        "rollback quarantine artifact is invalid"
                    )
                base = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
                artifacts = inventory.setdefault(base, {})
                if len(inventory) > _package_limit("_MAX_ROLLBACK_QUARANTINE_PAIRS", _MAX_ROLLBACK_QUARANTINE_PAIRS):
                    raise RollbackCorruptionError(
                        "rollback quarantine pair bound is exhausted"
                    )
                if suffix in artifacts:
                    raise RollbackCorruptionError(
                        "rollback quarantine artifact is duplicated"
                    )
                artifacts[suffix] = (name, value.st_size)
                aggregate_bytes += value.st_size
                if aggregate_bytes > _package_limit("_MAX_ROLLBACK_QUARANTINE_BYTES", _MAX_ROLLBACK_QUARANTINE_BYTES):
                    raise RollbackCorruptionError(
                        "rollback quarantine byte bound is exhausted"
                    )
        return inventory


    def _assert_rollback_quarantine_capacity(
        self,
        quarantine_name: str,
        successor_size: int,
    ) -> None:
        inventory = self._rollback_quarantine_inventory()
        match = re.fullmatch(
            r"rollback-quarantine\.([0-9a-f]{64})\."
            r"([0-9a-f]{32})\.([0-9a-f]{64})\.successor",
            quarantine_name,
        )
        if match is None:
            raise RollbackCorruptionError(
                "rollback quarantine capacity binding is invalid"
            )
        current_base = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
        aggregate_bytes = sum(
            size for artifacts in inventory.values() for _, size in artifacts.values()
        )
        for base, artifacts in inventory.items():
            expected = (
                {"successor"} if base == current_base else {"successor", "tombstone"}
            )
            if set(artifacts) != expected:
                raise RollbackCorruptionError("rollback quarantine pair is incomplete")
        projected_pairs = len(inventory)
        projected_bytes = aggregate_bytes
        if current_base not in inventory:
            projected_pairs += 1
            projected_bytes += successor_size
        projected_bytes += _MAX_ROLLBACK_QUARANTINE_TOMBSTONE_BYTES
        if (
            projected_pairs > _package_limit("_MAX_ROLLBACK_QUARANTINE_PAIRS", _MAX_ROLLBACK_QUARANTINE_PAIRS)
            or projected_bytes > _package_limit("_MAX_ROLLBACK_QUARANTINE_BYTES", _MAX_ROLLBACK_QUARANTINE_BYTES)
        ):
            raise RollbackCorruptionError(
                "rollback quarantine retention bound is exhausted"
            )


    def _assert_generation_not_quarantined(
        self,
        identity: str,
        generation: int,
        record_digest: str,
    ) -> None:
        inventory = self._rollback_quarantine_inventory()
        anchors = self._terminal_quarantine_anchors()
        for base, artifacts in inventory.items():
            anchor = anchors.get(base)
            if anchor is None:
                anchor = self._terminal_anchor_pending_for_base(base)
                if anchor is None:
                    raise RollbackCorruptionError(
                        "rollback quarantine pair has no signed anchor"
                    )
                continue
            if self._rollback_id_blocked(anchor.rollback_id):
                continue
            tombstone = artifacts.get("tombstone")
            if tombstone is None:
                raise RollbackCorruptionError("rollback quarantine pair is incomplete")
            raw = self._read(tombstone[0])
            if raw is None:
                raise RollbackCorruptionError(
                    "rollback quarantine tombstone disappeared"
                )
            payload = self._verify_signed(
                raw,
                "publication-rollback-intent",
            )
            if (
                payload.get("rollback_id") == identity
                and payload.get("successor_generation") == generation
                and payload.get("successor_record_digest") == record_digest
            ):
                raise RollbackConflictError(
                    "journal generation is terminally quarantined"
                )


    def _terminal_quarantine_anchors(
        self,
    ) -> dict[str, RollbackTerminalQuarantineRef]:
        raw = self._read(_ROLLBACK_TERMINAL_ANCHOR_INDEX)
        if raw is None:
            return {}
        try:
            payload = _require_object(
                self._verify_signed(raw, "terminal-quarantine-anchor-index"),
                frozenset(("entries", "schema_version")),
                "terminal quarantine anchor index",
            )
            if (
                payload["schema_version"]
                != "bb.rl.phase5.rollback-terminal-anchor-index.v1"
            ):
                raise RollbackCorruptionError(
                    "terminal quarantine anchor index schema is invalid"
                )
            entries = _require_tuple(
                payload["entries"],
                "terminal quarantine anchor index entries",
            )
            if len(entries) > _package_limit("_MAX_ROLLBACK_QUARANTINE_PAIRS", _MAX_ROLLBACK_QUARANTINE_PAIRS):
                raise RollbackCorruptionError(
                    "terminal quarantine anchor index bound is exhausted"
                )
            refs = tuple(
                _terminal_quarantine_ref_from_object(entry) for entry in entries
            )
        except (RollbackValidationError, RollbackCorruptionError) as error:
            raise RollbackCorruptionError(
                "terminal quarantine anchor index is invalid"
            ) from error
        anchor_keys = tuple(
            f"{canonical_digest(ref.rollback_id.encode())[7:]}."
            f"{ref.transaction_id}.{ref.successor_record_digest[7:]}"
            for ref in refs
        )
        if anchor_keys != tuple(sorted(anchor_keys)) or len(set(anchor_keys)) != len(
            anchor_keys
        ):
            raise RollbackCorruptionError(
                "terminal quarantine anchor index order is invalid"
            )
        return dict(zip(anchor_keys, refs, strict=True))


    def _terminal_anchor_index_bytes(
        self,
        anchors: Mapping[str, RollbackTerminalQuarantineRef],
    ) -> bytes:
        return self._signed_bytes(
            "terminal-quarantine-anchor-index",
            {
                "entries": [
                    anchors[anchor_key].canonical_object()
                    for anchor_key in sorted(anchors)
                ],
                "schema_version": ("bb.rl.phase5.rollback-terminal-anchor-index.v1"),
            },
        )


    @staticmethod
    def _terminal_anchor_key(
        ref: RollbackTerminalQuarantineRef,
    ) -> str:
        return (
            f"{canonical_digest(ref.rollback_id.encode())[7:]}."
            f"{ref.transaction_id}.{ref.successor_record_digest[7:]}"
        )


    def _terminal_anchor_pending_name(
        self,
        ref: RollbackTerminalQuarantineRef,
    ) -> str:
        return f".terminal-anchor-pending.{self._terminal_anchor_key(ref)}"


    def _terminal_anchor_pending_for_base(
        self,
        base: str,
    ) -> RollbackTerminalQuarantineRef | None:
        raw = self._read(f".terminal-anchor-pending.{base}")
        if raw is None:
            return None
        try:
            payload = _require_object(
                self._verify_signed(
                    raw,
                    "terminal-quarantine-anchor-pending",
                ),
                frozenset(("ref", "schema_version")),
                "terminal quarantine pending anchor",
            )
            if (
                payload["schema_version"]
                != "bb.rl.phase5.rollback-terminal-anchor-pending.v1"
            ):
                raise RollbackCorruptionError(
                    "terminal quarantine pending anchor schema is invalid"
                )
            ref = _terminal_quarantine_ref_from_object(payload["ref"])
        except (RollbackValidationError, RollbackCorruptionError) as error:
            raise RollbackCorruptionError(
                "terminal quarantine pending anchor is invalid"
            ) from error
        if self._terminal_anchor_key(ref) != base:
            raise RollbackCorruptionError(
                "terminal quarantine pending anchor binding is invalid"
            )
        return ref


    def _terminal_anchor_pending(
        self,
        ref: RollbackTerminalQuarantineRef,
    ) -> bytes | None:
        raw = self._read(self._terminal_anchor_pending_name(ref))
        if raw is None:
            return None
        expected = self._signed_bytes(
            "terminal-quarantine-anchor-pending",
            {
                "ref": ref.canonical_object(),
                "schema_version": ("bb.rl.phase5.rollback-terminal-anchor-pending.v1"),
            },
        )
        if raw != expected:
            raise RollbackCorruptionError(
                "terminal quarantine pending anchor is invalid"
            )
        return raw


    def _ensure_terminal_anchor_pending(
        self,
        ref: RollbackTerminalQuarantineRef,
    ) -> None:
        if self._terminal_anchor_pending(ref) is not None:
            return
        self._create_immutable(
            self._terminal_anchor_pending_name(ref),
            self._signed_bytes(
                "terminal-quarantine-anchor-pending",
                {
                    "ref": ref.canonical_object(),
                    "schema_version": (
                        "bb.rl.phase5.rollback-terminal-anchor-pending.v1"
                    ),
                },
            ),
        )
        os.fsync(self._root_fd)


    def _clear_terminal_anchor_pending(
        self,
        ref: RollbackTerminalQuarantineRef,
    ) -> None:
        name = self._terminal_anchor_pending_name(ref)
        if self._read(name) is None:
            return
        os.unlink(name, dir_fd=self._root_fd)
        os.fsync(self._root_fd)


    def _publish_terminal_anchor(
        self,
        ref: RollbackTerminalQuarantineRef,
    ) -> None:
        old_raw = self._read(_ROLLBACK_TERMINAL_ANCHOR_INDEX)
        anchors = self._terminal_quarantine_anchors()
        anchor_key = self._terminal_anchor_key(ref)
        existing = anchors.get(anchor_key)
        if existing is not None:
            if existing != ref:
                raise RollbackCorruptionError("terminal quarantine anchor conflicts")
            self._clear_terminal_anchor_pending(ref)
            return
        if len(anchors) >= _package_limit("_MAX_ROLLBACK_QUARANTINE_PAIRS", _MAX_ROLLBACK_QUARANTINE_PAIRS):
            raise RollbackCorruptionError(
                "terminal quarantine anchor index bound is exhausted"
            )
        anchors[anchor_key] = ref
        previous_boundary = self._cleanup_recovery_replace_boundary
        if (
            self._cleanup_forward_active
            or self._cleanup_recovery_checkpoint is not None
        ):
            self._cleanup_recovery_replace_boundary = "terminal_anchor"
        try:
            self._replace(
                _ROLLBACK_TERMINAL_ANCHOR_INDEX,
                self._terminal_anchor_index_bytes(anchors),
                old_raw,
            )
        finally:
            self._cleanup_recovery_replace_boundary = previous_boundary
            self._cleanup_recovery_replace_temp = None
            self._cleanup_recovery_replace_destination = None
        os.fsync(self._root_fd)
        self._clear_terminal_anchor_pending(ref)


    def _recover_pending_terminal_restorations(self) -> None:
        inventory = self._rollback_quarantine_inventory()
        anchors = self._terminal_quarantine_anchors()
        anchored_by_base = {
            self._terminal_anchor_key(ref): ref for ref in anchors.values()
        }
        for base, artifacts in inventory.items():
            if base in anchored_by_base:
                self._clear_terminal_anchor_pending(anchored_by_base[base])
                continue
            if set(artifacts) != {"successor", "tombstone"}:
                raise RollbackCorruptionError("rollback quarantine pair is incomplete")
            identity_digest, transaction_id, digest_hex = base.split(".", 2)
            successor_name = artifacts["successor"][0]
            tombstone_name = artifacts["tombstone"][0]
            held: list[_HeldStoreFile] = []
            try:
                successor = _HeldStoreFile.capture(self, successor_name)
                held.append(successor)
                tombstone = _HeldStoreFile.capture(self, tombstone_name)
                held.append(tombstone)
                payload = _require_object(
                    self._verify_signed(
                        tombstone.raw,
                        "publication-rollback-intent",
                    ),
                    frozenset(
                        (
                            "domain",
                            "prior_commit_identity",
                            "prior_generation",
                            "prior_history_identity",
                            "prior_raw_sha256",
                            "prior_record_digest",
                            "quarantine_name",
                            "relationship",
                            "rollback_id",
                            "schema_version",
                            "state",
                            "successor_generation",
                            "successor_quarantine_identity",
                            "successor_raw_sha256",
                            "successor_record_digest",
                            "transaction_id",
                        )
                    ),
                    "pending terminal rollback restoration",
                )
                if (
                    payload["schema_version"]
                    != "bb.rl.phase5.publication-rollback-intent.v1"
                    or payload["domain"] != self._domain
                    or payload["transaction_id"] != transaction_id
                    or payload["relationship"] != "exact-successor"
                    or payload["state"] != "quarantined"
                    or payload["quarantine_name"] != successor_name
                    or payload["successor_record_digest"] != f"sha256:{digest_hex}"
                    or identity_digest
                    != canonical_digest(str(payload["rollback_id"]).encode())[7:]
                ):
                    raise RollbackCorruptionError(
                        "pending terminal restoration binding is invalid"
                    )
                rollback_id = _require_id(
                    payload["rollback_id"],
                    "rollback id",
                )
                prior_generation = _require_int(
                    payload["prior_generation"],
                    "prior generation",
                    minimum=1,
                )
                successor_generation = _require_int(
                    payload["successor_generation"],
                    "successor generation",
                    minimum=2,
                )
                if successor_generation != prior_generation + 1:
                    raise RollbackCorruptionError(
                        "pending terminal restoration generations diverged"
                    )
                predecessor_name = self._journal_version_name(
                    rollback_id,
                    prior_generation,
                    payload["prior_record_digest"],
                    "history",
                )
                predecessor_commit_name = self._journal_version_name(
                    rollback_id,
                    prior_generation,
                    payload["prior_record_digest"],
                    "commit",
                )
                predecessor = _HeldStoreFile.capture(
                    self,
                    predecessor_name,
                )
                held.append(predecessor)
                predecessor_commit = _HeldStoreFile.capture(
                    self,
                    predecessor_commit_name,
                )
                held.append(predecessor_commit)
                predecessor_record = _journal_from_object(
                    self._verify_signed(
                        predecessor.raw,
                        "journal-record",
                    )
                )
                successor_record = _journal_from_object(
                    self._verify_signed(
                        successor.raw,
                        "journal-record",
                    )
                )
                if (
                    predecessor_record.rollback_id != rollback_id
                    or predecessor_record.generation != prior_generation
                    or predecessor_record.digest != payload["prior_record_digest"]
                    or successor_record.rollback_id != rollback_id
                    or successor_record.generation != successor_generation
                    or successor_record.digest != payload["successor_record_digest"]
                    or successor_record.previous_record_digest
                    != predecessor_record.digest
                    or canonical_digest(predecessor.raw) != payload["prior_raw_sha256"]
                    or canonical_digest(successor.raw)
                    != payload["successor_raw_sha256"]
                    or predecessor.identity
                    != self._decode_recovery_identity(
                        payload["prior_history_identity"],
                        "prior history",
                    )
                    or predecessor_commit.identity
                    != self._decode_recovery_identity(
                        payload["prior_commit_identity"],
                        "prior commit",
                    )
                    or successor.identity
                    != self._decode_recovery_identity(
                        payload["successor_quarantine_identity"],
                        "successor quarantine",
                    )
                ):
                    raise RollbackCorruptionError(
                        "pending terminal restoration authority diverged"
                    )
                self._verify_commit(
                    predecessor_commit.raw,
                    identity=rollback_id,
                    generation=prior_generation,
                    record_digest=predecessor_record.digest,
                )
                capsule = _RollbackRecoveryCapsule(
                    transaction_id=transaction_id,
                    intent=tombstone,
                    predecessor=predecessor,
                    predecessor_commit=predecessor_commit,
                    successor=successor,
                    head_name=self._head_name(rollback_id),
                    displaced_name=(f".{self._domain}.{transaction_id}.displaced-head"),
                    candidate_name=(
                        f".{self._domain}.{transaction_id}.prior-candidate"
                    ),
                    quarantine_name=successor_name,
                    tombstone_name=tombstone_name,
                    successor_history_name=self._journal_version_name(
                        rollback_id,
                        successor_generation,
                        successor_record.digest,
                        "history",
                    ),
                    successor_commit_name=self._journal_version_name(
                        rollback_id,
                        successor_generation,
                        successor_record.digest,
                        "commit",
                    ),
                    state="terminal_complete",
                )
                try:
                    checkpoint_factory = self._cleanup_pending_checkpoint_factory
                    if checkpoint_factory is not None:
                        self._cleanup_recovery_checkpoint = checkpoint_factory(capsule)
                    self._publish_terminal_restoration(capsule)
                except BaseException:
                    predecessor_for_ref = _journal_from_object(
                        self._verify_signed(
                            capsule.predecessor.raw,
                            "journal-record",
                        )
                    )
                    successor_for_ref = _journal_from_object(
                        self._verify_signed(
                            capsule.successor.raw,
                            "journal-record",
                        )
                    )
                    self._publish_terminal_anchor(
                        RollbackTerminalQuarantineRef(
                            capsule.transaction_id,
                            rollback_id,
                            predecessor_for_ref.generation,
                            predecessor_for_ref.digest,
                            successor_for_ref.generation,
                            successor_for_ref.digest,
                            canonical_digest(capsule.successor.raw),
                            successor_name,
                            tombstone_name,
                            canonical_digest(capsule.intent.raw),
                        )
                    )
                    self._block_rollback_id(rollback_id)
                    raise
                held.clear()
                capsule.close()
            finally:
                for item in reversed(held):
                    item.close()

        if self._read(_ROLLBACK_TERMINAL_ANCHOR_INDEX) is None:
            self._replace(
                _ROLLBACK_TERMINAL_ANCHOR_INDEX,
                self._terminal_anchor_index_bytes({}),
                None,
            )
            os.fsync(self._root_fd)


    def _terminal_pair_evidence(
        self,
        anchor: RollbackTerminalQuarantineRef,
        artifacts: Mapping[str, tuple[str, int]],
    ) -> tuple[Mapping[str, Any], RollbackJournalRecord, bytes]:
        if set(artifacts) != {"successor", "tombstone"}:
            raise RollbackCorruptionError("rollback quarantine pair is incomplete")
        successor_name = artifacts["successor"][0]
        tombstone_name = artifacts["tombstone"][0]
        if (
            successor_name != anchor.successor_name
            or tombstone_name != anchor.tombstone_name
        ):
            raise RollbackCorruptionError(
                "terminal rollback pair name binding is invalid"
            )
        successor = _HeldStoreFile.capture(self, successor_name)
        try:
            tombstone = _HeldStoreFile.capture(self, tombstone_name)
            try:
                payload = _require_object(
                    self._verify_signed(
                        tombstone.raw,
                        "publication-rollback-intent",
                    ),
                    frozenset(
                        (
                            "domain",
                            "prior_commit_identity",
                            "prior_generation",
                            "prior_history_identity",
                            "prior_raw_sha256",
                            "prior_record_digest",
                            "quarantine_name",
                            "relationship",
                            "rollback_id",
                            "schema_version",
                            "state",
                            "successor_generation",
                            "successor_quarantine_identity",
                            "successor_raw_sha256",
                            "successor_record_digest",
                            "transaction_id",
                        )
                    ),
                    "terminal rollback quarantine",
                )
                if (
                    payload["schema_version"]
                    != "bb.rl.phase5.publication-rollback-intent.v1"
                    or payload["domain"] != self._domain
                    or payload["transaction_id"] != anchor.transaction_id
                    or payload["rollback_id"] != anchor.rollback_id
                    or payload["relationship"] != "exact-successor"
                    or payload["state"] != "quarantined"
                    or payload["quarantine_name"] != successor_name
                    or payload["prior_generation"] != anchor.predecessor_generation
                    or payload["prior_record_digest"]
                    != anchor.predecessor_record_digest
                    or payload["successor_generation"] != anchor.successor_generation
                    or payload["successor_record_digest"]
                    != anchor.successor_record_digest
                    or payload["successor_raw_sha256"] != anchor.successor_raw_digest
                    or canonical_digest(tombstone.raw) != anchor.tombstone_raw_digest
                    or successor.identity
                    != self._decode_recovery_identity(
                        payload["successor_quarantine_identity"],
                        "successor quarantine",
                    )
                    or canonical_digest(successor.raw) != anchor.successor_raw_digest
                ):
                    raise RollbackCorruptionError(
                        "terminal rollback quarantine binding is invalid"
                    )
                successor_record = _journal_from_object(
                    self._verify_signed(
                        successor.raw,
                        "journal-record",
                    )
                )
                if (
                    successor_record.rollback_id != anchor.rollback_id
                    or successor_record.generation != anchor.successor_generation
                    or successor_record.digest != anchor.successor_record_digest
                    or successor_record.previous_record_digest
                    != anchor.predecessor_record_digest
                ):
                    raise RollbackCorruptionError(
                        "terminal rollback successor model is invalid"
                    )
                successor.revalidate(self)
                tombstone.revalidate(self)
                return payload, successor_record, successor.raw
            finally:
                tombstone.close()
        finally:
            successor.close()


    def _validate_live_terminal_anchor(
        self,
        anchor: RollbackTerminalQuarantineRef,
        payload: Mapping[str, Any],
        successor_record: RollbackJournalRecord,
        successor_raw: bytes,
        *,
        block_on_failure: bool = True,
    ) -> None:
        rollback_id = anchor.rollback_id
        predecessor_name = self._journal_version_name(
            rollback_id,
            anchor.predecessor_generation,
            anchor.predecessor_record_digest,
            "history",
        )
        predecessor_commit_name = self._journal_version_name(
            rollback_id,
            anchor.predecessor_generation,
            anchor.predecessor_record_digest,
            "commit",
        )
        successor_history_name = self._journal_version_name(
            rollback_id,
            anchor.successor_generation,
            anchor.successor_record_digest,
            "history",
        )
        successor_commit_name = self._journal_version_name(
            rollback_id,
            anchor.successor_generation,
            anchor.successor_record_digest,
            "commit",
        )
        marker = self._marker_name(rollback_id)
        if self._read(successor_history_name) is None:
            self._quarantine(
                successor_commit_name,
                marker,
                rollback_id,
            )
            raise RollbackCorruptionError(
                "terminal rollback successor history disappeared"
            )
        if self._read(successor_commit_name) is None:
            self._quarantine(
                self._head_name(rollback_id),
                marker,
                rollback_id,
            )
            raise RollbackCorruptionError(
                "terminal rollback successor commit disappeared"
            )
        held: list[_HeldStoreFile] = []
        try:
            predecessor = _HeldStoreFile.capture(self, predecessor_name)
            held.append(predecessor)
            predecessor_commit = _HeldStoreFile.capture(
                self,
                predecessor_commit_name,
            )
            held.append(predecessor_commit)
            successor_history = _HeldStoreFile.capture(
                self,
                successor_history_name,
            )
            held.append(successor_history)
            successor_commit = _HeldStoreFile.capture(
                self,
                successor_commit_name,
            )
            held.append(successor_commit)
            predecessor_record = _journal_from_object(
                self._verify_signed(predecessor.raw, "journal-record")
            )
            if (
                predecessor_record.rollback_id != rollback_id
                or predecessor_record.generation != anchor.predecessor_generation
                or predecessor_record.digest != anchor.predecessor_record_digest
                or canonical_digest(predecessor.raw) != payload["prior_raw_sha256"]
                or predecessor.identity
                != self._decode_recovery_identity(
                    payload["prior_history_identity"],
                    "prior history",
                )
                or predecessor_commit.identity
                != self._decode_recovery_identity(
                    payload["prior_commit_identity"],
                    "prior commit",
                )
                or successor_history.raw != successor_raw
            ):
                raise RollbackCorruptionError(
                    "terminal rollback root authority diverged"
                )
            self._verify_commit(
                predecessor_commit.raw,
                identity=rollback_id,
                generation=predecessor_record.generation,
                record_digest=predecessor_record.digest,
            )
            self._verify_commit(
                successor_commit.raw,
                identity=rollback_id,
                generation=successor_record.generation,
                record_digest=successor_record.digest,
            )
            restoration = RollbackJournalRecord(
                rollback_id,
                predecessor_record.request_digest,
                predecessor_record.request_payload_ref,
                successor_record.generation + 1,
                predecessor_record.revision,
                predecessor_record.phase,
                predecessor_record.phase_receipts,
                successor_record.digest,
                (*predecessor_record.terminal_quarantine_refs, anchor),
            )
            restoration_raw = self._signed_bytes(
                "journal-record",
                restoration.canonical_object(),
            )
            restoration_history = _HeldStoreFile.capture(
                self,
                self._history_name(restoration),
            )
            held.append(restoration_history)
            restoration_commit = _HeldStoreFile.capture(
                self,
                self._commit_name(restoration),
            )
            held.append(restoration_commit)
            if restoration_history.raw != restoration_raw:
                raise RollbackCorruptionError(
                    "terminal rollback restoration history diverged"
                )
            self._verify_commit(
                restoration_commit.raw,
                identity=rollback_id,
                generation=restoration.generation,
                record_digest=restoration.digest,
            )
            head = _HeldStoreFile.capture(
                self,
                self._head_name(rollback_id),
            )
            held.append(head)
            current = _journal_from_object(
                self._verify_signed(head.raw, "journal-record")
            )
            current_history = _HeldStoreFile.capture(
                self,
                self._history_name(current),
            )
            held.append(current_history)
            current_commit = _HeldStoreFile.capture(
                self,
                self._commit_name(current),
            )
            held.append(current_commit)
            if (
                current.rollback_id != rollback_id
                or anchor not in current.terminal_quarantine_refs
                or current_history.raw != head.raw
            ):
                raise RollbackCorruptionError(
                    "terminal rollback canonical head diverged"
                )
            self._verify_commit(
                current_commit.raw,
                identity=rollback_id,
                generation=current.generation,
                record_digest=current.digest,
            )
            for item in held:
                item.revalidate(self)
        except (OSError, RollbackValidationError, RollbackCorruptionError):
            if block_on_failure:
                self._block_rollback_id(rollback_id)
            raise
        finally:
            for item in reversed(held):
                item.close()


    def _validate_terminal_rollback_quarantines(self) -> None:
        inventory = self._rollback_quarantine_inventory()
        anchors = self._terminal_quarantine_anchors()
        expected_bases = {
            (
                f"{canonical_digest(ref.rollback_id.encode())[7:]}."
                f"{ref.transaction_id}.{ref.successor_record_digest[7:]}"
            ): ref
            for ref in anchors.values()
        }
        if set(inventory) - set(expected_bases):
            raise RollbackCorruptionError(
                "rollback terminal anchor and pair inventory diverged"
            )
        for base, anchor in expected_bases.items():
            blocked = self._rollback_id_blocked(anchor.rollback_id)
            artifacts = inventory.get(base)
            if artifacts is None:
                if blocked:
                    continue
                raise RollbackCorruptionError(
                    "rollback terminal anchor and pair inventory diverged"
                )
            if blocked:
                try:
                    self._terminal_pair_evidence(anchor, artifacts)
                except (
                    OSError,
                    RollbackValidationError,
                    RollbackCorruptionError,
                ):
                    continue
                continue
            payload, successor_record, successor_raw = self._terminal_pair_evidence(
                anchor, artifacts
            )
            self._validate_live_terminal_anchor(
                anchor,
                payload,
                successor_record,
                successor_raw,
            )


    @staticmethod
    def _temp_identity(
        value: os.stat_result,
    ) -> tuple[int, int, int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_uid,
            value.st_gid,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_ctime_ns,
        )

__all__ = ['_PinnedSignedDirectoryRecovery']
