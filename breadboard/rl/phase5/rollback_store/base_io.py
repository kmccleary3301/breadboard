from __future__ import annotations

from ._imports import *
from .models import *
from .publication import *

class _PinnedSignedDirectoryIO:
    def close(self) -> None:
        with self._thread_lock:
            if self._closed:
                return
            self._closed = True
            for descriptor in (
                self._lock_fd,
                self._terminal_fd,
                self._quarantine_fd,
                self._root_fd,
            ):
                try:
                    os.close(descriptor)
                except OSError:
                    pass


    def _validate_root(self) -> None:
        if self._closed:
            raise RollbackCorruptionError("rollback store is closed")
        current = os.stat(self.root, follow_symlinks=False)
        if (
            not stat.S_ISDIR(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o700
            or (current.st_dev, current.st_ino)
            != (self._root_stat.st_dev, self._root_stat.st_ino)
            or (current.st_uid, current.st_gid) != self._owner
        ):
            raise RollbackCorruptionError("rollback store root identity changed")
        if self._quarantine_stat is None or self._quarantine_fd < 0:
            raise RollbackCorruptionError(
                "rollback quarantine directory is unavailable"
            )
        quarantine = os.stat(
            ".quarantine",
            dir_fd=self._root_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(quarantine.st_mode)
            or stat.S_IMODE(quarantine.st_mode) != 0o700
            or (quarantine.st_dev, quarantine.st_ino)
            != (
                self._quarantine_stat.st_dev,
                self._quarantine_stat.st_ino,
            )
            or (quarantine.st_uid, quarantine.st_gid) != self._owner
        ):
            raise RollbackCorruptionError(
                "rollback quarantine directory identity changed"
            )
        if self._domain == "rollback-journal":
            if self._terminal_stat is None or self._terminal_fd < 0:
                raise RollbackCorruptionError(
                    "rollback terminal directory is unavailable"
                )
            terminal = os.stat(
                _ROLLBACK_TERMINAL_DIRECTORY,
                dir_fd=self._root_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(terminal.st_mode)
                or stat.S_IMODE(terminal.st_mode) != 0o700
                or (terminal.st_dev, terminal.st_ino)
                != (
                    self._terminal_stat.st_dev,
                    self._terminal_stat.st_ino,
                )
                or (terminal.st_uid, terminal.st_gid) != self._owner
            ):
                raise RollbackCorruptionError(
                    "rollback terminal directory identity changed"
                )


    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with self._thread_lock:
            self._validate_root()
            fcntl.flock(self._root_fd, fcntl.LOCK_EX)
            try:
                self._validate_root()
                if self._domain == "rollback-journal":
                    self._validate_terminal_rollback_quarantines()
                yield
                self._validate_root()
                if self._domain == "rollback-journal":
                    self._validate_terminal_rollback_quarantines()
            finally:
                fcntl.flock(self._root_fd, fcntl.LOCK_UN)


    def _path_directory_fd(self, name: str) -> int:
        if name.startswith("rollback-quarantine."):
            if self._domain != "rollback-journal" or self._terminal_fd < 0:
                raise RollbackCorruptionError("rollback terminal path is unavailable")
            return self._terminal_fd
        return self._root_fd


    def _open_regular(self, name: str, flags: int, mode: int | None = None) -> int:
        open_flags = flags | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = self._path_directory_fd(name)
        if mode is None:
            fd = os.open(name, open_flags, dir_fd=directory_fd)
        else:
            fd = os.open(name, open_flags, mode, dir_fd=directory_fd)
        value = os.fstat(fd)
        if (
            not stat.S_ISREG(value.st_mode)
            or stat.S_IMODE(value.st_mode) != 0o600
            or value.st_nlink != 1
            or (value.st_uid, value.st_gid) != self._owner
        ):
            os.close(fd)
            raise RollbackCorruptionError(
                "rollback store file must be trusted-owner, regular, "
                "single-link, and 0600"
            )
        return fd


    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("rollback store write made no progress")
            view = view[written:]


    def _read(self, name: str) -> bytes | None:
        self._validate_root()
        try:
            fd = self._open_regular(name, os.O_RDONLY)
        except FileNotFoundError:
            return None
        try:
            value = os.fstat(fd)
            if value.st_size > _MAX_RECORD_BYTES:
                raise RollbackCorruptionError(
                    "rollback store record exceeds size bound"
                )
            remaining = value.st_size + 1
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) != value.st_size:
                raise RollbackCorruptionError(
                    "rollback store record changed during read"
                )
            return payload
        finally:
            os.close(fd)


    def _write_temp(self, name: str, payload: bytes) -> None:
        if len(payload) > _MAX_RECORD_BYTES:
            raise RollbackValidationError("rollback store record exceeds size bound")
        if self._publication_tx is not None:
            self._publication_tx.temps.add(name)
        fd = self._open_regular(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            self._write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        if (
            self._cleanup_recovery_replace_boundary is not None
            and name == self._cleanup_recovery_replace_temp
        ):
            self._cleanup_recovery_fault(
                f"{self._cleanup_recovery_replace_boundary}.after_temp_ready"
            )


    def _create_immutable(self, name: str, payload: bytes) -> None:
        temp = f".{self._domain}.{uuid.uuid4().hex}.immutable"
        linked = False
        try:
            self._write_temp(temp, payload)
            try:
                os.link(
                    temp,
                    name,
                    src_dir_fd=self._root_fd,
                    dst_dir_fd=self._root_fd,
                    follow_symlinks=False,
                )
                linked = True
                if self._publication_tx is not None:
                    self._publication_tx.created.add(name)
            except FileExistsError:
                existing = self._read(name)
                if existing != payload:
                    raise RollbackCorruptionError(
                        "immutable rollback history conflicts"
                    )
            try:
                os.unlink(temp, dir_fd=self._root_fd)
            except FileNotFoundError:
                pass
            os.fsync(self._root_fd)
        except BaseException:
            if linked:
                try:
                    os.unlink(name, dir_fd=self._root_fd)
                    os.fsync(self._root_fd)
                except FileNotFoundError:
                    pass
            raise
        finally:
            try:
                os.unlink(temp, dir_fd=self._root_fd)
            except FileNotFoundError:
                pass


    def _replace_at(
        self,
        directory_fd: int,
        name: str,
        payload: bytes,
        old_payload: bytes | None,
        old_file: _HeldStoreFile,
    ) -> None:
        replacement_state = self._verify_signed(
            payload,
            "publication-rollback-intent",
        )["state"]
        boundary_prefix = {
            "cleanup_pending": "cleanup_intent",
            "quarantined": "terminal_intent",
        }.get(replacement_state)
        if boundary_prefix is None:
            raise RollbackCorruptionError(
                "recovery replacement state is invalid"
            )
        if directory_fd == self._root_fd:
            if old_payload is None or old_file.raw != old_payload:
                raise RollbackCorruptionError(
                    "recovery replacement old payload binding is invalid"
                )
            old_file.revalidate(self)
            self._replace(name, payload, old_payload)
            self._cleanup_recovery_fault(f"{boundary_prefix}.after_publish")
            return
        if len(payload) > _MAX_RECORD_BYTES:
            raise RollbackValidationError("rollback store record exceeds size bound")

        resumed_proof = self._cleanup_recovery_replace_proof
        if resumed_proof is not None and resumed_proof["state"] == "post":
            self._validate_cleanup_replacement_proof(
                resumed_proof,
                label="post stage replacement proof",
            )
            temp = str(resumed_proof["temp"])
            if (
                self._cleanup_replacement_temp_location(temp) != "stage"
                or resumed_proof["destination"] != name
                or resumed_proof["expected_digest"] != canonical_digest(payload)
                or resumed_proof["expected_payload"] != payload.decode("utf-8")
                or self._path_exists_at(directory_fd, temp)
                or old_file.identity != tuple(resumed_proof["identity"])
                or old_file.raw != payload
            ):
                raise RollbackCorruptionError(
                    "post stage replacement binding changed"
                )
            old_file.revalidate(self)
            self._cleanup_recovery_replace_proof = None
            self._cleanup_recovery_replace_temp = None
            self._cleanup_recovery_replace_destination = None
            return

        if old_payload is None or old_file.raw != old_payload:
            raise RollbackCorruptionError(
                "recovery replacement old payload binding is invalid"
            )
        old_file.revalidate(self)
        token = canonical_digest(
            canonical_json_bytes(
                {
                    "name": name,
                    "old_identity": list(old_file.identity),
                    "payload_digest": canonical_digest(payload),
                }
            )
        )[7:39]
        temp = f".intent-replace-{token}"
        intent_temps = {
            candidate
            for candidate in self._bounded_cleanup_staging_names(directory_fd)
            if candidate.startswith(".intent-replace-")
        }
        for candidate in intent_temps:
            if self._cleanup_replacement_temp_location(candidate) != "stage":
                raise RollbackCorruptionError(
                    "recovery replacement temporary name changed"
                )
        if intent_temps - {temp}:
            raise RollbackCorruptionError(
                "recovery replacement temporary name changed"
            )

        base_proof: dict[str, object] = {
            "destination": name,
            "destination_digest": canonical_digest(old_file.raw),
            "destination_identity": list(old_file.identity),
            "expected_digest": canonical_digest(payload),
            "expected_payload": payload.decode("utf-8"),
            "expected_size": len(payload),
            "temp": temp,
        }
        proof = self._cleanup_recovery_replace_proof
        self._cleanup_recovery_replace_temp = temp
        self._cleanup_recovery_replace_destination = name
        temp_file: _HeldStoreFile | None = None
        write_fd = -1
        try:
            if proof is None:
                proof = {
                    **base_proof,
                    "identity": None,
                    "observed_digest": None,
                    "state": "preparing",
                }
                self._cleanup_recovery_replace_proof = proof
                self._cleanup_recovery_fault(
                    f"{boundary_prefix}.before_temp_create"
                )
            else:
                self._validate_cleanup_replacement_proof(
                    proof,
                    label="active stage replacement proof",
                )
                if (
                    self._cleanup_replacement_temp_location(proof["temp"])
                    != "stage"
                    or any(proof.get(key) != value for key, value in base_proof.items())
                ):
                    raise RollbackCorruptionError(
                        "active stage replacement binding changed"
                    )

            for _ in range(2):
                state = str(proof["state"])
                temp_exists = self._path_exists_at(directory_fd, temp)
                if state == "preparing":
                    if temp_exists:
                        temp_file = _HeldStoreFile.capture(
                            self,
                            temp,
                            directory_fd=directory_fd,
                        )
                        if temp_file.raw:
                            raise RollbackCorruptionError(
                                "unsigned stage replacement temporary changed"
                            )
                        self._cleanup_recovery_fault(
                            f"{boundary_prefix}.before_temp_restart_unlink"
                        )
                        temp_file.revalidate(self)
                        os.unlink(temp, dir_fd=directory_fd)
                        os.fsync(directory_fd)
                        self._cleanup_recovery_fault(
                            f"{boundary_prefix}.after_temp_restart_unlink"
                        )
                        temp_file.close()
                        temp_file = None
                        self._cleanup_recovery_fault(
                            f"{boundary_prefix}.before_temp_create"
                        )
                    write_fd = os.open(
                        temp,
                        os.O_RDWR
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=directory_fd,
                    )
                    temp_file = _HeldStoreFile.capture(
                        self,
                        temp,
                        directory_fd=directory_fd,
                    )
                    if temp_file.raw or temp_file.identity[6] != 0:
                        raise RollbackCorruptionError(
                            "created stage replacement temporary is not empty"
                        )
                    proof = {
                        **base_proof,
                        "identity": list(temp_file.identity),
                        "observed_digest": canonical_digest(temp_file.raw),
                        "state": "created",
                    }
                    self._cleanup_recovery_replace_proof = proof
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.after_temp_create"
                    )
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.before_temp_write"
                    )
                    self._write_all(write_fd, payload)
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.after_temp_write"
                    )
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.before_temp_fsync"
                    )
                    os.fsync(write_fd)
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.after_temp_fsync"
                    )
                    os.close(write_fd)
                    write_fd = -1
                    created_identity = temp_file.identity
                    temp_file.close()
                    temp_file = _HeldStoreFile.capture(
                        self,
                        temp,
                        directory_fd=directory_fd,
                    )
                    if (
                        temp_file.identity[:6] != created_identity[:6]
                        or temp_file.raw != payload
                    ):
                        raise RollbackCorruptionError(
                            "stage replacement temporary changed during write"
                        )
                    proof = {
                        **base_proof,
                        "identity": list(temp_file.identity),
                        "observed_digest": canonical_digest(temp_file.raw),
                        "state": "ready",
                    }
                    self._cleanup_recovery_replace_proof = proof
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.after_temp_ready"
                    )
                    break

                if not temp_exists:
                    raise RollbackCorruptionError(
                        "signed stage replacement temporary disappeared"
                    )
                temp_file = _HeldStoreFile.capture(
                    self,
                    temp,
                    directory_fd=directory_fd,
                )
                signed_identity = tuple(proof["identity"])
                if (
                    temp_file.identity != signed_identity
                    or canonical_digest(temp_file.raw) != proof["observed_digest"]
                ):
                    raise RollbackCorruptionError(
                        "created stage replacement temporary changed"
                    )
                if state == "ready":
                    if temp_file.raw != payload:
                        raise RollbackCorruptionError(
                            "ready stage replacement temporary changed"
                        )
                    break
                self._cleanup_recovery_fault(
                    f"{boundary_prefix}.before_temp_restart_unlink"
                )
                temp_file.revalidate(self)
                os.unlink(temp, dir_fd=directory_fd)
                os.fsync(directory_fd)
                self._cleanup_recovery_fault(
                    f"{boundary_prefix}.after_temp_restart_unlink"
                )
                temp_file.close()
                temp_file = None
                proof = {
                    **base_proof,
                    "identity": None,
                    "observed_digest": None,
                    "state": "preparing",
                }
                self._cleanup_recovery_replace_proof = proof
                self._cleanup_recovery_fault(
                    f"{boundary_prefix}.before_temp_create"
                )
            else:
                raise RollbackCorruptionError(
                    "stage replacement temporary restart bound exhausted"
                )

            assert temp_file is not None
            self._cleanup_recovery_fault(f"{boundary_prefix}.before_publish")
            old_file.revalidate(self)
            temp_file.revalidate(self)
            os.replace(
                temp,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
            self._cleanup_recovery_fault(f"{boundary_prefix}.after_publish")
            self._cleanup_recovery_replace_proof = None
            self._cleanup_recovery_replace_temp = None
            self._cleanup_recovery_replace_destination = None
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            if temp_file is not None:
                temp_file.close()


    def _replace_recovery_root(
        self,
        name: str,
        payload: bytes,
        old_payload: bytes | None,
        *,
        boundary: str,
        temp: str,
    ) -> None:
        old_file: _HeldStoreFile | None = None
        temp_file: _HeldStoreFile | None = None
        write_fd = -1
        try:
            resumed_proof = self._cleanup_recovery_replace_proof
            if (
                resumed_proof is not None
                and resumed_proof["state"] == "post"
            ):
                self._validate_cleanup_replacement_proof(
                    resumed_proof,
                    label="post cleanup replacement proof",
                )
                if (
                    resumed_proof["destination"] != name
                    or resumed_proof["temp"] != temp
                    or resumed_proof["expected_digest"]
                    != canonical_digest(payload)
                    or self._path_exists_at(self._root_fd, temp)
                ):
                    raise RollbackCorruptionError(
                        "post cleanup replacement binding changed"
                    )
                installed = _HeldStoreFile.capture(self, name)
                try:
                    if (
                        installed.identity != tuple(resumed_proof["identity"])
                        or installed.raw != payload
                    ):
                        raise RollbackCorruptionError(
                            "post cleanup replacement destination changed"
                        )
                    installed.revalidate(self)
                finally:
                    installed.close()
                self._cleanup_recovery_replace_proof = None
                self._cleanup_recovery_replace_temp = None
                self._cleanup_recovery_replace_destination = None
                return
            if old_payload is not None:
                old_file = _HeldStoreFile.capture(self, name)
                if old_file.raw != old_payload:
                    raise RollbackCorruptionError(
                        "recovery root replacement old payload changed"
                    )
                old_file.revalidate(self)
            elif self._path_exists_at(self._root_fd, name):
                raise RollbackCorruptionError(
                    "recovery root replacement destination appeared"
                )
            base_proof: dict[str, object] = {
                "destination": name,
                "destination_digest": (
                    None
                    if old_file is None
                    else canonical_digest(old_file.raw)
                ),
                "destination_identity": (
                    None if old_file is None else list(old_file.identity)
                ),
                "expected_digest": canonical_digest(payload),
                "expected_payload": payload.decode("utf-8"),
                "expected_size": len(payload),
                "temp": temp,
            }
            proof = self._cleanup_recovery_replace_proof
            if proof is None:
                proof = {
                    **base_proof,
                    "identity": None,
                    "observed_digest": None,
                    "state": "preparing",
                }
                self._cleanup_recovery_replace_proof = proof
                self._cleanup_recovery_fault(f"{boundary}.before_temp_create")
            else:
                self._validate_cleanup_replacement_proof(
                    proof,
                    label="active cleanup replacement proof",
                )
                if any(proof.get(key) != value for key, value in base_proof.items()):
                    raise RollbackCorruptionError(
                        "active cleanup replacement binding changed"
                    )
            for _ in range(2):
                state = str(proof["state"])
                temp_exists = self._path_exists_at(self._root_fd, temp)
                if state == "preparing":
                    if temp_exists:
                        raise RollbackCorruptionError(
                            "uncreated recovery replacement temporary appeared"
                        )
                    write_fd = os.open(
                        temp,
                        os.O_RDWR
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=self._root_fd,
                    )
                    temp_file = _HeldStoreFile.capture(self, temp)
                    if temp_file.raw or temp_file.identity[6] != 0:
                        raise RollbackCorruptionError(
                            "created recovery replacement temporary is not empty"
                        )
                    proof = {
                        **base_proof,
                        "identity": list(temp_file.identity),
                        "observed_digest": canonical_digest(temp_file.raw),
                        "state": "created",
                    }
                    self._cleanup_recovery_replace_proof = proof
                    self._cleanup_recovery_fault(f"{boundary}.after_temp_create")
                    self._cleanup_recovery_fault(f"{boundary}.before_temp_write")
                    self._write_all(write_fd, payload)
                    self._cleanup_recovery_fault(f"{boundary}.after_temp_write")
                    self._cleanup_recovery_fault(f"{boundary}.before_temp_fsync")
                    os.fsync(write_fd)
                    self._cleanup_recovery_fault(f"{boundary}.after_temp_fsync")
                    os.close(write_fd)
                    write_fd = -1
                    created_identity = temp_file.identity
                    temp_file.close()
                    temp_file = _HeldStoreFile.capture(self, temp)
                    if (
                        temp_file.identity[:6] != created_identity[:6]
                        or temp_file.raw != payload
                    ):
                        raise RollbackCorruptionError(
                            "recovery replacement temporary changed during write"
                        )
                    proof = {
                        **base_proof,
                        "identity": list(temp_file.identity),
                        "observed_digest": canonical_digest(temp_file.raw),
                        "state": "ready",
                    }
                    self._cleanup_recovery_replace_proof = proof
                    self._cleanup_recovery_fault(f"{boundary}.after_temp_ready")
                    break
                if not temp_exists:
                    raise RollbackCorruptionError(
                        "signed recovery replacement temporary disappeared"
                    )
                temp_file = _HeldStoreFile.capture(self, temp)
                signed_identity = tuple(proof["identity"])
                if (
                    temp_file.identity != signed_identity
                    or canonical_digest(temp_file.raw) != proof["observed_digest"]
                ):
                    raise RollbackCorruptionError(
                        "created recovery replacement temporary changed"
                    )
                if state == "ready":
                    if (
                        temp_file.identity != signed_identity
                        or temp_file.raw != payload
                    ):
                        raise RollbackCorruptionError(
                            "ready recovery replacement temporary changed"
                        )
                    break
                self._cleanup_recovery_fault(
                    f"{boundary}.before_temp_restart_unlink"
                )
                temp_file.revalidate(self)
                os.unlink(temp, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
                self._cleanup_recovery_fault(
                    f"{boundary}.after_temp_restart_unlink"
                )
                temp_file.close()
                temp_file = None
                proof = {
                    **base_proof,
                    "identity": None,
                    "observed_digest": None,
                    "state": "preparing",
                }
                self._cleanup_recovery_replace_proof = proof
                self._cleanup_recovery_fault(f"{boundary}.before_temp_create")
            else:
                raise RollbackCorruptionError(
                    "recovery replacement temporary restart bound exhausted"
                )
            assert temp_file is not None
            self._cleanup_recovery_fault(f"{boundary}.before_replace")
            if old_file is not None:
                old_file.revalidate(self)
            elif self._path_exists_at(self._root_fd, name):
                raise RollbackCorruptionError(
                    "recovery root replacement destination appeared"
                )
            temp_file.revalidate(self)
            os.replace(
                temp,
                name,
                src_dir_fd=self._root_fd,
                dst_dir_fd=self._root_fd,
            )
            os.fsync(self._root_fd)
            self._cleanup_recovery_fault(f"{boundary}.after_replace")
            self._cleanup_recovery_replace_proof = None
            self._cleanup_recovery_replace_temp = None
            self._cleanup_recovery_replace_destination = None
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            if temp_file is not None:
                temp_file.close()
            if old_file is not None:
                old_file.close()


    def _replace(self, name: str, payload: bytes, old_payload: bytes | None) -> None:
        if self._publication_tx is not None:
            self._publication_tx.capture_replaced(name, old_payload)
        recovery_boundary = self._cleanup_recovery_replace_boundary
        if recovery_boundary is None:
            temp = f".{self._domain}.{uuid.uuid4().hex}.tmp"
        else:
            resumed_proof = self._cleanup_recovery_replace_proof
            if (
                resumed_proof is not None
                and resumed_proof["state"] == "post"
                and (
                    resumed_proof["destination"] != name
                    or resumed_proof["expected_digest"]
                    != canonical_digest(payload)
                )
            ):
                self._validate_cleanup_replacement_proof(
                    resumed_proof,
                    label="completed cleanup replacement proof",
                )
                completed_temp = str(resumed_proof["temp"])
                if self._path_exists_at(self._root_fd, completed_temp):
                    raise RollbackCorruptionError(
                        "completed cleanup replacement temporary survived"
                    )
                completed = _HeldStoreFile.capture(
                    self,
                    str(resumed_proof["destination"]),
                )
                try:
                    expected_payload = str(
                        resumed_proof["expected_payload"]
                    ).encode("utf-8")
                    if (
                        completed.identity
                        != tuple(resumed_proof["identity"])
                        or completed.raw != expected_payload
                        or canonical_digest(completed.raw)
                        != resumed_proof["expected_digest"]
                    ):
                        raise RollbackCorruptionError(
                            "completed cleanup replacement destination changed"
                        )
                    completed.revalidate(self)
                finally:
                    completed.close()
                self._cleanup_recovery_replace_proof = None
                self._cleanup_recovery_replace_temp = None
                self._cleanup_recovery_replace_destination = None
                resumed_proof = None
            if (
                resumed_proof is not None
                and resumed_proof["state"] == "post"
            ):
                temp = str(resumed_proof["temp"])
            else:
                token = canonical_digest(
                    canonical_json_bytes(
                        {
                            "name": name,
                            "old_digest": (
                                None
                                if old_payload is None
                                else canonical_digest(old_payload)
                            ),
                            "payload_digest": canonical_digest(payload),
                        }
                    )
                )[7:39]
                temp = f".{self._domain}.{token}.tmp"
            self._cleanup_recovery_replace_temp = temp
            self._cleanup_recovery_replace_destination = name
            ready_root_temps = {
                candidate
                for candidate in self._bounded_root_names()
                if re.fullmatch(
                    rf"\.{re.escape(self._domain)}\.[0-9a-f]{{32}}\.tmp",
                    candidate,
                )
                is not None
            }
            if ready_root_temps - {temp}:
                raise RollbackCorruptionError(
                    "recovery root replacement temporary name changed"
                )
            self._replace_recovery_root(
                name,
                payload,
                old_payload,
                boundary=recovery_boundary,
                temp=temp,
            )
            return
        replaced = False
        old_file: _HeldStoreFile | None = None
        temp_file: _HeldStoreFile | None = None
        try:
            if not self._path_exists_at(self._root_fd, temp):
                self._write_temp(temp, payload)
            temp_file = _HeldStoreFile.capture(self, temp)
            if temp_file.raw != payload:
                raise RollbackCorruptionError(
                    "recovery root replacement temporary payload changed"
                )
            if old_payload is not None:
                old_file = _HeldStoreFile.capture(self, name)
                if old_file.raw != old_payload:
                    raise RollbackCorruptionError(
                        "recovery root replacement old payload changed"
                    )
            if old_file is not None:
                old_file.revalidate(self)
            elif self._path_exists_at(self._root_fd, name):
                raise RollbackCorruptionError(
                    "recovery root replacement destination appeared"
                )
            temp_file.revalidate(self)
            os.replace(
                temp,
                name,
                src_dir_fd=self._root_fd,
                dst_dir_fd=self._root_fd,
            )
            replaced = True
            if self._publication_tx is not None:
                self._publication_tx.mark_replaced(name)
            os.fsync(self._root_fd)
            if recovery_boundary is not None:
                self._cleanup_recovery_fault(
                    f"{recovery_boundary}.after_replace"
                )
                self._cleanup_recovery_replace_temp = None
                self._cleanup_recovery_replace_destination = None
        except BaseException:
            if replaced and recovery_boundary is None:
                if old_payload is None:
                    os.unlink(name, dir_fd=self._root_fd)
                else:
                    rollback = f".{self._domain}.{uuid.uuid4().hex}.rollback"
                    try:
                        self._write_temp(rollback, old_payload)
                        os.replace(
                            rollback,
                            name,
                            src_dir_fd=self._root_fd,
                            dst_dir_fd=self._root_fd,
                        )
                    finally:
                        try:
                            os.unlink(rollback, dir_fd=self._root_fd)
                        except FileNotFoundError:
                            pass
                os.fsync(self._root_fd)
            raise
        finally:
            if old_file is not None:
                old_file.close()
            if temp_file is not None:
                temp_file.close()
            if recovery_boundary is None:
                try:
                    os.unlink(temp, dir_fd=self._root_fd)
                except FileNotFoundError:
                    pass


    def _commit_bytes(
        self, identity: str, generation: int, record_digest: str
    ) -> bytes:
        return self._signed_bytes(
            "generation-commit",
            {
                "generation": generation,
                "identity": identity,
                "record_digest": record_digest,
                "schema_version": "bb.rl.phase5.rollback-generation-commit.v1",
            },
        )


    def _verify_commit(
        self,
        raw: bytes,
        *,
        identity: str,
        generation: int,
        record_digest: str,
    ) -> None:
        payload = _require_object(
            self._verify_signed(raw, "generation-commit"),
            frozenset(("generation", "identity", "record_digest", "schema_version")),
            "rollback generation commit",
        )
        if (
            payload["schema_version"] != "bb.rl.phase5.rollback-generation-commit.v1"
            or payload["identity"] != identity
            or payload["generation"] != generation
            or payload["record_digest"] != record_digest
        ):
            raise RollbackCorruptionError("rollback generation commit mismatch")


    def _publish_versioned(
        self,
        *,
        head_name: str,
        history_name: str,
        commit_name: str,
        identity: str,
        generation: int,
        record_digest: str,
        signed_record: bytes,
        old_head: bytes | None,
    ) -> None:
        if self._domain == "rollback-journal":
            self._assert_generation_not_quarantined(
                identity,
                generation,
                record_digest,
            )
        self._create_immutable(history_name, signed_record)
        if self._publication_tx is not None:
            self._publication_tx.revalidate()
        self._replace(head_name, signed_record, old_head)
        try:
            self._create_immutable(
                commit_name,
                self._commit_bytes(identity, generation, record_digest),
            )
        except BaseException:
            if old_head is None:
                try:
                    os.unlink(head_name, dir_fd=self._root_fd)
                except FileNotFoundError:
                    pass
                os.fsync(self._root_fd)
            else:
                self._replace(head_name, old_head, signed_record)
            raise


    def _signed_bytes(self, kind: str, payload: Mapping[str, Any]) -> bytes:
        payload_bytes = canonical_json_bytes(payload)
        payload_digest = canonical_digest(payload_bytes)
        mac_input = (
            canonical_json_bytes(
                {
                    "domain": self._domain,
                    "kind": kind,
                    "payload_digest": payload_digest,
                }
            )
            + payload_bytes
        )
        authority_hmac = hmac.new(
            self._authority_key, mac_input, hashlib.sha256
        ).hexdigest()
        return canonical_json_bytes(
            {
                "authority_hmac": authority_hmac,
                "domain": self._domain,
                "kind": kind,
                "payload": payload,
                "payload_digest": payload_digest,
                "schema_version": "bb.rl.phase5.rollback-signed-record.v1",
            }
        )


    def _verify_signed(self, raw: bytes, kind: str) -> Mapping[str, Any]:
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RollbackCorruptionError(
                "rollback store record is not canonical JSON"
            ) from error
        outer = _require_object(
            decoded,
            frozenset(
                (
                    "authority_hmac",
                    "domain",
                    "kind",
                    "payload",
                    "payload_digest",
                    "schema_version",
                )
            ),
            "signed rollback record",
        )
        if raw != canonical_json_bytes(decoded):
            raise RollbackCorruptionError(
                "rollback store record is not canonically encoded"
            )
        if (
            outer["schema_version"] != "bb.rl.phase5.rollback-signed-record.v1"
            or outer["domain"] != self._domain
            or outer["kind"] != kind
            or type(outer["authority_hmac"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", outer["authority_hmac"])
        ):
            raise RollbackCorruptionError("rollback store signed identity is invalid")
        payload = outer["payload"]
        if type(payload) is not dict:
            raise RollbackCorruptionError("rollback store signed payload is invalid")
        payload_bytes = canonical_json_bytes(payload)
        expected_digest = canonical_digest(payload_bytes)
        if outer["payload_digest"] != expected_digest:
            raise RollbackCorruptionError("rollback store payload digest mismatch")
        mac_input = (
            canonical_json_bytes(
                {
                    "domain": self._domain,
                    "kind": kind,
                    "payload_digest": expected_digest,
                }
            )
            + payload_bytes
        )
        expected_hmac = hmac.new(
            self._authority_key, mac_input, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_hmac, outer["authority_hmac"]):
            raise RollbackCorruptionError("rollback store authority HMAC mismatch")
        return payload


    def _block_identity(
        self,
        marker: str,
        identity: str,
    ) -> None:
        marker_payload = self._signed_bytes(
            "corruption-marker",
            {"domain": self._domain, "identity": identity},
        )
        old_marker = self._read(marker)
        if old_marker is None:
            self._replace(marker, marker_payload, None)
        elif old_marker != marker_payload:
            raise RollbackCorruptionError(
                "rollback corruption marker identity diverged"
            )
        os.fsync(self._root_fd)


    def _block_rollback_id(self, rollback_id: str) -> None:
        rollback_id = _require_id(rollback_id, "rollback id")
        self._block_identity(
            f"journal.{rollback_id}.blocked",
            rollback_id,
        )


    def _rollback_id_blocked(self, rollback_id: str) -> bool:
        rollback_id = _require_id(rollback_id, "rollback id")
        raw = self._read(f"journal.{rollback_id}.blocked")
        if raw is None:
            return False
        payload = _require_object(
            self._verify_signed(raw, "corruption-marker"),
            frozenset(("domain", "identity")),
            "rollback corruption marker",
        )
        if payload != {
            "domain": "rollback-journal",
            "identity": rollback_id,
        }:
            raise RollbackCorruptionError(
                "rollback corruption marker identity is invalid"
            )
        return True


    def _blocked(self, marker: str) -> bool:
        raw = self._read(marker)
        if raw is None:
            return False
        self._verify_signed(raw, "corruption-marker")
        return True


    def _quarantine(self, name: str, marker: str, identity: str) -> None:
        self._block_identity(marker, identity)
        destination = f"{self._domain}.{canonical_digest(identity.encode())[7:]}.{uuid.uuid4().hex}.corrupt"
        try:
            os.rename(
                name,
                destination,
                src_dir_fd=self._root_fd,
                dst_dir_fd=self._quarantine_fd,
            )
        except FileNotFoundError:
            pass
        os.fsync(self._quarantine_fd)
        os.fsync(self._root_fd)

__all__ = ['_PinnedSignedDirectoryIO']
