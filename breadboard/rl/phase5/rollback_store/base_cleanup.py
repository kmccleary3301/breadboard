from __future__ import annotations

from ._imports import *
from .models import *
from .publication import *

class _PinnedSignedDirectoryCleanup:
    def _scan_abandoned_temp_names(
        self,
        pattern: re.Pattern[str],
        *,
        collect: bool,
    ) -> tuple[
        tuple[int, int, int, int, int, int],
        list[str],
        list[str],
    ]:
        entry_count = 0
        aggregate_name_bytes = 0
        name_digest_sum = 0
        name_digest_xor = 0
        owned_count = 0
        owned_name_bytes = 0
        names: list[str] = []
        root_names: list[str] = []
        owned_prefix = f".{self._domain}."
        owned_suffixes = (
            ".immutable",
            ".rollback",
            ".tmp",
            ".transaction-rollback",
            ".displaced-head",
            ".prior-candidate",
        )
        with os.scandir(self._root_fd) as entries:
            for entry in entries:
                name = entry.name
                if type(name) is not str:
                    raise RollbackCorruptionError(
                        "rollback store root entry name is invalid"
                    )
                try:
                    encoded_name = name.encode("utf-8")
                except UnicodeEncodeError as error:
                    raise RollbackCorruptionError(
                        "rollback store root entry name is not UTF-8"
                    ) from error
                entry_count += 1
                aggregate_name_bytes += len(encoded_name)
                if (
                    entry_count > _package_limit("_MAX_ROOT_ENTRIES", _MAX_ROOT_ENTRIES)
                    or aggregate_name_bytes > _package_limit("_MAX_ROOT_NAME_BYTES", _MAX_ROOT_NAME_BYTES)
                ):
                    raise RollbackCorruptionError(
                        "rollback store root enumeration bound is exhausted"
                    )
                name_digest = int.from_bytes(
                    hashlib.sha256(encoded_name).digest(),
                    "big",
                )
                name_digest_sum = (name_digest_sum + name_digest) % (1 << 256)
                name_digest_xor ^= name_digest
                if collect:
                    root_names.append(name)
                match = pattern.fullmatch(name)
                looks_owned = name.startswith(owned_prefix) and name.endswith(
                    owned_suffixes
                )
                if looks_owned and match is None:
                    raise RollbackCorruptionError(
                        "abandoned rollback temp name is invalid"
                    )
                if match is None:
                    continue
                owned_count += 1
                owned_name_bytes += len(encoded_name)
                if (
                    owned_count > _MAX_ABANDONED_TEMPS
                    or owned_name_bytes > _MAX_ABANDONED_TEMP_NAME_BYTES
                ):
                    raise RollbackCorruptionError(
                        "abandoned rollback temp bound is exhausted"
                    )
                if collect:
                    names.append(name)
        return (
            (
                entry_count,
                aggregate_name_bytes,
                name_digest_sum,
                name_digest_xor,
                owned_count,
                owned_name_bytes,
            ),
            names,
            root_names,
        )


    def _bounded_root_names(self) -> tuple[str, ...]:
        def scan(
            *, collect: bool
        ) -> tuple[
            tuple[int, int, int, int],
            list[str],
        ]:
            entry_count = 0
            aggregate_name_bytes = 0
            name_digest_sum = 0
            name_digest_xor = 0
            names: list[str] = []
            with os.scandir(self._root_fd) as entries:
                for entry in entries:
                    name = entry.name
                    if type(name) is not str:
                        raise RollbackCorruptionError(
                            "rollback store root entry name is invalid"
                        )
                    try:
                        encoded_name = name.encode("utf-8")
                    except UnicodeEncodeError as error:
                        raise RollbackCorruptionError(
                            "rollback store root entry name is not UTF-8"
                        ) from error
                    entry_count += 1
                    aggregate_name_bytes += len(encoded_name)
                    if (
                        entry_count > _package_limit("_MAX_ROOT_ENTRIES", _MAX_ROOT_ENTRIES)
                        or aggregate_name_bytes > _package_limit("_MAX_ROOT_NAME_BYTES", _MAX_ROOT_NAME_BYTES)
                    ):
                        raise RollbackCorruptionError(
                            "rollback store root enumeration bound is exhausted"
                        )
                    name_digest = int.from_bytes(
                        hashlib.sha256(encoded_name).digest(),
                        "big",
                    )
                    name_digest_sum = (name_digest_sum + name_digest) % (1 << 256)
                    name_digest_xor ^= name_digest
                    if collect:
                        names.append(name)
            return (
                (
                    entry_count,
                    aggregate_name_bytes,
                    name_digest_sum,
                    name_digest_xor,
                ),
                names,
            )

        expected_scan, _ = scan(collect=False)
        observed_scan, names = scan(collect=True)
        if observed_scan != expected_scan or len(set(names)) != len(names):
            raise RollbackCorruptionError(
                "rollback store root changed during bounded enumeration"
            )
        return tuple(sorted(names))


    @property
    def _cleanup_staging_name(self) -> str:
        return f".{self._domain}.cleanup-staging"


    def _cleanup_fault(self, boundary: str) -> None:
        hook = _package_value("_TEST_CLEANUP_FAULT_HOOK", _TEST_CLEANUP_FAULT_HOOK)
        if hook is None:
            return
        try:
            hook(boundary)
        except _CleanupInjectedCrash:
            raise
        except BaseException as error:
            raise _CleanupInjectedCrash(boundary) from error


    def _cleanup_recovery_fault(self, boundary: str) -> None:
        checkpoint = self._cleanup_recovery_checkpoint
        if not self._cleanup_forward_active and checkpoint is None:
            return
        is_after = boundary.rsplit(".", 1)[-1].startswith("after")
        if checkpoint is not None:
            checkpoint(boundary, True)
        self._cleanup_fault(f"forward.recovery.{boundary}")
        if not is_after and checkpoint is not None:
            checkpoint(boundary, False)


    @staticmethod
    def _cleanup_authority_temp_name(name: str) -> str:
        if name == _CLEANUP_PREPARING_NAME:
            return _CLEANUP_PREPARING_TEMP_NAME
        if name == _CLEANUP_COMMITTED_NAME:
            return _CLEANUP_COMMITTED_TEMP_NAME
        if name == _CLEANUP_RECEIPT_NAME:
            return _CLEANUP_RECEIPT_TEMP_NAME
        raise RollbackCorruptionError("cleanup authority name is invalid")


    @staticmethod
    def _cleanup_stage_identity(
        value: os.stat_result,
    ) -> tuple[int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_uid,
            value.st_gid,
            stat.S_IMODE(value.st_mode),
            value.st_nlink,
        )


    def _cleanup_transaction_id(
        self,
        stage_identity: Sequence[int],
        root_identity: Sequence[int],
        root_names: Sequence[str],
    ) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "domain": self._domain,
                    "root_identity": list(root_identity),
                    "root_names": list(root_names),
                    "stage_identity": list(stage_identity),
                }
            )
        ).hexdigest()


    def _rejoin_cleanup_stage(
        self,
        directory_fd: int,
        stage_identity: Sequence[int],
        *,
        expected_names: set[str] | None = None,
    ) -> None:
        descriptor_value = os.fstat(directory_fd)
        path_value = os.stat(
            self._cleanup_staging_name,
            dir_fd=self._root_fd,
            follow_symlinks=False,
        )
        expected = tuple(stage_identity)
        if (
            len(expected) != 6
            or any(type(part) is not int or part < 0 for part in expected)
            or not stat.S_ISDIR(descriptor_value.st_mode)
            or not stat.S_ISDIR(path_value.st_mode)
            or self._cleanup_stage_identity(descriptor_value)[:5] != expected[:5]
            or self._cleanup_stage_identity(path_value)[:5] != expected[:5]
            or descriptor_value.st_nlink != path_value.st_nlink
            or descriptor_value.st_nlink < expected[5]
            or (
                expected_names is not None
                and descriptor_value.st_nlink
                not in {expected[5], expected[5] + len(expected_names)}
            )
            or expected[2:4] != tuple(self._owner)
            or expected[4] != 0o700
            or expected[5] < 2
            or expected[0] != self._root_stat.st_dev
        ):
            raise RollbackCorruptionError(
                "abandoned cleanup staging directory binding changed: "
                f"expected={expected!r}, "
                f"descriptor={self._cleanup_stage_identity(descriptor_value)!r}, "
                f"path={self._cleanup_stage_identity(path_value)!r}"
            )
        if (
            expected_names is not None
            and set(self._bounded_cleanup_staging_names(directory_fd)) != expected_names
        ):
            raise RollbackCorruptionError(
                "cleanup staging inventory binding is invalid"
            )


    def _dispose_cleanup_temp(
        self,
        directory_fd: int,
        name: str,
        stage_identity: Sequence[int],
        *,
        expected_names: set[str],
        prefix: str,
    ) -> None:
        temp = self._validate_cleanup_authority_file(directory_fd, name)
        try:
            if len(temp.raw) > _MAX_CLEANUP_MANIFEST_BYTES:
                raise RollbackCorruptionError(
                    "cleanup temporary authority exceeds bound"
                )
            self._rejoin_cleanup_stage(
                directory_fd,
                stage_identity,
                expected_names=expected_names,
            )
            temp.revalidate(self)
            self._remove_cleanup_authority(
                directory_fd,
                name,
                prefix=prefix,
                stage_identity=stage_identity,
                expected_names=expected_names,
                target=temp,
            )
            self._sync_cleanup_stage(directory_fd, prefix=prefix)
        finally:
            temp.close()


    def _cleanup_write_all(self, fd: int, payload: bytes, *, prefix: str) -> None:
        view = memoryview(payload)
        chunk_index = 0
        while view:
            self._cleanup_fault(f"{prefix}.before_write_chunk.{chunk_index}")
            before = len(view)
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("rollback store write made no progress")
            view = view[written:]
            self._cleanup_fault(f"{prefix}.after_write_chunk.{chunk_index}")
            if written < before:
                self._cleanup_fault(f"{prefix}.after_short_write.{chunk_index}")
            chunk_index += 1


    def _cleanup_stage_identity_now(self, directory_fd: int) -> tuple[int, ...]:
        return self._cleanup_stage_identity(os.fstat(directory_fd))


    def _remove_cleanup_stage(
        self,
        directory_fd: int,
        *,
        prefix: str,
        stage_identity: Sequence[int],
    ) -> None:
        self._rejoin_cleanup_stage(
            directory_fd,
            stage_identity,
            expected_names=set(),
        )
        self._cleanup_fault(f"{prefix}.before_stage_rmdir")
        self._rejoin_cleanup_stage(
            directory_fd,
            stage_identity,
            expected_names=set(),
        )
        os.rmdir(self._cleanup_staging_name, dir_fd=self._root_fd)
        self._cleanup_fault(f"{prefix}.after_stage_rmdir")
        self._cleanup_fault(f"{prefix}.before_parent_fsync")
        os.fsync(self._root_fd)
        self._cleanup_fault(f"{prefix}.after_parent_fsync")


    def _remove_cleanup_authority(
        self,
        directory_fd: int,
        name: str,
        *,
        prefix: str,
        stage_identity: Sequence[int] | None = None,
        expected_names: set[str] | None = None,
        target: _HeldStoreFile | None = None,
        hook_name: str | None = None,
    ) -> None:
        if stage_identity is not None and expected_names is None:
            raise RollbackCorruptionError(
                "cleanup mutation inventory is unavailable"
            )
        owns_target = target is None
        if target is None:
            try:
                target = _HeldStoreFile.capture(
                    self,
                    name,
                    directory_fd=directory_fd,
                )
            except (OSError, RollbackCorruptionError) as error:
                raise RollbackCorruptionError(
                    "cleanup mutation target could not be held"
                ) from error
        try:
            boundary_name = name if hook_name is None else hook_name
            assert target is not None
            self._cleanup_fault(f"{prefix}.before_unlink.{boundary_name}")
            if stage_identity is not None:
                assert expected_names is not None
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=expected_names,
                )
            target.revalidate(self)
            os.unlink(name, dir_fd=directory_fd)
            os.fsync(directory_fd)
            self._cleanup_fault(f"{prefix}.after_unlink.{boundary_name}")
        finally:
            if owns_target:
                assert target is not None
                target.close()


    def _sync_cleanup_stage(self, directory_fd: int, *, prefix: str) -> None:
        self._cleanup_fault(f"{prefix}.before_stage_fsync")
        os.fsync(directory_fd)
        self._cleanup_fault(f"{prefix}.after_stage_fsync")


    def _sync_cleanup_root(self, *, prefix: str) -> None:
        self._cleanup_fault(f"{prefix}.before_root_fsync")
        os.fsync(self._root_fd)
        self._cleanup_fault(f"{prefix}.after_root_fsync")


    def _validate_cleanup_root_inventory(
        self,
        preparing: Mapping[str, object],
        *,
        staged_names: set[str],
        permitted_additions: set[str] | None = None,
    ) -> None:
        expected = set(preparing["root_names"]) - staged_names | {
            self._cleanup_staging_name
        }
        if permitted_additions is not None:
            expected |= permitted_additions
        if set(self._bounded_root_names()) != expected:
            raise RollbackCorruptionError("cleanup root inventory binding is invalid")


    def _validate_cleanup_candidate_name(self, name: object) -> str:
        if type(name) is not str or name in (
            "",
            ".",
            "..",
            _CLEANUP_PREPARING_NAME,
            _CLEANUP_COMMITTED_NAME,
            _CLEANUP_PREPARING_TEMP_NAME,
            _CLEANUP_COMMITTED_TEMP_NAME,
            _CLEANUP_RECEIPT_NAME,
            _CLEANUP_RECEIPT_TEMP_NAME,
            self._cleanup_staging_name,
        ):
            raise RollbackCorruptionError("cleanup candidate name is invalid")
        if (
            os.path.isabs(name)
            or "/" in name
            or (os.altsep is not None and os.altsep in name)
            or os.path.normpath(name) != name
        ):
            raise RollbackCorruptionError("cleanup candidate name is invalid")
        return name


    def _validate_cleanup_authority_file(
        self,
        directory_fd: int,
        name: str,
    ) -> _HeldStoreFile:
        try:
            return _HeldStoreFile.capture(self, name, directory_fd=directory_fd)
        except (OSError, RollbackCorruptionError) as error:
            raise RollbackCorruptionError(
                "cleanup staging authority file is not exact"
            ) from error


    def _validate_preparing_candidate_positions(
        self,
        directory_fd: int,
        preparing: Mapping[str, object],
    ) -> tuple[list[_HeldStoreFile], dict[str, int]]:
        candidates = tuple(preparing["candidates"])
        held: list[_HeldStoreFile] = []
        locations: dict[str, int] = {}
        try:
            for expected in candidates:
                name = self._validate_cleanup_candidate_name(expected["name"])
                root_exists = self._path_exists_at(self._root_fd, name)
                staged_exists = self._path_exists_at(directory_fd, name)
                if root_exists == staged_exists:
                    raise RollbackCorruptionError(
                        "cleanup preparing candidate location is ambiguous"
                    )
                location = self._root_fd if root_exists else directory_fd
                candidate = _HeldStoreFile.capture(
                    self,
                    name,
                    directory_fd=location,
                )
                if not self._cleanup_candidate_survives_rename(candidate, expected):
                    raise RollbackCorruptionError(
                        "cleanup preparing candidate identity changed"
                    )
                held.append(candidate)
                locations[name] = location
            staged_names = {
                name for name, location in locations.items() if location == directory_fd
            }
            self._validate_cleanup_root_inventory(
                preparing,
                staged_names=staged_names,
            )
            for candidate in held:
                candidate.revalidate(self)
            return held, locations
        except BaseException:
            for candidate in held:
                candidate.close()
            raise


    @staticmethod
    def _path_exists_at(directory_fd: int, name: str) -> bool:
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True


    def _open_cleanup_staging(self, *, create: bool) -> tuple[int, bool] | None:
        created = False
        if create:
            try:
                self._cleanup_fault("stage_dir.before_create")
                os.mkdir(self._cleanup_staging_name, 0o700, dir_fd=self._root_fd)
                self._cleanup_fault("stage_dir.after_create")
                self._sync_cleanup_root(prefix="stage_dir")
                created = True
            except FileExistsError:
                pass
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            fd = os.open(self._cleanup_staging_name, flags, dir_fd=self._root_fd)
        except FileNotFoundError:
            return None
        try:
            value = os.fstat(fd)
            path_value = os.stat(
                self._cleanup_staging_name,
                dir_fd=self._root_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(value.st_mode)
                or stat.S_IMODE(value.st_mode) != 0o700
                or (value.st_uid, value.st_gid) != self._owner
                or (value.st_dev, value.st_ino)
                != (path_value.st_dev, path_value.st_ino)
                or value.st_dev != self._root_stat.st_dev
                or value.st_nlink < 2
            ):
                raise RollbackCorruptionError(
                    "abandoned cleanup staging directory is not exact"
                )
            return fd, created
        except BaseException:
            os.close(fd)
            raise


    def _write_cleanup_authority(
        self,
        directory_fd: int,
        name: str,
        raw: bytes,
        *,
        stage_identity: Sequence[int] | None = None,
        expected_names: set[str] | None = None,
        replace: bool = False,
        boundary_prefix: str | None = None,
    ) -> None:
        if len(raw) > _MAX_CLEANUP_MANIFEST_BYTES:
            raise RollbackCorruptionError("cleanup staging authority exceeds bound")
        prefix = boundary_prefix or f"authority.{name}"
        temp_name = self._cleanup_authority_temp_name(name)
        if stage_identity is None:
            stage_identity = self._cleanup_stage_identity_now(directory_fd)
        if expected_names is None:
            expected_names = set(self._bounded_cleanup_staging_names(directory_fd))
        self._rejoin_cleanup_stage(
            directory_fd,
            stage_identity,
            expected_names=expected_names,
        )
        self._cleanup_fault(f"{prefix}.before_temp_create")
        fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        self._cleanup_fault(f"{prefix}.after_temp_create")
        try:
            created = os.fstat(fd)
            if (
                not stat.S_ISREG(created.st_mode)
                or stat.S_IMODE(created.st_mode) != 0o600
                or created.st_nlink != 1
                or (created.st_uid, created.st_gid) != self._owner
                or created.st_size != 0
            ):
                raise RollbackCorruptionError(
                    "cleanup temporary authority file is not exact"
                )
            self._cleanup_fault(f"{prefix}.before_temp_write")
            self._cleanup_write_all(fd, raw, prefix=prefix)
            self._cleanup_fault(f"{prefix}.after_temp_write")
            self._cleanup_fault(f"{prefix}.before_temp_fsync")
            os.fsync(fd)
            self._cleanup_fault(f"{prefix}.after_temp_fsync")
        finally:
            os.close(fd)
        names_with_temp = {*expected_names, temp_name}
        self._rejoin_cleanup_stage(
            directory_fd,
            stage_identity,
            expected_names=names_with_temp,
        )
        temp = self._validate_cleanup_authority_file(directory_fd, temp_name)
        replacement_target: _HeldStoreFile | None = None
        try:
            if temp.raw != raw:
                raise RollbackCorruptionError(
                    "cleanup temporary authority write is incomplete"
                )
            temp.revalidate(self)
            replacement_target = (
                _HeldStoreFile.capture(
                    self,
                    name,
                    directory_fd=directory_fd,
                )
                if replace
                else None
            )
            self._cleanup_fault(f"{prefix}.before_rename")
            self._rejoin_cleanup_stage(
                directory_fd,
                stage_identity,
                expected_names=names_with_temp,
            )
            temp.revalidate(self)
            if replacement_target is not None:
                replacement_target.revalidate(self)
            elif self._path_exists_at(directory_fd, name):
                raise RollbackCorruptionError(
                    "cleanup authority destination appeared"
                )
            if replace:
                os.replace(
                    temp_name,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
            else:
                _package_callable("_rename_noreplace_between", _rename_noreplace_between)(
                    temp_name,
                    name,
                    directory_fd,
                    directory_fd,
                )
            self._cleanup_fault(f"{prefix}.after_rename")
        finally:
            temp.close()
            if replacement_target is not None:
                replacement_target.close()
        self._sync_cleanup_stage(directory_fd, prefix=prefix)


    def _bounded_cleanup_staging_names(self, directory_fd: int) -> tuple[str, ...]:
        def scan(*, collect: bool) -> tuple[tuple[int, int, int, int], list[str]]:
            count = 0
            name_bytes = 0
            digest_sum = 0
            digest_xor = 0
            names: list[str] = []
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    name = entry.name
                    if type(name) is not str:
                        raise RollbackCorruptionError(
                            "cleanup staging entry name is invalid"
                        )
                    try:
                        encoded = name.encode("utf-8")
                    except UnicodeEncodeError as error:
                        raise RollbackCorruptionError(
                            "cleanup staging entry name is not UTF-8"
                        ) from error
                    count += 1
                    name_bytes += len(encoded)
                    if (
                        count > _MAX_ABANDONED_TEMPS + 3
                        or name_bytes
                        > _MAX_ABANDONED_TEMP_NAME_BYTES
                        + len(_CLEANUP_PREPARING_NAME)
                        + len(_CLEANUP_COMMITTED_NAME)
                        + len(_CLEANUP_RECEIPT_NAME)
                        + len(_CLEANUP_RECEIPT_TEMP_NAME)
                    ):
                        raise RollbackCorruptionError(
                            "cleanup staging enumeration bound is exhausted"
                        )
                    digest = int.from_bytes(hashlib.sha256(encoded).digest(), "big")
                    digest_sum = (digest_sum + digest) % (1 << 256)
                    digest_xor ^= digest
                    if collect:
                        names.append(name)
            return (count, name_bytes, digest_sum, digest_xor), names

        expected, _ = scan(collect=False)
        observed, names = scan(collect=True)
        if expected != observed or len(set(names)) != len(names):
            raise RollbackCorruptionError(
                "cleanup staging changed during bounded enumeration"
            )
        return tuple(sorted(names))


    def _cleanup_preparing_payload(
        self,
        raw: bytes,
        directory_fd: int,
    ) -> dict[str, object]:
        if len(raw) > _MAX_CLEANUP_MANIFEST_BYTES:
            raise RollbackCorruptionError("cleanup preparing authority exceeds bound")
        payload = _require_object(
            self._verify_signed(raw, "abandoned-cleanup-preparing"),
            frozenset(
                (
                    "candidates",
                    "domain",
                    "root_identity",
                    "root_names",
                    "schema_version",
                    "stage_identity",
                    "state",
                    "transaction_id",
                )
            ),
            "abandoned cleanup preparing authority",
        )
        if (
            payload["schema_version"] != "bb.rl.phase5.abandoned-cleanup-preparing.v2"
            or payload["domain"] != self._domain
            or payload["state"] != "preparing"
        ):
            raise RollbackCorruptionError("cleanup preparing binding is invalid")
        root_identity = _require_tuple(
            payload["root_identity"],
            "cleanup preparing root identity",
        )
        if (
            len(root_identity) != 4
            or any(type(item) is not int for item in root_identity)
            or tuple(root_identity)
            != (
                self._root_stat.st_dev,
                self._root_stat.st_ino,
                self._owner[0],
                self._owner[1],
            )
        ):
            raise RollbackCorruptionError("cleanup preparing root binding is invalid")
        root_names = _require_tuple(
            payload["root_names"],
            "cleanup preparing root names",
        )
        if len(root_names) > _package_limit("_MAX_ROOT_ENTRIES", _MAX_ROOT_ENTRIES):
            raise RollbackCorruptionError("cleanup preparing root inventory is invalid")
        root_name_bytes = 0
        for name in root_names:
            if (
                type(name) is not str
                or not name
                or os.path.isabs(name)
                or "/" in name
                or (os.altsep is not None and os.altsep in name)
                or os.path.normpath(name) != name
            ):
                raise RollbackCorruptionError(
                    "cleanup preparing root inventory is invalid"
                )
            try:
                root_name_bytes += len(name.encode("utf-8"))
            except UnicodeEncodeError as error:
                raise RollbackCorruptionError(
                    "cleanup preparing root inventory is invalid"
                ) from error
        if (
            sorted(root_names) != root_names
            or len(set(root_names)) != len(root_names)
            or root_name_bytes > _package_limit("_MAX_ROOT_NAME_BYTES", _MAX_ROOT_NAME_BYTES)
            or self._cleanup_staging_name in root_names
        ):
            raise RollbackCorruptionError("cleanup preparing root inventory is invalid")
        stage_identity = _require_tuple(
            payload["stage_identity"],
            "cleanup preparing stage identity",
        )
        self._rejoin_cleanup_stage(directory_fd, stage_identity)
        transaction_id = payload["transaction_id"]
        if type(
            transaction_id
        ) is not str or transaction_id != self._cleanup_transaction_id(
            stage_identity,
            root_identity,
            root_names,
        ):
            raise RollbackCorruptionError(
                "cleanup preparing transaction binding is invalid"
            )
        candidates = _require_tuple(
            payload["candidates"],
            "cleanup preparing candidates",
        )
        if len(candidates) > _MAX_ABANDONED_TEMPS:
            raise RollbackCorruptionError(
                "cleanup preparing candidate bound is exhausted"
            )
        candidate_names: list[str] = []
        total_bytes = 0
        for candidate in candidates:
            item = _require_object(
                candidate,
                frozenset(("identity", "name", "raw_sha256")),
                "cleanup preparing candidate",
            )
            name = item["name"]
            identity = _require_tuple(
                item["identity"],
                "cleanup preparing candidate identity",
            )
            name = self._validate_cleanup_candidate_name(name)
            if (
                len(identity) != 8
                or any(type(part) is not int or part < 0 for part in identity)
                or identity[2:4] != [self._owner[0], self._owner[1]]
                or identity[4] != 0o600
                or identity[5] != 1
                or identity[6] > _MAX_RECORD_BYTES
            ):
                raise RollbackCorruptionError(
                    "cleanup preparing candidate identity is invalid"
                )
            _require_digest(item["raw_sha256"], "cleanup candidate raw digest")
            candidate_names.append(name)
            total_bytes += identity[6]
        if (
            tuple(sorted(candidate_names)) != tuple(candidate_names)
            or len(set(candidate_names)) != len(candidate_names)
            or sum(len(name.encode("utf-8")) for name in candidate_names)
            > _MAX_ABANDONED_TEMP_NAME_BYTES
            or total_bytes > _MAX_ABANDONED_TEMP_BYTES
        ):
            raise RollbackCorruptionError(
                "cleanup preparing candidate inventory is invalid"
            )
        return payload


    def _cleanup_object_proof(
        self,
        candidate: _HeldStoreFile,
        *,
        location: str,
        path: str,
    ) -> dict[str, object]:
        if location not in {"root", "stage", "terminal"}:
            raise RollbackCorruptionError("cleanup proof location is invalid")
        self._validate_cleanup_candidate_name(path)
        return {
            "identity": list(candidate.identity),
            "location": location,
            "path": path,
            "raw_sha256": canonical_digest(candidate.raw),
        }


    def _validate_cleanup_object_proof(
        self,
        value: object,
        *,
        label: str,
    ) -> dict[str, object]:
        proof = _require_object(
            value,
            frozenset(("identity", "location", "path", "raw_sha256")),
            label,
        )
        identity = _require_tuple(proof["identity"], f"{label} identity")
        location = proof["location"]
        path = proof["path"]
        if (
            len(identity) != 8
            or any(type(part) is not int or part < 0 for part in identity)
            or identity[2:4] != [self._owner[0], self._owner[1]]
            or identity[4] != 0o600
            or identity[5] != 1
            or identity[6] > _MAX_RECORD_BYTES
            or location not in {"root", "stage", "terminal"}
        ):
            raise RollbackCorruptionError(f"{label} identity is invalid")
        self._validate_cleanup_candidate_name(path)
        _require_digest(proof["raw_sha256"], f"{label} raw digest")
        return proof


    def _cleanup_replacement_temp_location(self, temp: object) -> str:
        if type(temp) is not str:
            raise RollbackCorruptionError(
                "cleanup replacement temporary name is invalid"
            )
        if re.fullmatch(
            rf"\.{re.escape(self._domain)}\.[0-9a-f]{{32}}\.tmp",
            temp,
        ):
            return "root"
        if re.fullmatch(r"\.intent-replace-[0-9a-f]{32}", temp):
            return "stage"
        raise RollbackCorruptionError(
            "cleanup replacement temporary name is invalid"
        )


    def _validate_cleanup_replacement_proof(
        self,
        value: object,
        *,
        label: str,
    ) -> dict[str, object]:
        proof = _require_object(
            value,
            frozenset(
                (
                    "destination",
                    "destination_digest",
                    "destination_identity",
                    "expected_digest",
                    "expected_size",
                    "expected_payload",
                    "identity",
                    "observed_digest",
                    "state",
                    "temp",
                )
            ),
            label,
        )
        state = proof["state"]
        temp = proof["temp"]
        destination = proof["destination"]
        expected_size = proof["expected_size"]
        temp_location = self._cleanup_replacement_temp_location(temp)
        if (
            state not in {"preparing", "created", "ready", "post"}
            or temp_location not in {"root", "stage"}
            or type(destination) is not str
            or self._validate_cleanup_candidate_name(destination) != destination
            or type(expected_size) is not int
            or not 0 <= expected_size <= _MAX_RECORD_BYTES
        ):
            raise RollbackCorruptionError(f"{label} binding is invalid")
        expected_payload = proof["expected_payload"]
        if type(expected_payload) is not str:
            raise RollbackCorruptionError(f"{label} expected payload is invalid")
        expected_raw = expected_payload.encode("utf-8")
        if (
            len(expected_raw) != expected_size
            or canonical_digest(expected_raw) != proof["expected_digest"]
        ):
            raise RollbackCorruptionError(f"{label} expected payload is invalid")
        _require_digest(proof["expected_digest"], f"{label} expected digest")
        destination_identity = proof["destination_identity"]
        destination_digest = proof["destination_digest"]
        if destination_identity is None:
            if destination_digest is not None:
                raise RollbackCorruptionError(
                    f"{label} destination binding is invalid"
                )
        else:
            identity = _require_tuple(
                destination_identity,
                f"{label} destination identity",
            )
            if (
                len(identity) != 8
                or any(type(part) is not int or part < 0 for part in identity)
                or identity[2:4] != [self._owner[0], self._owner[1]]
                or identity[4] != 0o600
                or identity[5] != 1
                or identity[6] > _MAX_RECORD_BYTES
            ):
                raise RollbackCorruptionError(
                    f"{label} destination identity is invalid"
                )
            _require_digest(
                destination_digest,
                f"{label} destination digest",
            )
        if temp_location == "stage":
            if destination_identity is None:
                raise RollbackCorruptionError(
                    f"{label} stage destination binding is invalid"
                )
            expected_token = canonical_digest(
                canonical_json_bytes(
                    {
                        "name": destination,
                        "old_identity": list(destination_identity),
                        "payload_digest": proof["expected_digest"],
                    }
                )
            )[7:39]
            if temp != f".intent-replace-{expected_token}":
                raise RollbackCorruptionError(
                    f"{label} stage temporary binding is invalid"
                )
        replacement_identity = proof["identity"]
        observed_digest = proof["observed_digest"]
        if state == "preparing":
            if replacement_identity is not None or observed_digest is not None:
                raise RollbackCorruptionError(f"{label} preparing state is invalid")
        else:
            identity = _require_tuple(
                replacement_identity,
                f"{label} identity",
            )
            if (
                len(identity) != 8
                or any(type(part) is not int or part < 0 for part in identity)
                or identity[2:4] != [self._owner[0], self._owner[1]]
                or identity[4] != 0o600
                or identity[5] != 1
                or identity[6] > _MAX_RECORD_BYTES
            ):
                raise RollbackCorruptionError(f"{label} identity is invalid")
            _require_digest(observed_digest, f"{label} observed digest")
            if state in {"ready", "post"} and (
                identity[6] != expected_size
                or observed_digest != proof["expected_digest"]
            ):
                raise RollbackCorruptionError(f"{label} final state is invalid")
        return proof


    def _validate_terminal_cleanup_replacement(
        self,
        recovery_proof: Mapping[str, object],
    ) -> tuple[dict[str, object], dict[str, object] | None]:
        replacement_value = recovery_proof.get("replacement")
        if replacement_value is None:
            return dict(recovery_proof), None
        replacement = self._validate_cleanup_replacement_proof(
            replacement_value,
            label="terminal cleanup replacement proof",
        )
        if replacement["state"] != "post":
            raise RollbackCorruptionError(
                "terminal cleanup replacement proof is not poststate"
            )
        if self._path_exists_at(self._root_fd, str(replacement["temp"])):
            raise RollbackCorruptionError(
                "terminal cleanup replacement temporary survived"
            )
        objects = _require_tuple(
            recovery_proof.get("objects"),
            "terminal cleanup recovery proof objects",
        )
        destination_matches: list[dict[str, object]] = []
        for value in objects:
            proof = self._validate_cleanup_object_proof(
                value,
                label="terminal cleanup recovery object proof",
            )
            if (
                proof["location"] == "root"
                and proof["path"] == replacement["destination"]
            ):
                destination_matches.append(proof)
        if len(destination_matches) != 1:
            raise RollbackCorruptionError(
                "terminal cleanup replacement destination proof is not unique"
            )
        destination_proof = destination_matches[0]
        expected_raw = str(replacement["expected_payload"]).encode("utf-8")
        if (
            destination_proof["identity"] != replacement["identity"]
            or destination_proof["raw_sha256"]
            != replacement["expected_digest"]
        ):
            raise RollbackCorruptionError(
                "terminal cleanup replacement destination proof diverged"
            )
        installed = _HeldStoreFile.capture(
            self,
            str(replacement["destination"]),
        )
        try:
            if (
                installed.identity != tuple(replacement["identity"])
                or installed.raw != expected_raw
                or canonical_digest(installed.raw)
                != replacement["expected_digest"]
            ):
                raise RollbackCorruptionError(
                    "terminal cleanup replacement destination changed"
                )
            installed.revalidate(self)
        finally:
            installed.close()
        active = self._cleanup_recovery_replace_proof
        if active is not None and active != replacement:
            raise RollbackCorruptionError(
                "active cleanup replacement proof diverged"
            )
        self._cleanup_recovery_replace_proof = None
        self._cleanup_recovery_replace_temp = None
        self._cleanup_recovery_replace_destination = None
        cleaned = dict(recovery_proof)
        del cleaned["replacement"]
        return cleaned, dict(replacement)


    def _cleanup_committed_payload(
        self,
        raw: bytes,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
    ) -> dict[str, object]:
        if len(raw) > _MAX_CLEANUP_MANIFEST_BYTES:
            raise RollbackCorruptionError("cleanup committed authority exceeds bound")
        payload = _require_object(
            self._verify_signed(raw, "abandoned-cleanup-committed"),
            frozenset(
                (
                    "candidate_states",
                    "domain",
                    "preparing_digest",
                    "progress_generation",
                    "recovery_proof",
                    "schema_version",
                    "stage_identity",
                    "state",
                    "tombstone_proofs",
                    "transaction_id",
                )
            ),
            "abandoned cleanup committed authority",
        )
        candidate_states = _require_tuple(
            payload["candidate_states"],
            "cleanup committed candidate states",
        )
        expected_names = tuple(item["name"] for item in tuple(preparing["candidates"]))
        observed_names: list[str] = []
        for candidate_state in candidate_states:
            item = _require_object(
                candidate_state,
                frozenset(("name", "state")),
                "cleanup committed candidate state",
            )
            observed_names.append(self._validate_cleanup_candidate_name(item["name"]))
            if item["state"] not in ("pending", "processing", "processed"):
                raise RollbackCorruptionError(
                    "cleanup committed candidate progress is invalid"
                )
        tombstone_proofs = _require_tuple(
            payload["tombstone_proofs"],
            "cleanup committed tombstone proofs",
        )
        tombstone_names: list[str] = []
        for tombstone_proof in tombstone_proofs:
            item = _require_object(
                tombstone_proof,
                frozenset(("candidate_name", "proof", "status")),
                "cleanup committed tombstone proof",
            )
            candidate_name = self._validate_cleanup_candidate_name(
                item["candidate_name"]
            )
            proof = self._validate_cleanup_object_proof(
                item["proof"],
                label="cleanup committed tombstone proof",
            )
            if item["status"] not in {"moving", "processed"}:
                raise RollbackCorruptionError(
                    "cleanup committed tombstone status is invalid"
                )
            if (
                proof["location"] != "stage"
                or not str(proof["path"]).endswith(".cleanup-tombstone")
            ):
                raise RollbackCorruptionError(
                    "cleanup committed tombstone binding is invalid"
                )
            tombstone_names.append(candidate_name)
        recovery_proof = payload["recovery_proof"]
        if recovery_proof is not None:
            recovery_keys = (
                frozenset(recovery_proof)
                if type(recovery_proof) is dict
                else frozenset()
            )
            expected_recovery_keys = frozenset(("objects", "substate"))
            if recovery_keys not in {
                expected_recovery_keys,
                expected_recovery_keys | {"replacement"},
            }:
                raise RollbackCorruptionError(
                    "cleanup committed recovery proof has invalid keys"
                )
            recovery = _require_object(
                recovery_proof,
                recovery_keys,
                "cleanup committed recovery proof",
            )
            if "replacement" in recovery:
                self._validate_cleanup_replacement_proof(
                    recovery["replacement"],
                    label="cleanup committed replacement proof",
                )
            if type(recovery["substate"]) is not str or not recovery["substate"]:
                raise RollbackCorruptionError(
                    "cleanup committed recovery substate is invalid"
                )
            objects = _require_tuple(
                recovery["objects"],
                "cleanup committed recovery proof objects",
            )
            if not objects or len(objects) > 16:
                raise RollbackCorruptionError(
                    "cleanup committed recovery proof bound is invalid"
                )
            proof_keys: list[tuple[str, str]] = []
            for proof_value in objects:
                proof = self._validate_cleanup_object_proof(
                    proof_value,
                    label="cleanup committed recovery object proof",
                )
                proof_keys.append((proof["location"], proof["path"]))
            if proof_keys != sorted(proof_keys) or len(set(proof_keys)) != len(
                proof_keys
            ):
                raise RollbackCorruptionError(
                    "cleanup committed recovery proof ordering is invalid"
                )
        stage_identity = _require_tuple(
            payload["stage_identity"],
            "cleanup committed stage identity",
        )
        generation = payload["progress_generation"]
        if (
            payload["schema_version"] != "bb.rl.phase5.abandoned-cleanup-committed.v3"
            or payload["domain"] != self._domain
            or payload["state"] != "committed"
            or payload["preparing_digest"] != canonical_digest(preparing_raw)
            or tuple(observed_names) != expected_names
            or type(generation) is not int
            or generation < 0
            or generation > 2 * len(expected_names) + 2
            or tombstone_names != sorted(tombstone_names)
            or len(set(tombstone_names)) != len(tombstone_names)
            or any(name not in expected_names for name in tombstone_names)
            or list(stage_identity) != list(preparing["stage_identity"])
            or payload["transaction_id"] != preparing["transaction_id"]
        ):
            raise RollbackCorruptionError("cleanup committed binding is invalid")
        return payload


    def _cleanup_committed_bytes(
        self,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
        candidate_states: Sequence[Mapping[str, object]],
        progress_generation: int,
        *,
        tombstone_proofs: Sequence[Mapping[str, object]],
        recovery_proof: Mapping[str, object] | None,
    ) -> bytes:
        return self._signed_bytes(
            "abandoned-cleanup-committed",
            {
                "candidate_states": [
                    {"name": item["name"], "state": item["state"]}
                    for item in candidate_states
                ],
                "domain": self._domain,
                "preparing_digest": canonical_digest(preparing_raw),
                "progress_generation": progress_generation,
                "recovery_proof": recovery_proof,
                "schema_version": "bb.rl.phase5.abandoned-cleanup-committed.v3",
                "stage_identity": list(preparing["stage_identity"]),
                "state": "committed",
                "tombstone_proofs": list(tombstone_proofs),
                "transaction_id": preparing["transaction_id"],
            },
        )


    def _persist_cleanup_progress(
        self,
        directory_fd: int,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
        committed_raw: bytes,
        candidate_states: list[dict[str, object]],
        progress_generation: int,
        tombstone_proofs: Sequence[Mapping[str, object]],
        recovery_proof: Mapping[str, object] | None,
        *,
        expected_names: set[str],
    ) -> tuple[bytes, int]:
        current = self._validate_cleanup_authority_file(
            directory_fd,
            _CLEANUP_COMMITTED_NAME,
        )
        try:
            if current.raw != committed_raw:
                raise RollbackCorruptionError(
                    "cleanup committed authority changed during progress"
                )
            current.revalidate(self)
            self._rejoin_cleanup_stage(
                directory_fd,
                preparing["stage_identity"],
                expected_names=expected_names,
            )
            next_generation = progress_generation + 1
            next_raw = self._cleanup_committed_bytes(
                preparing_raw,
                preparing,
                candidate_states,
                next_generation,
                tombstone_proofs=tombstone_proofs,
                recovery_proof=recovery_proof,
            )
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_COMMITTED_NAME,
                next_raw,
                stage_identity=preparing["stage_identity"],
                expected_names=expected_names,
                replace=True,
                boundary_prefix=f"authority.committed.g{next_generation}",
            )
            return next_raw, next_generation
        finally:
            current.close()


    def _persist_cleanup_recovery_checkpoint(
        self,
        directory_fd: int,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
        committed_raw: bytes,
        candidate_states: list[dict[str, object]],
        progress_generation: int,
        *,
        tombstone_proofs: Sequence[Mapping[str, object]],
        recovery_proof: Mapping[str, object] | None,
        expected_names: set[str],
        boundary: str,
    ) -> bytes:
        current = self._validate_cleanup_authority_file(
            directory_fd,
            _CLEANUP_COMMITTED_NAME,
        )
        try:
            if current.raw != committed_raw:
                raise RollbackCorruptionError(
                    "cleanup committed authority changed during recovery checkpoint"
                )
            next_raw = self._cleanup_committed_bytes(
                preparing_raw,
                preparing,
                candidate_states,
                progress_generation,
                tombstone_proofs=tombstone_proofs,
                recovery_proof=recovery_proof,
            )
            current.revalidate(self)
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_COMMITTED_NAME,
                next_raw,
                stage_identity=preparing["stage_identity"],
                expected_names=expected_names,
                replace=True,
                boundary_prefix=f"authority.recovery_checkpoint.{boundary}",
            )
            return next_raw
        finally:
            current.close()


    def _cleanup_receipt_bytes(
        self,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
        committed_raw: bytes,
        candidate_names: tuple[str, ...],
        tombstone_proofs: Sequence[Mapping[str, object]],
        recovery_proof: Mapping[str, object] | None,
        terminal_replacement_proof: Mapping[str, object] | None,
    ) -> bytes:
        return self._signed_bytes(
            "abandoned-cleanup-receipt",
            {
                "candidate_names": list(candidate_names),
                "committed_digest": canonical_digest(committed_raw),
                "domain": self._domain,
                "preparing_digest": canonical_digest(preparing_raw),
                "recovery_proof": recovery_proof,
                "schema_version": "bb.rl.phase5.abandoned-cleanup-receipt.v4",
                "stage_identity": list(preparing["stage_identity"]),
                "state": "complete",
                "terminal_removal_intent": True,
                "tombstone_proofs": list(tombstone_proofs),
                "terminal_replacement_proof": terminal_replacement_proof,
                "transaction_id": preparing["transaction_id"],
            },
        )


    def _cleanup_receipt_payload(
        self,
        raw: bytes,
    ) -> Mapping[str, object]:
        if len(raw) > _MAX_CLEANUP_MANIFEST_BYTES:
            raise RollbackCorruptionError("cleanup receipt authority exceeds bound")
        payload = _require_object(
            self._verify_signed(raw, "abandoned-cleanup-receipt"),
            frozenset(
                (
                    "candidate_names",
                    "committed_digest",
                    "domain",
                    "preparing_digest",
                    "recovery_proof",
                    "schema_version",
                    "stage_identity",
                    "state",
                    "terminal_removal_intent",
                    "terminal_replacement_proof",
                    "tombstone_proofs",
                    "transaction_id",
                )
            ),
            "abandoned cleanup receipt",
        )
        candidate_names = _require_tuple(
            payload["candidate_names"],
            "cleanup receipt candidate names",
        )
        stage_identity = _require_tuple(
            payload["stage_identity"],
            "cleanup receipt stage identity",
        )
        if (
            payload["schema_version"] != "bb.rl.phase5.abandoned-cleanup-receipt.v4"
            or payload["domain"] != self._domain
            or payload["state"] != "complete"
            or payload["terminal_removal_intent"] is not True
            or type(payload["transaction_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", payload["transaction_id"]) is None
            or len(stage_identity) != 6
            or any(type(part) is not int or part < 0 for part in stage_identity)
            or len(candidate_names) > _MAX_ABANDONED_TEMPS
            or any(type(name) is not str for name in candidate_names)
            or candidate_names != sorted(candidate_names)
            or len(set(candidate_names)) != len(candidate_names)
        ):
            raise RollbackCorruptionError("cleanup receipt binding is invalid")
        _require_digest(payload["preparing_digest"], "cleanup preparing digest")
        _require_digest(payload["committed_digest"], "cleanup committed digest")
        for name in candidate_names:
            self._validate_cleanup_candidate_name(name)
        _require_tuple(
            payload["tombstone_proofs"],
            "cleanup receipt tombstone proofs",
        )
        if payload["recovery_proof"] is not None:
            _require_object(
                payload["recovery_proof"],
                frozenset(("objects", "substate")),
                "cleanup receipt recovery proof",
            )
        terminal_replacement = payload["terminal_replacement_proof"]
        if terminal_replacement is not None:
            if payload["recovery_proof"] is None:
                raise RollbackCorruptionError(
                    "cleanup receipt replacement has no recovery proof"
                )
            replacement = self._validate_cleanup_replacement_proof(
                terminal_replacement,
                label="cleanup receipt terminal replacement proof",
            )
            if replacement["state"] != "post":
                raise RollbackCorruptionError(
                    "cleanup receipt terminal replacement is not poststate"
                )
        return payload


    def _resume_cleanup_receipt(
        self,
        directory_fd: int,
        names: tuple[str, ...],
    ) -> None:
        receipt_file = self._validate_cleanup_authority_file(
            directory_fd,
            _CLEANUP_RECEIPT_NAME,
        )
        try:
            receipt = self._cleanup_receipt_payload(receipt_file.raw)
            stage_identity = receipt["stage_identity"]
            candidate_names = tuple(receipt["candidate_names"])
            tombstone_proofs = tuple(receipt["tombstone_proofs"])
            tombstone_paths: set[str] = set()
            tombstone_candidates: list[str] = []
            for value in tombstone_proofs:
                item = _require_object(
                    value,
                    frozenset(("candidate_name", "proof", "status")),
                    "cleanup receipt tombstone proof",
                )
                if item["status"] != "processed":
                    raise RollbackCorruptionError(
                        "cleanup receipt tombstone is not terminal"
                    )
                candidate_name = self._validate_cleanup_candidate_name(
                    item["candidate_name"]
                )
                proof = self._validate_cleanup_object_proof(
                    item["proof"],
                    label="cleanup receipt tombstone proof",
                )
                if (
                    proof["location"] != "stage"
                    or not str(proof["path"]).endswith(".cleanup-tombstone")
                ):
                    raise RollbackCorruptionError(
                        "cleanup receipt tombstone binding is invalid"
                    )
                tombstone_candidates.append(candidate_name)
                tombstone_paths.add(str(proof["path"]))
            if (
                tombstone_candidates != sorted(tombstone_candidates)
                or len(tombstone_paths) != len(tombstone_proofs)
                or any(name not in candidate_names for name in tombstone_candidates)
            ):
                raise RollbackCorruptionError(
                    "cleanup receipt tombstone ordering is invalid"
                )
            recovery_proof = receipt["recovery_proof"]
            recovery_objects: tuple[object, ...] = ()
            if recovery_proof is not None:
                recovery = _require_object(
                    recovery_proof,
                    frozenset(("objects", "substate")),
                    "cleanup receipt recovery proof",
                )
                recovery_objects = _require_tuple(
                    recovery["objects"],
                    "cleanup receipt recovery proof objects",
                )
            terminal_replacement_proof = receipt[
                "terminal_replacement_proof"
            ]
            if terminal_replacement_proof is not None:
                assert recovery_proof is not None
                terminal_recovery = dict(recovery_proof)
                terminal_recovery["replacement"] = terminal_replacement_proof
                (
                    terminal_recovery,
                    validated_terminal_replacement,
                ) = self._validate_terminal_cleanup_replacement(
                    terminal_recovery
                )
                if (
                    terminal_recovery != recovery_proof
                    or validated_terminal_replacement
                    != terminal_replacement_proof
                ):
                    raise RollbackCorruptionError(
                        "cleanup receipt terminal replacement binding changed"
                    )
            allowed = {
                _CLEANUP_PREPARING_NAME,
                _CLEANUP_COMMITTED_NAME,
                _CLEANUP_RECEIPT_NAME,
                *tombstone_paths,
            }
            stage_names = set(names)
            if stage_names - allowed:
                raise RollbackCorruptionError(
                    "cleanup receipt staging inventory is invalid"
                )
            if (
                _CLEANUP_COMMITTED_NAME in stage_names
                and _CLEANUP_PREPARING_NAME not in stage_names
            ):
                raise RollbackCorruptionError(
                    "cleanup receipt committed authority has no preparing authority"
                )
            self._rejoin_cleanup_stage(
                directory_fd,
                stage_identity,
                expected_names=stage_names,
            )
            preparing_payload: Mapping[str, object] | None = None
            preparing_raw: bytes | None = None
            if _CLEANUP_PREPARING_NAME in stage_names:
                preparing = self._validate_cleanup_authority_file(
                    directory_fd,
                    _CLEANUP_PREPARING_NAME,
                )
                try:
                    if canonical_digest(preparing.raw) != receipt["preparing_digest"]:
                        raise RollbackCorruptionError(
                            "cleanup receipt preparing binding is invalid"
                        )
                    preparing_payload = self._cleanup_preparing_payload(
                        preparing.raw,
                        directory_fd,
                    )
                    preparing_raw = preparing.raw
                    if (
                        preparing_payload["stage_identity"] != stage_identity
                        or preparing_payload["transaction_id"]
                        != receipt["transaction_id"]
                    ):
                        raise RollbackCorruptionError(
                            "cleanup receipt stage binding is invalid"
                        )
                finally:
                    preparing.close()
            if _CLEANUP_COMMITTED_NAME in stage_names:
                assert preparing_payload is not None and preparing_raw is not None
                committed = self._validate_cleanup_authority_file(
                    directory_fd,
                    _CLEANUP_COMMITTED_NAME,
                )
                try:
                    if canonical_digest(committed.raw) != receipt["committed_digest"]:
                        raise RollbackCorruptionError(
                            "cleanup receipt committed binding is invalid"
                        )
                    committed_payload = self._cleanup_committed_payload(
                        committed.raw,
                        preparing_raw,
                        preparing_payload,
                    )
                    if any(
                        item["state"] != "processed"
                        for item in committed_payload["candidate_states"]
                    ):
                        raise RollbackCorruptionError(
                            "cleanup receipt progress is incomplete"
                        )
                    committed_recovery_proof = committed_payload[
                        "recovery_proof"
                    ]
                    if terminal_replacement_proof is not None:
                        if (
                            type(committed_recovery_proof) is not dict
                            or committed_recovery_proof.get("replacement")
                            != terminal_replacement_proof
                        ):
                            raise RollbackCorruptionError(
                                "cleanup receipt terminal replacement history "
                                "diverged"
                            )
                        committed_recovery_proof = dict(
                            committed_recovery_proof
                        )
                        del committed_recovery_proof["replacement"]
                    if (
                        list(committed_payload["tombstone_proofs"])
                        != list(tombstone_proofs)
                        or committed_recovery_proof != recovery_proof
                    ):
                        raise RollbackCorruptionError(
                            "cleanup receipt terminal proof binding is invalid"
                        )
                finally:
                    committed.close()
            intents = tuple(
                name
                for name in candidate_names
                if name.endswith(".transaction-rollback")
            )
            ordinary_names = tuple(
                name
                for name in candidate_names
                if name not in intents
                and not name.endswith((".displaced-head", ".prior-candidate"))
            )
            if tuple(tombstone_candidates) != ordinary_names:
                raise RollbackCorruptionError(
                    "cleanup receipt ordinary candidate proof is incomplete"
                )
            for name in candidate_names:
                if self._path_exists_at(directory_fd, name) or self._path_exists_at(
                    self._root_fd,
                    name,
                ):
                    raise RollbackCorruptionError("cleanup receipt candidate survived")
            if intents and (
                len(intents) != 1
                or not self._committed_recovery_is_complete(intents[0])
            ):
                raise RollbackCorruptionError(
                    "cleanup receipt recovery proof is incomplete"
                )
            for proof_value in recovery_objects:
                proof = self._validate_cleanup_object_proof(
                    proof_value,
                    label="cleanup receipt recovery object proof",
                )
                location_fd = {
                    "root": self._root_fd,
                    "stage": directory_fd,
                    "terminal": self._terminal_fd,
                }[proof["location"]]
                recovery_object = _HeldStoreFile.capture(
                    self,
                    str(proof["path"]),
                    directory_fd=location_fd,
                )
                try:
                    if (
                        recovery_object.identity != tuple(proof["identity"])
                        or canonical_digest(recovery_object.raw)
                        != proof["raw_sha256"]
                    ):
                        raise RollbackCorruptionError(
                            "cleanup receipt recovery object changed"
                        )
                    recovery_object.revalidate(self)
                finally:
                    recovery_object.close()
            for tombstone_index, value in enumerate(tombstone_proofs):
                proof = value["proof"]
                path = str(proof["path"])
                if not self._path_exists_at(directory_fd, path):
                    stage_names.discard(path)
                    continue
                tombstone = _HeldStoreFile.capture(
                    self,
                    path,
                    directory_fd=directory_fd,
                )
                try:
                    if (
                        tombstone.identity != tuple(proof["identity"])
                        or canonical_digest(tombstone.raw) != proof["raw_sha256"]
                    ):
                        raise RollbackCorruptionError(
                            "cleanup receipt tombstone changed"
                        )
                    prefix = f"receipt.remove.tombstone.{tombstone_index}"
                    self._remove_cleanup_authority(
                        directory_fd,
                        path,
                        prefix=prefix,
                        stage_identity=stage_identity,
                        expected_names=stage_names,
                        target=tombstone,
                        hook_name=str(tombstone_index),
                    )
                    stage_names.remove(path)
                    self._sync_cleanup_stage(directory_fd, prefix=prefix)
                finally:
                    tombstone.close()
            for name in (_CLEANUP_COMMITTED_NAME, _CLEANUP_PREPARING_NAME):
                if name not in stage_names:
                    continue
                prefix = f"receipt.remove.{name}"
                self._remove_cleanup_authority(
                    directory_fd,
                    name,
                    prefix=prefix,
                    stage_identity=stage_identity,
                    expected_names=stage_names,
                )
                stage_names.remove(name)
                self._sync_cleanup_stage(directory_fd, prefix=prefix)
            self._remove_cleanup_authority(
                directory_fd,
                _CLEANUP_RECEIPT_NAME,
                prefix="receipt.remove.receipt",
                stage_identity=stage_identity,
                expected_names=stage_names,
                target=receipt_file,
            )
            stage_names.remove(_CLEANUP_RECEIPT_NAME)
            self._sync_cleanup_stage(
                directory_fd,
                prefix="receipt.remove.receipt",
            )
            self._remove_cleanup_stage(
                directory_fd,
                prefix="receipt.terminal",
                stage_identity=stage_identity,
            )
        finally:
            receipt_file.close()


    @staticmethod
    def _cleanup_candidate_matches(
        candidate: _HeldStoreFile,
        expected: Mapping[str, object],
    ) -> bool:
        return (
            candidate.identity == tuple(expected["identity"])
            and canonical_digest(candidate.raw) == expected["raw_sha256"]
        )


    @staticmethod
    def _cleanup_candidate_survives_rename(
        candidate: _HeldStoreFile,
        expected: Mapping[str, object],
    ) -> bool:
        identity = tuple(expected["identity"])
        return (
            candidate.identity[:7] == identity[:7]
            and canonical_digest(candidate.raw) == expected["raw_sha256"]
        )


    def _rollback_cleanup_staging(
        self,
        directory_fd: int,
        preparing: Mapping[str, object],
        *,
        authority_name: str = _CLEANUP_PREPARING_NAME,
        discard_names: tuple[str, ...] = (),
    ) -> None:
        candidates = tuple(preparing["candidates"])
        stage_identity = preparing["stage_identity"]
        held: list[_HeldStoreFile] = []
        try:
            held, locations = self._validate_preparing_candidate_positions(
                directory_fd,
                preparing,
            )
            staged_candidates = {
                name for name, location in locations.items() if location == directory_fd
            }
            stage_names = {
                *staged_candidates,
                authority_name,
                *discard_names,
            }
            self._rejoin_cleanup_stage(
                directory_fd,
                stage_identity,
                expected_names=stage_names,
            )
            for candidate in reversed(held):
                if locations[candidate.name] == self._root_fd:
                    continue
                prefix = f"rollback.move.{candidate.name}"
                candidate.revalidate(self)
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                self._cleanup_fault(f"{prefix}.before_move")
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                candidate.revalidate(self)
                _package_callable("_rename_noreplace_between", _rename_noreplace_between)(
                    candidate.name,
                    candidate.name,
                    directory_fd,
                    self._root_fd,
                )
                stage_names.remove(candidate.name)
                self._cleanup_fault(f"{prefix}.after_move")
                self._sync_cleanup_stage(directory_fd, prefix=prefix)
                self._sync_cleanup_root(prefix=prefix)
                candidate.path_directory_fd = self._root_fd
                candidate.refresh_path_identity(self, candidate.name)
            if set(self._bounded_root_names()) != {
                *preparing["root_names"],
                self._cleanup_staging_name,
            }:
                raise RollbackCorruptionError(
                    "cleanup preparing rollback root inventory diverged"
                )
            for candidate, expected in zip(held, candidates, strict=True):
                candidate.revalidate(self)
                if not self._cleanup_candidate_survives_rename(candidate, expected):
                    raise RollbackCorruptionError(
                        "cleanup preparing rollback identity diverged"
                    )
            for name in (*discard_names, authority_name):
                prefix = f"rollback.remove.{name}"
                self._remove_cleanup_authority(
                    directory_fd,
                    name,
                    prefix=prefix,
                    stage_identity=stage_identity,
                    expected_names=stage_names,
                )
                stage_names.remove(name)
                self._sync_cleanup_stage(directory_fd, prefix=prefix)
            self._remove_cleanup_stage(
                directory_fd,
                prefix="rollback.terminal",
                stage_identity=stage_identity,
            )
            if self._bounded_root_names() != tuple(preparing["root_names"]):
                raise RollbackCorruptionError(
                    "cleanup preparing rollback root inventory diverged"
                )
        except _CleanupInjectedCrash:
            raise
        except BaseException as error:
            raise RollbackCorruptionError(
                "abandoned cleanup staging rollback failed"
            ) from error
        finally:
            for candidate in held:
                candidate.close()


    def _committed_recovery_rollback_id(
        self,
        intent_name: str,
    ) -> str | None:
        match = re.fullmatch(
            rf"\.{re.escape(self._domain)}\.([0-9a-f]{{32}})\."
            r"transaction-rollback",
            intent_name,
        )
        if match is None or self._domain != "rollback-journal":
            return None
        transaction_id = match.group(1)
        matching = tuple(
            artifacts
            for base, artifacts in self._rollback_quarantine_inventory().items()
            if base.split(".", 2)[1] == transaction_id
        )
        if len(matching) != 1 or set(matching[0]) != {"successor", "tombstone"}:
            return None
        tombstone = _HeldStoreFile.capture(self, matching[0]["tombstone"][0])
        try:
            payload = self._verify_signed(
                tombstone.raw,
                "publication-rollback-intent",
            )
            if (
                payload.get("transaction_id") != transaction_id
                or payload.get("state") != "quarantined"
            ):
                return None
            rollback_id = _require_id(payload.get("rollback_id"), "rollback id")
            tombstone.revalidate(self)
            return rollback_id
        finally:
            tombstone.close()


    def _committed_recovery_is_complete(self, intent_name: str) -> bool:
        match = re.fullmatch(
            rf"\.{re.escape(self._domain)}\.([0-9a-f]{{32}})\."
            r"transaction-rollback",
            intent_name,
        )
        if match is None or self._domain != "rollback-journal":
            return False
        transaction_id = match.group(1)
        anchors = self._terminal_quarantine_anchors()
        refs = tuple(
            ref for ref in anchors.values() if ref.transaction_id == transaction_id
        )
        if len(refs) != 1:
            return False
        ref = refs[0]
        base = self._terminal_anchor_key(ref)
        inventory = self._rollback_quarantine_inventory()
        artifacts = inventory.get(base)
        if artifacts is None or set(inventory) - set(anchors):
            return False
        payload, successor_record, successor_raw = self._terminal_pair_evidence(
            ref,
            artifacts,
        )
        self._validate_live_terminal_anchor(
            ref,
            payload,
            successor_record,
            successor_raw,
            block_on_failure=False,
        )
        return True


    def _resume_committed_cleanup(
        self,
        directory_fd: int,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
        committed_raw: bytes,
    ) -> None:
        candidates = tuple(preparing["candidates"])
        candidate_names = tuple(item["name"] for item in candidates)
        committed = self._cleanup_committed_payload(
            committed_raw,
            preparing_raw,
            preparing,
        )
        candidate_states = [
            {"name": item["name"], "state": item["state"]}
            for item in committed["candidate_states"]
        ]
        progress_generation = committed["progress_generation"]
        tombstone_proofs = [
            {
                "candidate_name": item["candidate_name"],
                "proof": dict(item["proof"]),
                "status": item["status"],
            }
            for item in committed["tombstone_proofs"]
        ]
        recovery_proof = (
            None
            if committed["recovery_proof"] is None
            else {
                "objects": [
                    dict(item) for item in committed["recovery_proof"]["objects"]
                ],
                "substate": committed["recovery_proof"]["substate"],
            }
        )
        if (
            recovery_proof is not None
            and "replacement" in committed["recovery_proof"]
        ):
            recovery_proof["replacement"] = dict(
                committed["recovery_proof"]["replacement"]
            )
            self._cleanup_recovery_replace_proof = dict(
                committed["recovery_proof"]["replacement"]
            )
        stage_identity = preparing["stage_identity"]
        pattern = re.compile(
            rf"^\.{re.escape(self._domain)}\.[0-9a-f]{{32}}\."
            r"(?:immutable|rollback|tmp|transaction-rollback|"
            r"displaced-head|prior-candidate)$"
        )
        expected_by_name = {item["name"]: item for item in candidates}
        recovery_names = {
            name
            for name in candidate_names
            if name.endswith(
                (".transaction-rollback", ".displaced-head", ".prior-candidate")
            )
        }
        intents = tuple(
            name for name in candidate_names if name.endswith(".transaction-rollback")
        )
        if len(intents) > 1:
            raise RollbackCorruptionError(
                "committed cleanup has multiple recovery intents"
            )
        if recovery_names and not intents:
            raise RollbackCorruptionError(
                "committed cleanup recovery artifacts have no intent"
            )
        for name in candidate_names:
            if pattern.fullmatch(name) is None:
                raise RollbackCorruptionError(
                    "committed cleanup candidate name is invalid"
                )

        def close_held(held: Mapping[str, _HeldStoreFile]) -> None:
            for candidate in held.values():
                candidate.close()

        def capture_stage() -> tuple[dict[str, _HeldStoreFile], set[str]]:
            nonlocal committed_raw, recovery_proof
            held: dict[str, _HeldStoreFile] = {}
            stage_names = {
                _CLEANUP_PREPARING_NAME,
                _CLEANUP_COMMITTED_NAME,
            }
            actual_stage_names = set(
                self._bounded_cleanup_staging_names(directory_fd)
            )
            intent_temps = {
                name
                for name in actual_stage_names
                if name.startswith(".intent-replace-")
            }
            for intent_temp in intent_temps:
                if self._cleanup_replacement_temp_location(intent_temp) != "stage":
                    raise RollbackCorruptionError(
                        "recovery replacement temporary name is invalid"
                    )
            if len(intent_temps) > 1:
                raise RollbackCorruptionError(
                    "multiple recovery replacement temporaries survived"
                )
            replacement = (
                None
                if recovery_proof is None
                else recovery_proof.get("replacement")
            )
            if replacement is not None:
                replacement = self._validate_cleanup_replacement_proof(
                    replacement,
                    label="cleanup stage replacement proof",
                )
                if (
                    self._cleanup_replacement_temp_location(replacement["temp"])
                    == "stage"
                ):
                    replacement_temp = str(replacement["temp"])
                    replacement_state = str(replacement["state"])
                    if replacement_state == "post":
                        expected_temps: set[str] = set()
                    elif replacement_state == "preparing":
                        expected_temps = intent_temps & {replacement_temp}
                    else:
                        expected_temps = {replacement_temp}
                    if intent_temps != expected_temps:
                        raise RollbackCorruptionError(
                            "cleanup stage replacement temporary state changed"
                        )
                    if intent_temps:
                        replacement_file = _HeldStoreFile.capture(
                            self,
                            replacement_temp,
                            directory_fd=directory_fd,
                        )
                        try:
                            expected_payload = str(
                                replacement["expected_payload"]
                            ).encode("utf-8")
                            if replacement_state == "preparing":
                                if replacement_file.raw:
                                    raise RollbackCorruptionError(
                                        "unsigned stage replacement temporary changed"
                                    )
                            else:
                                signed_identity = tuple(replacement["identity"])
                                observed_digest = canonical_digest(
                                    replacement_file.raw
                                )
                                if (
                                    replacement_state == "created"
                                    and (
                                        replacement_file.identity
                                        != signed_identity
                                        or observed_digest
                                        != replacement["observed_digest"]
                                    )
                                ) or (
                                    replacement_state == "ready"
                                    and (
                                        replacement_file.identity
                                        != signed_identity
                                        or observed_digest
                                        != replacement["observed_digest"]
                                        or replacement_file.raw != expected_payload
                                    )
                                ):
                                    raise RollbackCorruptionError(
                                        "signed stage replacement temporary changed"
                                    )
                            replacement_file.revalidate(self)
                        finally:
                            replacement_file.close()
            elif intent_temps:
                raise RollbackCorruptionError(
                    "unsigned recovery replacement temporary survived"
                )
            stage_names.update(intent_temps)
            proof_by_candidate = {
                item["candidate_name"]: item for item in tombstone_proofs
            }

            def capture_proof(
                key: str,
                proof: Mapping[str, object],
            ) -> _HeldStoreFile:
                location = proof["location"]
                location_fd = {
                    "root": self._root_fd,
                    "stage": directory_fd,
                    "terminal": self._terminal_fd,
                }[location]
                path = str(proof["path"])
                try:
                    candidate = _HeldStoreFile.capture(
                        self,
                        path,
                        directory_fd=location_fd,
                    )
                except (OSError, RollbackCorruptionError) as error:
                    raise RollbackCorruptionError(
                        "committed cleanup proof object could not be held"
                    ) from error
                if (
                    candidate.identity != tuple(proof["identity"])
                    or canonical_digest(candidate.raw) != proof["raw_sha256"]
                ):
                    candidate.close()
                    raise RollbackCorruptionError(
                        "committed cleanup proof object identity changed"
                    )
                held[key] = candidate
                if location == "stage":
                    stage_names.add(path)
                return candidate

            try:
                recovery_stage_paths: set[str] = set()
                recovery_objects = (
                    []
                    if recovery_proof is None
                    else [dict(value) for value in recovery_proof["objects"]]
                )
                resolvable_move = (
                    None if recovery_proof is None else recovery_proof["substate"]
                )
                if resolvable_move in {
                    "successor_displacement.before_move",
                    "successor_quarantine.before_move",
                    "terminal_tombstone.before_move",
                    "prior_head.before_publish",
                    "cleanup_intent.before_publish",
                    "terminal_intent.before_publish",
                } and intents:
                    if resolvable_move == "terminal_tombstone.before_move":
                        transaction_match = re.fullmatch(
                            rf"\.{re.escape(self._domain)}\.([0-9a-f]{{32}})\."
                            r"transaction-rollback",
                            intents[0],
                        )
                        if transaction_match is None:
                            raise RollbackCorruptionError(
                                "planned terminal tombstone source is invalid"
                            )
                        transaction_id = transaction_match.group(1)
                        planned_transaction_id = transaction_id
                        successor_proof = next(
                            (
                                value
                                for value in recovery_objects
                                if value["location"] == "terminal"
                                and f".{transaction_id}." in str(value["path"])
                                and str(value["path"]).endswith(".successor")
                            ),
                            None,
                        )
                        if successor_proof is None:
                            raise RollbackCorruptionError(
                                "planned terminal tombstone destination is unbound"
                            )
                        successor_path = str(successor_proof["path"])
                        signed_terminal_destination = (
                            f"{successor_path[:-len('.successor')]}.tombstone"
                        )
                        source_exists = self._path_exists_at(
                            directory_fd,
                            intents[0],
                        )
                        destination_exists = self._path_exists_at(
                            self._terminal_fd,
                            signed_terminal_destination,
                        )
                        if source_exists == destination_exists:
                            raise RollbackCorruptionError(
                                "planned terminal tombstone transition is ambiguous"
                            )
                        intent_authority = _HeldStoreFile.capture(
                            self,
                            intents[0] if source_exists else signed_terminal_destination,
                            directory_fd=(
                                directory_fd if source_exists else self._terminal_fd
                            ),
                        )
                    else:
                        intent_location_fd = (
                            directory_fd
                            if self._path_exists_at(directory_fd, intents[0])
                            else self._root_fd
                        )
                        intent_authority = _HeldStoreFile.capture(
                            self,
                            intents[0],
                            directory_fd=intent_location_fd,
                        )
                    try:
                        intent_payload = self._verify_signed(
                            intent_authority.raw,
                            "publication-rollback-intent",
                        )
                        rollback_id = str(intent_payload["rollback_id"])
                        transaction_id = str(intent_payload["transaction_id"])
                        if (
                            resolvable_move == "terminal_tombstone.before_move"
                            and transaction_id != planned_transaction_id
                        ):
                            raise RollbackCorruptionError(
                                "planned terminal tombstone transaction changed"
                            )
                    finally:
                        intent_authority.close()
                    source_location = "root"
                    destination_location = "root"
                    if resolvable_move in {
                        "cleanup_intent.before_publish",
                        "terminal_intent.before_publish",
                    }:
                        source_location = "stage"
                        destination_location = "stage"
                        ready_proofs = [
                            value
                            for value in recovery_objects
                            if value["location"] == "stage"
                            and str(value["path"]).startswith(".intent-replace-")
                        ]
                        if len(ready_proofs) != 1:
                            raise RollbackCorruptionError(
                                "recovery replacement READY proof is invalid"
                            )
                        source_path = str(ready_proofs[0]["path"])
                        destination_path = intents[0]
                    elif resolvable_move == "successor_displacement.before_move":
                        source_path = f"journal.{rollback_id}.head"
                        destination_path = (
                            f".{self._domain}.{transaction_id}.displaced-head"
                        )
                    elif resolvable_move == "successor_quarantine.before_move":
                        source_path = (
                            f".{self._domain}.{transaction_id}.displaced-head"
                        )
                        destination_path, _ = self._rollback_quarantine_names(
                            transaction_id,
                            rollback_id,
                            str(intent_payload["successor_record_digest"]),
                        )
                        destination_location = "terminal"
                    elif resolvable_move == "terminal_tombstone.before_move":
                        source_path = intents[0]
                        source_location = "stage"
                        _, destination_path = self._rollback_quarantine_names(
                            transaction_id,
                            rollback_id,
                            str(intent_payload["successor_record_digest"]),
                        )
                        if destination_path != signed_terminal_destination:
                            raise RollbackCorruptionError(
                                "planned terminal tombstone name changed"
                            )
                        destination_location = "terminal"
                    else:
                        source_path = (
                            f".{self._domain}.{transaction_id}.prior-candidate"
                        )
                        destination_path = f"journal.{rollback_id}.head"
                    location_fds = {
                        "root": self._root_fd,
                        "stage": directory_fd,
                        "terminal": self._terminal_fd,
                    }
                    source_proof = next(
                        (
                            value
                            for value in recovery_objects
                            if value["location"] == source_location
                            and value["path"] == source_path
                        ),
                        None,
                    )
                    if (
                        source_proof is not None
                        and not self._path_exists_at(
                            location_fds[source_location],
                            source_path,
                        )
                        and self._path_exists_at(
                            location_fds[destination_location],
                            destination_path,
                        )
                    ):
                        installed = _HeldStoreFile.capture(
                            self,
                            destination_path,
                            directory_fd=location_fds[destination_location],
                        )
                        try:
                            if resolvable_move in {
                                "cleanup_intent.before_publish",
                                "terminal_intent.before_publish",
                            }:
                                installed_payload = self._verify_signed(
                                    installed.raw,
                                    "publication-rollback-intent",
                                )
                                expected_state = (
                                    "cleanup_pending"
                                    if resolvable_move
                                    == "cleanup_intent.before_publish"
                                    else "quarantined"
                                )
                                if installed_payload["state"] != expected_state:
                                    raise RollbackCorruptionError(
                                        "recovery replacement destination "
                                        "state is invalid"
                                    )
                            if (
                                installed.identity[:7]
                                != tuple(source_proof["identity"])[:7]
                                or canonical_digest(installed.raw)
                                != source_proof["raw_sha256"]
                            ):
                                raise RollbackCorruptionError(
                                    "planned recovery move transition is invalid"
                                )
                            replacement_proof = self._cleanup_object_proof(
                                installed,
                                location=destination_location,
                                path=destination_path,
                            )
                        finally:
                            installed.close()
                        recovery_objects.remove(source_proof)
                        recovery_objects = [
                            value
                            for value in recovery_objects
                            if not (
                                value["location"] == destination_location
                                and value["path"] == destination_path
                            )
                        ]
                        recovery_objects.append(replacement_proof)
                        recovery_objects.sort(
                            key=lambda item: (item["location"], item["path"])
                        )
                        boundary = f"{resolvable_move}.after_resolved"
                        next_proof = {
                            "objects": recovery_objects,
                            "substate": boundary,
                        }
                        committed_raw = self._persist_cleanup_recovery_checkpoint(
                            directory_fd,
                            preparing_raw,
                            preparing,
                            committed_raw,
                            candidate_states,
                            progress_generation,
                            tombstone_proofs=tombstone_proofs,
                            recovery_proof=next_proof,
                            expected_names=set(
                                self._bounded_cleanup_staging_names(directory_fd)
                            ),
                            boundary=boundary,
                        )
                        recovery_proof = next_proof
                if (
                    recovery_proof is not None
                    and "replacement" in recovery_proof
                    and recovery_proof["replacement"]["state"] == "ready"
                ):
                    replacement = recovery_proof["replacement"]
                    replacement_temp = str(replacement["temp"])
                    replacement_destination = str(replacement["destination"])
                    replacement_location = self._cleanup_replacement_temp_location(
                        replacement_temp
                    )
                    replacement_fd = {
                        "root": self._root_fd,
                        "stage": directory_fd,
                    }[replacement_location]
                    temp_exists = self._path_exists_at(
                        replacement_fd,
                        replacement_temp,
                    )
                    if not temp_exists:
                        if not self._path_exists_at(
                            replacement_fd,
                            replacement_destination,
                        ):
                            raise RollbackCorruptionError(
                                "ready recovery replacement disappeared"
                            )
                        installed = _HeldStoreFile.capture(
                            self,
                            replacement_destination,
                            directory_fd=replacement_fd,
                        )
                        try:
                            if (
                                installed.identity[:7]
                                != tuple(replacement["identity"])[:7]
                                or canonical_digest(installed.raw)
                                != replacement["expected_digest"]
                            ):
                                raise RollbackCorruptionError(
                                    "ready recovery replacement poststate changed"
                                )
                            installed.revalidate(self)
                            installed_proof = self._cleanup_object_proof(
                                installed,
                                location=replacement_location,
                                path=replacement_destination,
                            )
                        finally:
                            installed.close()
                        recovery_objects = [
                            value
                            for value in recovery_proof["objects"]
                            if not (
                                value["location"] == replacement_location
                                and value["path"]
                                in {
                                    replacement_temp,
                                    replacement_destination,
                                }
                            )
                        ]
                        recovery_objects.append(installed_proof)
                        recovery_objects.sort(
                            key=lambda item: (item["location"], item["path"])
                        )
                        boundary = (
                            f"{recovery_proof['substate']}.replacement_post"
                        )
                        next_replacement = {
                            **replacement,
                            "identity": list(installed_proof["identity"]),
                            "observed_digest": installed_proof["raw_sha256"],
                            "state": "post",
                        }
                        next_proof = {
                            "objects": recovery_objects,
                            "replacement": next_replacement,
                            "substate": boundary,
                        }
                        committed_raw = self._persist_cleanup_recovery_checkpoint(
                            directory_fd,
                            preparing_raw,
                            preparing,
                            committed_raw,
                            candidate_states,
                            progress_generation,
                            tombstone_proofs=tombstone_proofs,
                            recovery_proof=next_proof,
                            expected_names=set(
                                self._bounded_cleanup_staging_names(directory_fd)
                            ),
                            boundary=boundary,
                        )
                        recovery_proof = next_proof
                        self._cleanup_recovery_replace_proof = dict(
                            next_replacement
                        )
                if recovery_proof is not None:
                    recovery_objects = [
                        dict(value) for value in recovery_proof["objects"]
                    ]
                for proof in recovery_objects:
                    key = f"proof:{proof['location']}:{proof['path']}"
                    capture_proof(key, proof)
                    if proof["location"] == "stage":
                        recovery_stage_paths.add(str(proof["path"]))
                if recovery_proof is not None and "replacement" in recovery_proof:
                    completed_replacement = recovery_proof["replacement"]
                    if (
                        completed_replacement["state"] == "post"
                        and self._cleanup_replacement_temp_location(
                            completed_replacement["temp"]
                        )
                        == "stage"
                    ):
                        self._cleanup_recovery_replace_proof = None
                        self._cleanup_recovery_replace_temp = None
                        self._cleanup_recovery_replace_destination = None
                for name in candidate_names:
                    state = next(
                        item["state"] for item in candidate_states if item["name"] == name
                    )
                    if name in recovery_names and recovery_proof is not None:
                        staged = self._path_exists_at(directory_fd, name)
                        if staged != (name in recovery_stage_paths):
                            raise RollbackCorruptionError(
                                "committed cleanup recovery path proof changed"
                            )
                        proof_key = f"proof:stage:{name}"
                        if proof_key in held:
                            held[name] = held.pop(proof_key)
                        elif state != "processed" and name == intents[0]:
                            terminal_intent = any(
                                proof["location"] == "terminal"
                                and str(proof["path"]).endswith(".tombstone")
                                for proof in recovery_proof["objects"]
                            )
                            if not terminal_intent:
                                raise RollbackCorruptionError(
                                    "unfinished cleanup recovery intent disappeared"
                                )
                        continue
                    proof_item = proof_by_candidate.get(name)
                    if state == "processed":
                        if proof_item is None or proof_item["status"] != "processed":
                            raise RollbackCorruptionError(
                                "processed cleanup candidate has no tombstone proof"
                            )
                        capture_proof(name, proof_item["proof"])
                        if self._path_exists_at(directory_fd, name):
                            raise RollbackCorruptionError(
                                "processed cleanup candidate was replaced"
                            )
                        continue
                    if proof_item is not None:
                        if state != "processing" or proof_item["status"] != "moving":
                            raise RollbackCorruptionError(
                                "unfinished cleanup tombstone proof is invalid"
                            )
                        proof = proof_item["proof"]
                        source_exists = self._path_exists_at(directory_fd, name)
                        tombstone_exists = self._path_exists_at(
                            directory_fd,
                            str(proof["path"]),
                        )
                        if source_exists == tombstone_exists:
                            raise RollbackCorruptionError(
                                "cleanup tombstone move location is ambiguous"
                            )
                        if source_exists:
                            stage_names.add(name)
                            candidate = _HeldStoreFile.capture(
                                self,
                                name,
                                directory_fd=directory_fd,
                            )
                            held[name] = candidate
                            if not self._cleanup_candidate_matches(
                                candidate,
                                expected_by_name[name],
                            ):
                                raise RollbackCorruptionError(
                                    "cleanup tombstone source identity changed"
                                )
                        else:
                            path = str(proof["path"])
                            stage_names.add(path)
                            candidate = _HeldStoreFile.capture(
                                self,
                                path,
                                directory_fd=directory_fd,
                            )
                            held[name] = candidate
                            if (
                                candidate.identity[:7]
                                != tuple(proof["identity"])[:7]
                                or canonical_digest(candidate.raw)
                                != proof["raw_sha256"]
                            ):
                                raise RollbackCorruptionError(
                                    "cleanup tombstone transition identity changed"
                                )
                        continue
                    if self._path_exists_at(self._root_fd, name):
                        raise RollbackCorruptionError(
                            "committed cleanup candidate replayed"
                        )
                    if not self._path_exists_at(directory_fd, name):
                        raise RollbackCorruptionError(
                            "unfinished cleanup candidate disappeared"
                        )
                    stage_names.add(name)
                    try:
                        candidate = _HeldStoreFile.capture(
                            self,
                            name,
                            directory_fd=directory_fd,
                        )
                    except OSError as error:
                        raise RollbackCorruptionError(
                            "committed cleanup stage candidate could not be held: "
                            f"name={name!r}, errno={error.errno!r}"
                        ) from error
                    held[name] = candidate
                    if not self._cleanup_candidate_matches(
                        candidate,
                        expected_by_name[name],
                    ):
                        raise RollbackCorruptionError(
                            "committed cleanup candidate identity changed: "
                            f"{name!r}, state={state!r}, "
                            f"expected={tuple(expected_by_name[name]['identity'])!r}, "
                            f"actual={candidate.identity!r}"
                        )
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                for candidate in held.values():
                    candidate.revalidate(self)
                return held, stage_names
            except BaseException:
                close_held(held)
                raise

        def validate_root(
            intent_capsule: _RollbackRecoveryCapsule | None,
        ) -> None:
            actual_root = set(self._bounded_root_names())
            baseline_root = set(preparing["root_names"]) - set(candidate_names) | {
                self._cleanup_staging_name
            }
            additions = actual_root - baseline_root
            permitted_additions = {
                name
                for name in additions
                if (
                    name == _ROLLBACK_TERMINAL_ANCHOR_INDEX
                    or name.startswith(".terminal-anchor-pending.")
                )
            }
            rollback_id: str | None = None
            if intent_capsule is not None:
                rollback_id = _journal_from_object(
                    self._verify_signed(
                        intent_capsule.predecessor.raw,
                        "journal-record",
                    )
                ).rollback_id
                permitted_additions.update(
                    name
                    for name in additions
                    if name.startswith(f"journal.{rollback_id}.")
                    and name.endswith((".history", ".commit"))
                )
                permitted_additions.update(
                    additions
                    & {
                        intent_capsule.displaced_name,
                        intent_capsule.candidate_name,
                    }
                )
            elif intents:
                rollback_id = self._committed_recovery_rollback_id(intents[0])
                if rollback_id is not None:
                    permitted_additions.update(
                        name
                        for name in additions
                        if name.startswith(f"journal.{rollback_id}.")
                        and name.endswith((".history", ".commit"))
                    )
                transaction_match = re.fullmatch(
                    rf"\.{re.escape(self._domain)}\.([0-9a-f]{{32}})\."
                    r"transaction-rollback",
                    intents[0],
                )
                assert transaction_match is not None
                recovery_temp = f".{self._domain}.{transaction_match.group(1)}.tmp"
                if recovery_temp in additions:
                    permitted_additions.add(recovery_temp)
            if recovery_proof is not None:
                permitted_additions.update(
                    str(value["path"])
                    for value in recovery_proof["objects"]
                    if value["location"] == "root"
                    and str(value["path"]) in additions
                )
                replacement = recovery_proof.get("replacement")
                if replacement is not None:
                    replacement = self._validate_cleanup_replacement_proof(
                        replacement,
                        label="cleanup committed replacement proof",
                    )
                    replacement_temp = str(replacement["temp"])
                    replacement_location = self._cleanup_replacement_temp_location(
                        replacement_temp
                    )
                    replacement_state = str(replacement["state"])
                    if replacement_location == "root" and replacement_state == "preparing":
                        if replacement_temp in actual_root:
                            raise RollbackCorruptionError(
                                "uncreated recovery replacement temporary appeared"
                            )
                    elif replacement_location == "root" and replacement_state == "post":
                        if replacement_temp in actual_root:
                            raise RollbackCorruptionError(
                                "post recovery replacement temporary survived"
                            )
                        replacement_file = _HeldStoreFile.capture(
                            self,
                            str(replacement["destination"]),
                        )
                        try:
                            if (
                                replacement_file.identity
                                != tuple(replacement["identity"])
                                or canonical_digest(replacement_file.raw)
                                != replacement["observed_digest"]
                            ):
                                raise RollbackCorruptionError(
                                    "post recovery replacement destination changed"
                                )
                            replacement_file.revalidate(self)
                        finally:
                            replacement_file.close()
                    elif replacement_location == "root":
                        if replacement_temp not in additions:
                            raise RollbackCorruptionError(
                                "signed recovery replacement temporary disappeared"
                            )
                        replacement_file = _HeldStoreFile.capture(
                            self,
                            replacement_temp,
                        )
                        try:
                            signed_identity = tuple(replacement["identity"])
                            expected_payload = str(
                                replacement["expected_payload"]
                            ).encode("utf-8")
                            if (
                                replacement_file.identity[:6]
                                != signed_identity[:6]
                                or (
                                    replacement_state == "created"
                                    and not expected_payload.startswith(
                                        replacement_file.raw
                                    )
                                )
                                or (
                                    replacement_state == "ready"
                                    and (
                                        replacement_file.identity
                                        != signed_identity
                                        or canonical_digest(replacement_file.raw)
                                        != replacement["observed_digest"]
                                    )
                                )
                            ):
                                raise RollbackCorruptionError(
                                    "signed recovery replacement temporary changed"
                                )
                            replacement_file.revalidate(self)
                        finally:
                            replacement_file.close()
                        permitted_additions.add(replacement_temp)
            missing_root = baseline_root - actual_root
            permitted_missing = (
                {intent_capsule.head_name}
                if intent_capsule is not None
                and intent_capsule.head_name in missing_root
                and intent_capsule.displaced_name in additions
                else set()
            )
            if additions != permitted_additions or missing_root != permitted_missing:
                raise RollbackCorruptionError(
                    "committed cleanup root inventory is invalid: "
                    f"additions={sorted(additions)!r}, "
                    f"permitted={sorted(permitted_additions)!r}, "
                    f"missing={sorted(missing_root)!r}, "
                    f"permitted_missing={sorted(permitted_missing)!r}"
                )

        def capture_recovery_checkpoint(
            capsule: _RollbackRecoveryCapsule,
            boundary: str,
            *,
            persist_checkpoint: bool,
        ) -> None:
            nonlocal committed_raw, recovery_proof
            specifications = {
                *(
                    ("stage", name)
                    for name in {
                        *recovery_names,
                        capsule.displaced_name,
                        capsule.candidate_name,
                    }
                ),
                ("root", capsule.displaced_name),
                ("root", capsule.candidate_name),
                ("root", capsule.head_name),
                ("terminal", capsule.quarantine_name),
                ("terminal", capsule.tombstone_name),
            }
            replacement_temp = self._cleanup_recovery_replace_temp
            replacement_destination = self._cleanup_recovery_replace_destination
            replacement_location = (
                "root"
                if replacement_temp is None
                else self._cleanup_replacement_temp_location(replacement_temp)
            )
            replacement_poststate = (
                boundary.rsplit(".", 1)[-1] in {"after_replace", "after_publish"}
                and replacement_temp is not None
                and replacement_destination is not None
            )
            if (
                replacement_temp is not None
                and not replacement_poststate
                and self._cleanup_recovery_replace_proof is None
            ):
                specifications.add((replacement_location, replacement_temp))
            if recovery_proof is not None:
                specifications.update(
                    (str(item["location"]), str(item["path"]))
                    for item in recovery_proof["objects"]
                    if not (
                        item["location"] == replacement_location
                        and item["path"] == replacement_temp
                        and (
                            replacement_poststate
                            or self._cleanup_recovery_replace_proof is not None
                        )
                    )
                )
            if replacement_poststate:
                specifications.add(
                    (replacement_location, replacement_destination)
                )
            objects: list[dict[str, object]] = []
            captured: list[_HeldStoreFile] = []
            try:
                for location, path in sorted(specifications):
                    location_fd = {
                        "root": self._root_fd,
                        "stage": directory_fd,
                        "terminal": self._terminal_fd,
                    }[location]
                    if not self._path_exists_at(location_fd, path):
                        continue
                    candidate = _HeldStoreFile.capture(
                        self,
                        path,
                        directory_fd=location_fd,
                    )
                    captured.append(candidate)
                    candidate.revalidate(self)
                    objects.append(
                        self._cleanup_object_proof(
                            candidate,
                            location=location,
                            path=path,
                        )
                    )
                objects.sort(key=lambda item: (item["location"], item["path"]))
                if replacement_poststate:
                    assert replacement_temp is not None
                    assert replacement_destination is not None
                    previous_temp = next(
                        (
                            item
                            for item in (
                                () if recovery_proof is None else recovery_proof["objects"]
                            )
                            if item["location"] == replacement_location
                            and item["path"] == replacement_temp
                        ),
                        None,
                    )
                    if (
                        previous_temp is None
                        and self._cleanup_recovery_replace_proof is not None
                        and self._cleanup_recovery_replace_proof["state"] == "ready"
                    ):
                        previous_temp = {
                            "identity": self._cleanup_recovery_replace_proof[
                                "identity"
                            ],
                            "raw_sha256": self._cleanup_recovery_replace_proof[
                                "observed_digest"
                            ],
                        }
                    replacement_object = next(
                        (
                            item
                            for item in objects
                            if item["location"] == replacement_location
                            and item["path"] == replacement_destination
                        ),
                        None,
                    )
                    if (
                        previous_temp is None
                        or replacement_object is None
                        or replacement_object["identity"][:7]
                        != previous_temp["identity"][:7]
                        or replacement_object["raw_sha256"]
                        != previous_temp["raw_sha256"]
                    ):
                        raise RollbackCorruptionError(
                            "cleanup recovery replacement poststate is invalid"
                        )
                    if self._cleanup_recovery_replace_proof is not None:
                        self._cleanup_recovery_replace_proof = {
                            **self._cleanup_recovery_replace_proof,
                            "identity": list(replacement_object["identity"]),
                            "observed_digest": replacement_object["raw_sha256"],
                            "state": "post",
                        }
                current_objects = (
                    None if recovery_proof is None else recovery_proof["objects"]
                )
                if not persist_checkpoint:
                    if current_objects is not None and objects != current_objects:
                        raise RollbackCorruptionError(
                            "cleanup recovery objects changed after signed checkpoint"
                        )
                    validate_root(capsule)
                    return
                if not any(
                    item["path"] == capsule.intent.name
                    or item["path"] == capsule.tombstone_name
                    for item in objects
                ):
                    raise RollbackCorruptionError(
                        "cleanup recovery checkpoint has no intent authority"
                    )
                next_proof: dict[str, object] = {
                    "objects": objects,
                    "substate": boundary,
                }
                if self._cleanup_recovery_replace_proof is not None:
                    next_proof["replacement"] = dict(
                        self._cleanup_recovery_replace_proof
                    )
                stage_names = {
                    _CLEANUP_PREPARING_NAME,
                    _CLEANUP_COMMITTED_NAME,
                    *(
                        str(item["proof"]["path"])
                        for item in tombstone_proofs
                        if item["proof"]["location"] == "stage"
                    ),
                    *(
                        item["name"]
                        for item in candidate_states
                        if item["name"] not in recovery_names
                        and item["state"] != "processed"
                    ),
                    *(
                        str(item["path"])
                        for item in objects
                        if item["location"] == "stage"
                    ),
                    *(
                        (replacement_temp,)
                        if replacement_temp is not None
                        and replacement_location == "stage"
                        and not replacement_poststate
                        and self._path_exists_at(directory_fd, replacement_temp)
                        else ()
                    ),
                }
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                committed_raw = self._persist_cleanup_recovery_checkpoint(
                    directory_fd,
                    preparing_raw,
                    preparing,
                    committed_raw,
                    candidate_states,
                    progress_generation,
                    tombstone_proofs=tombstone_proofs,
                    recovery_proof=next_proof,
                    expected_names=stage_names,
                    boundary=boundary,
                )
                recovery_proof = next_proof
            finally:
                for candidate in captured:
                    candidate.close()

        def preflight_exact_intent(
            held: Mapping[str, _HeldStoreFile],
        ) -> _RollbackRecoveryCapsule | None:
            if not intents or intents[0] not in held:
                return None
            intent = held[intents[0]]
            intent_state = next(
                item["state"] for item in candidate_states if item["name"] == intents[0]
            )
            if intent_state == "pending" and not self._cleanup_candidate_matches(
                intent,
                expected_by_name[intents[0]],
            ):
                raise RollbackCorruptionError(
                    "committed cleanup recovery intent identity changed"
                )
            if intent_state == "processing" and self._committed_recovery_is_complete(
                intents[0]
            ):
                raise RollbackCorruptionError(
                    "completed cleanup recovery intent was replaced"
                )
            capsule = self._preflight_transaction_rollback_intent(
                intents[0],
                recovery_directory_fd=directory_fd,
            )
            if (
                capsule.intent.identity != intent.identity
                or capsule.intent.raw != intent.raw
            ):
                capsule.close()
                raise RollbackCorruptionError(
                    "committed cleanup recovery intent was not rejoined"
                )
            intent.revalidate(self)
            capsule.intent.revalidate(self)
            return capsule

        def persist(
            stage_names: set[str],
        ) -> None:
            nonlocal committed_raw, progress_generation
            committed_raw, progress_generation = self._persist_cleanup_progress(
                directory_fd,
                preparing_raw,
                preparing,
                committed_raw,
                candidate_states,
                progress_generation,
                tombstone_proofs,
                recovery_proof,
                expected_names=stage_names,
            )


        recovery_states = {
            item["state"] for item in candidate_states if item["name"] in recovery_names
        }
        if len(recovery_states) > 1:
            raise RollbackCorruptionError("cleanup recovery progress is not atomic")
        if recovery_names:
            recovery_state = next(iter(recovery_states))
            held, stage_names = capture_stage()
            intent_capsule: _RollbackRecoveryCapsule | None = None
            try:
                intent_capsule = preflight_exact_intent(held)
                validate_root(intent_capsule)
                if recovery_state == "pending":
                    for item in candidate_states:
                        if item["name"] in recovery_names:
                            item["state"] = "processing"
                    persist(stage_names)
                    recovery_state = "processing"
            finally:
                if intent_capsule is not None:
                    intent_capsule.close()
                close_held(held)

            if recovery_state == "processing":
                held, stage_names = capture_stage()
                intent_capsule = None
                try:
                    intent_capsule = preflight_exact_intent(held)
                    validate_root(intent_capsule)
                    self._cleanup_forward_active = True
                    self._cleanup_recovery_checkpoint = (
                        None
                        if intent_capsule is None
                        else lambda boundary, should_persist: (
                            capture_recovery_checkpoint(
                                intent_capsule,
                                boundary,
                                persist_checkpoint=should_persist,
                            )
                        )
                    )
                    try:
                        if intent_capsule is not None:
                            self._cleanup_fault(
                                "forward.recovery.before."
                                f"{intent_capsule.transaction_id}"
                            )
                            validate_root(intent_capsule)
                            self._rejoin_cleanup_stage(
                                directory_fd,
                                stage_identity,
                                expected_names=stage_names,
                            )
                            held[intents[0]].revalidate(self)
                            intent_capsule.intent.revalidate(self)
                            self._recover_transaction_rollback(intent_capsule)
                            self._cleanup_fault(
                                "forward.recovery.after."
                                f"{intent_capsule.transaction_id}"
                            )
                        elif not self._committed_recovery_is_complete(intents[0]):
                            self._cleanup_pending_checkpoint_factory = (
                                lambda capsule: (
                                    lambda boundary, should_persist: (
                                        capture_recovery_checkpoint(
                                            capsule,
                                            boundary,
                                            persist_checkpoint=should_persist,
                                        )
                                    )
                                )
                            )
                            try:
                                self._cleanup_fault(
                                    f"forward.pending_recovery.before.{intents[0]}"
                                )
                                self._recover_pending_terminal_restorations()
                                self._cleanup_fault(
                                    f"forward.pending_recovery.after.{intents[0]}"
                                )
                            finally:
                                self._cleanup_pending_checkpoint_factory = None
                    finally:
                        self._cleanup_recovery_checkpoint = None
                        self._cleanup_forward_active = False
                finally:
                    if intent_capsule is not None:
                        intent_capsule.close()
                    close_held(held)
                held, stage_names = capture_stage()
                try:
                    if any(name in held for name in recovery_names):
                        raise RollbackCorruptionError(
                            "cleanup recovery candidate replacement survived"
                        )
                    if not self._committed_recovery_is_complete(intents[0]):
                        raise RollbackCorruptionError(
                            "committed cleanup recovery evidence is incomplete"
                        )
                    validate_root(None)
                    for item in candidate_states:
                        if item["name"] in recovery_names:
                            item["state"] = "processed"
                    persist(stage_names)
                finally:
                    close_held(held)
            else:
                if recovery_state != "processed":
                    raise RollbackCorruptionError(
                        "cleanup recovery progress is invalid"
                    )
                held, stage_names = capture_stage()
                try:
                    if any(name in held for name in recovery_names):
                        raise RollbackCorruptionError(
                            "processed cleanup recovery candidate survived"
                        )
                    if not self._committed_recovery_is_complete(intents[0]):
                        raise RollbackCorruptionError(
                            "committed cleanup recovery evidence is incomplete"
                        )
                    validate_root(None)
                finally:
                    close_held(held)

        for state_item in candidate_states:
            name = state_item["name"]
            if name in recovery_names:
                continue
            held, stage_names = capture_stage()
            try:
                validate_root(None)
                if state_item["state"] == "pending":
                    state_item["state"] = "processing"
                    persist(stage_names)
            finally:
                close_held(held)
            if state_item["state"] == "processing":
                tombstone_index = candidate_names.index(name)
                tombstone_name = (
                    f".{self._domain}.{preparing['transaction_id']}."
                    f"{tombstone_index:04x}.cleanup-tombstone"
                )
                proof_item = next(
                    (
                        item
                        for item in tombstone_proofs
                        if item["candidate_name"] == name
                    ),
                    None,
                )
                if proof_item is None:
                    held, stage_names = capture_stage()
                    try:
                        validate_root(None)
                        candidate = held[name]
                        candidate.revalidate(self)
                        if not self._cleanup_candidate_matches(
                            candidate,
                            expected_by_name[name],
                        ):
                            raise RollbackCorruptionError(
                                "ordinary cleanup candidate identity changed"
                            )
                        proof_item = {
                            "candidate_name": name,
                            "proof": self._cleanup_object_proof(
                                candidate,
                                location="stage",
                                path=tombstone_name,
                            ),
                            "status": "moving",
                        }
                        tombstone_proofs.append(proof_item)
                        tombstone_proofs.sort(
                            key=lambda item: str(item["candidate_name"])
                        )
                        committed_raw = self._persist_cleanup_recovery_checkpoint(
                            directory_fd,
                            preparing_raw,
                            preparing,
                            committed_raw,
                            candidate_states,
                            progress_generation,
                            tombstone_proofs=tombstone_proofs,
                            recovery_proof=recovery_proof,
                            expected_names=stage_names,
                            boundary=f"tombstone_plan.{tombstone_index}",
                        )
                    finally:
                        close_held(held)
                held, stage_names = capture_stage()
                try:
                    validate_root(None)
                    candidate = held[name]
                    if candidate.name == name:
                        prefix = f"forward.tombstone.{name}"
                        self._cleanup_fault(f"{prefix}.before_move")
                        self._rejoin_cleanup_stage(
                            directory_fd,
                            stage_identity,
                            expected_names=stage_names,
                        )
                        candidate.revalidate(self)
                        _package_callable("_rename_noreplace_between", _rename_noreplace_between)(
                            name,
                            tombstone_name,
                            directory_fd,
                            directory_fd,
                        )
                        stage_names.remove(name)
                        stage_names.add(tombstone_name)
                        candidate.name = tombstone_name
                        candidate.refresh_path_identity(self, tombstone_name)
                    else:
                        prefix = f"forward.tombstone.{name}"
                    self._cleanup_fault(f"{prefix}.before_stage_fsync")
                    self._rejoin_cleanup_stage(
                        directory_fd,
                        stage_identity,
                        expected_names=stage_names,
                    )
                    candidate.revalidate(self)
                    os.fsync(directory_fd)
                    candidate.refresh_path_identity(self, tombstone_name)
                    assert proof_item is not None
                    proof_item["proof"] = self._cleanup_object_proof(
                        candidate,
                        location="stage",
                        path=tombstone_name,
                    )
                    proof_item["status"] = "processed"
                    state_item["state"] = "processed"
                    persist(stage_names)
                    self._cleanup_fault(f"{prefix}.after_stage_fsync")
                    self._cleanup_fault(f"{prefix}.after_move")
                finally:
                    close_held(held)
            elif state_item["state"] != "processed":
                raise RollbackCorruptionError("ordinary cleanup progress is invalid")

        held, stage_names = capture_stage()
        try:
            validate_root(None)
            if any(item["state"] != "processed" for item in candidate_states):
                raise RollbackCorruptionError(
                    "cleanup completion inventory is incomplete"
                )
            if intents and not self._committed_recovery_is_complete(intents[0]):
                raise RollbackCorruptionError(
                    "cleanup completion recovery proof is incomplete"
                )
            terminal_replacement_proof = None
            if recovery_proof is not None:
                (
                    recovery_proof,
                    terminal_replacement_proof,
                ) = self._validate_terminal_cleanup_replacement(
                    recovery_proof
                )
            expected_receipt_raw = self._cleanup_receipt_bytes(
                preparing_raw,
                preparing,
                committed_raw,
                candidate_names,
                tombstone_proofs,
                recovery_proof,
                terminal_replacement_proof,
            )
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_RECEIPT_NAME,
                expected_receipt_raw,
                stage_identity=stage_identity,
                expected_names=stage_names,
                boundary_prefix="authority.receipt",
            )
        finally:
            close_held(held)
        self._resume_cleanup_receipt(
            directory_fd,
            self._bounded_cleanup_staging_names(directory_fd),
        )
        if self._path_exists_at(self._root_fd, self._cleanup_staging_name):
            raise RollbackCorruptionError("cleanup staging directory survived removal")


    def _resume_cleanup_staging(self) -> bool:
        opened = self._open_cleanup_staging(create=False)
        if opened is None:
            return False
        directory_fd, _ = opened
        try:
            stage_identity: Sequence[int] = self._cleanup_stage_identity_now(
                directory_fd
            )
            names = self._bounded_cleanup_staging_names(directory_fd)
            if not names:
                self._remove_cleanup_stage(
                    directory_fd,
                    prefix="resume.empty",
                    stage_identity=stage_identity,
                )
                return True
            temp_names = set(names) & {
                _CLEANUP_PREPARING_TEMP_NAME,
                _CLEANUP_COMMITTED_TEMP_NAME,
                _CLEANUP_RECEIPT_TEMP_NAME,
            }
            if len(temp_names) > 1:
                raise RollbackCorruptionError(
                    "cleanup staging has multiple temporary authorities"
                )
            if _CLEANUP_RECEIPT_NAME in names:
                if temp_names:
                    raise RollbackCorruptionError(
                        "cleanup receipt has an unsafe temporary authority"
                    )
                self._resume_cleanup_receipt(directory_fd, names)
                self._cleanup_resumed_forward = True
                return True
            if _CLEANUP_PREPARING_NAME not in names:
                if names != (_CLEANUP_PREPARING_TEMP_NAME,):
                    raise RollbackCorruptionError(
                        "cleanup staging has no preparing authority"
                    )
                self._dispose_cleanup_temp(
                    directory_fd,
                    _CLEANUP_PREPARING_TEMP_NAME,
                    stage_identity,
                    expected_names={_CLEANUP_PREPARING_TEMP_NAME},
                    prefix="resume.dispose.preparing",
                )
                stage_identity = self._cleanup_stage_identity_now(directory_fd)
                self._remove_cleanup_stage(
                    directory_fd,
                    prefix="resume.empty",
                    stage_identity=stage_identity,
                )
                return True
            preparing_file = self._validate_cleanup_authority_file(
                directory_fd,
                _CLEANUP_PREPARING_NAME,
            )
            try:
                preparing = self._cleanup_preparing_payload(
                    preparing_file.raw,
                    directory_fd,
                )
                stage_identity = preparing["stage_identity"]
                if _CLEANUP_PREPARING_TEMP_NAME in names:
                    if (
                        _CLEANUP_COMMITTED_NAME in names
                        or _CLEANUP_RECEIPT_TEMP_NAME in names
                    ):
                        raise RollbackCorruptionError(
                            "cleanup preparing temporary authority is unsafe"
                        )
                    self._dispose_cleanup_temp(
                        directory_fd,
                        _CLEANUP_PREPARING_TEMP_NAME,
                        stage_identity,
                        expected_names=set(names),
                        prefix="resume.dispose.preparing",
                    )
                    names = self._bounded_cleanup_staging_names(directory_fd)
                if _CLEANUP_COMMITTED_NAME not in names:
                    if _CLEANUP_RECEIPT_TEMP_NAME in names:
                        raise RollbackCorruptionError(
                            "cleanup receipt temporary authority has no commit"
                        )
                    discard_names: tuple[str, ...] = ()
                    if _CLEANUP_COMMITTED_TEMP_NAME in names:
                        self._dispose_cleanup_temp(
                            directory_fd,
                            _CLEANUP_COMMITTED_TEMP_NAME,
                            stage_identity,
                            expected_names=set(names),
                            prefix="resume.dispose.committed",
                        )
                        names = self._bounded_cleanup_staging_names(directory_fd)
                    self._rollback_cleanup_staging(
                        directory_fd,
                        preparing,
                        discard_names=discard_names,
                    )
                    return True
                committed_file = self._validate_cleanup_authority_file(
                    directory_fd,
                    _CLEANUP_COMMITTED_NAME,
                )
                try:
                    committed_payload = self._cleanup_committed_payload(
                        committed_file.raw,
                        preparing_file.raw,
                        preparing,
                    )
                    recovery = committed_payload["recovery_proof"]
                    if recovery is not None and "replacement" in recovery:
                        replacement = self._validate_cleanup_replacement_proof(
                            recovery["replacement"],
                            label="resumed cleanup replacement proof",
                        )
                        replacement_state = str(replacement["state"])
                        replacement_temp = str(replacement["temp"])
                        replacement_location = (
                            self._cleanup_replacement_temp_location(
                                replacement_temp
                            )
                        )
                        replacement_fd = {
                            "root": self._root_fd,
                            "stage": directory_fd,
                        }[replacement_location]
                        temp_exists = self._path_exists_at(
                            replacement_fd,
                            replacement_temp,
                        )
                        if replacement_state == "preparing":
                            if temp_exists:
                                if replacement_location != "stage":
                                    raise RollbackCorruptionError(
                                        "recovery replacement temporary state changed"
                                    )
                                replacement_file = _HeldStoreFile.capture(
                                    self,
                                    replacement_temp,
                                    directory_fd=replacement_fd,
                                )
                                try:
                                    if replacement_file.raw:
                                        raise RollbackCorruptionError(
                                            "unsigned stage replacement changed"
                                        )
                                    replacement_file.revalidate(self)
                                finally:
                                    replacement_file.close()
                        elif replacement_state == "post":
                            if temp_exists:
                                raise RollbackCorruptionError(
                                    "recovery replacement temporary state changed"
                                )
                            destination = _HeldStoreFile.capture(
                                self,
                                str(replacement["destination"]),
                                directory_fd=replacement_fd,
                            )
                            try:
                                if (
                                    destination.identity
                                    != tuple(replacement["identity"])
                                    or canonical_digest(destination.raw)
                                    != replacement["observed_digest"]
                                ):
                                    raise RollbackCorruptionError(
                                        "post recovery replacement changed"
                                    )
                                destination.revalidate(self)
                            finally:
                                destination.close()
                        else:
                            if not temp_exists:
                                raise RollbackCorruptionError(
                                    "signed recovery replacement disappeared"
                                )
                            replacement_file = _HeldStoreFile.capture(
                                self,
                                replacement_temp,
                                directory_fd=replacement_fd,
                            )
                            try:
                                signed_identity = tuple(replacement["identity"])
                                expected_payload = str(
                                    replacement["expected_payload"]
                                ).encode("utf-8")
                                observed_digest = canonical_digest(
                                    replacement_file.raw
                                )
                                if replacement_state in {"created", "ready"} and (
                                    replacement_file.identity != signed_identity
                                    or observed_digest
                                    != replacement["observed_digest"]
                                    or (
                                        replacement_state == "ready"
                                        and replacement_file.raw
                                        != expected_payload
                                    )
                                ):
                                    raise RollbackCorruptionError(
                                        "signed recovery replacement changed"
                                    )
                                replacement_file.revalidate(self)
                            finally:
                                replacement_file.close()
                    if _CLEANUP_COMMITTED_TEMP_NAME in names:
                        self._dispose_cleanup_temp(
                            directory_fd,
                            _CLEANUP_COMMITTED_TEMP_NAME,
                            stage_identity,
                            expected_names=set(names),
                            prefix="resume.dispose.committed",
                        )
                        names = self._bounded_cleanup_staging_names(directory_fd)
                    if _CLEANUP_RECEIPT_TEMP_NAME in names:
                        self._dispose_cleanup_temp(
                            directory_fd,
                            _CLEANUP_RECEIPT_TEMP_NAME,
                            stage_identity,
                            expected_names=set(names),
                            prefix="resume.dispose.receipt",
                        )
                    self._resume_committed_cleanup(
                        directory_fd,
                        preparing_file.raw,
                        preparing,
                        committed_file.raw,
                    )
                    self._cleanup_resumed_forward = True
                finally:
                    committed_file.close()
                return True
            finally:
                preparing_file.close()
        finally:
            os.close(directory_fd)


    def _cleanup_abandoned_temps(self) -> None:
        if self._resume_cleanup_staging() and not self._cleanup_resumed_forward:
            self._cleanup_resumed_forward = False
            return
        self._cleanup_resumed_forward = False
        pattern = re.compile(
            rf"^\.{re.escape(self._domain)}\.[0-9a-f]{{32}}\."
            r"(?:immutable|rollback|tmp|transaction-rollback|"
            r"displaced-head|prior-candidate)$"
        )
        expected_scan, _, _ = self._scan_abandoned_temp_names(
            pattern,
            collect=False,
        )
        observed_scan, names, root_names = self._scan_abandoned_temp_names(
            pattern,
            collect=True,
        )
        if observed_scan != expected_scan:
            raise RollbackCorruptionError(
                "rollback store root changed during abandoned temp scan"
            )
        names = sorted(names)
        root_names = sorted(root_names)
        if len(set(names)) != len(names):
            raise RollbackCorruptionError("abandoned rollback temp name is duplicated")
        if not names:
            if self._domain == "rollback-journal":
                self._recover_pending_terminal_restorations()
                self._validate_terminal_rollback_quarantines()
            return
        intents = tuple(
            name for name in names if name.endswith(".transaction-rollback")
        )
        if len(intents) > 1:
            raise RollbackCorruptionError(
                "multiple transaction rollback intents are forbidden"
            )
        recovery_artifacts = tuple(
            name
            for name in names
            if name.endswith((".displaced-head", ".prior-candidate"))
        )
        if recovery_artifacts and not intents:
            raise RollbackCorruptionError("rollback recovery artifact has no intent")
        held: dict[str, _HeldStoreFile] = {}
        recovery: _RollbackRecoveryCapsule | None = None
        directory_fd = -1
        preparing_raw: bytes | None = None
        preparing: dict[str, object] | None = None
        committed = False
        moved_any = False
        try:
            total_bytes = 0
            for name in names:
                try:
                    candidate = _HeldStoreFile.capture(self, name)
                except (OSError, RollbackCorruptionError) as error:
                    raise RollbackCorruptionError(
                        "abandoned rollback temp could not be held"
                    ) from error
                total_bytes += len(candidate.raw)
                if total_bytes > _MAX_ABANDONED_TEMP_BYTES:
                    candidate.close()
                    raise RollbackCorruptionError(
                        "abandoned rollback temp byte bound is exhausted"
                    )
                held[name] = candidate
            recovery = (
                self._preflight_transaction_rollback_intent(intents[0])
                if intents
                else None
            )
            try:
                for candidate in held.values():
                    candidate.revalidate(self)
            except (OSError, RollbackCorruptionError) as error:
                raise RollbackCorruptionError(
                    "abandoned rollback temp identity changed"
                ) from error
            if tuple(root_names) != self._bounded_root_names():
                raise RollbackCorruptionError(
                    "rollback store root changed before cleanup staging"
                )
            estimated_manifest_bytes = (
                sum(len(name.encode("utf-8")) for name in root_names)
                + sum(len(name.encode("utf-8")) + 256 for name in names)
                + 1024
            )
            if estimated_manifest_bytes > _MAX_CLEANUP_MANIFEST_BYTES:
                raise RollbackCorruptionError(
                    "cleanup preparing manifest bound is exhausted"
                )
            opened = self._open_cleanup_staging(create=True)
            assert opened is not None
            directory_fd, created = opened
            if not created or self._bounded_cleanup_staging_names(directory_fd):
                raise RollbackCorruptionError(
                    "exclusive cleanup staging directory already exists"
                )
            stage_identity = list(self._cleanup_stage_identity_now(directory_fd))
            root_identity = [
                self._root_stat.st_dev,
                self._root_stat.st_ino,
                self._owner[0],
                self._owner[1],
            ]
            preparing = {
                "candidates": [
                    {
                        "identity": list(held[name].identity),
                        "name": name,
                        "raw_sha256": canonical_digest(held[name].raw),
                    }
                    for name in names
                ],
                "domain": self._domain,
                "root_identity": root_identity,
                "root_names": root_names,
                "schema_version": "bb.rl.phase5.abandoned-cleanup-preparing.v2",
                "stage_identity": stage_identity,
                "state": "preparing",
                "transaction_id": self._cleanup_transaction_id(
                    stage_identity,
                    root_identity,
                    root_names,
                ),
            }
            preparing_raw = self._signed_bytes(
                "abandoned-cleanup-preparing",
                preparing,
            )
            if len(preparing_raw) > _MAX_CLEANUP_MANIFEST_BYTES:
                raise RollbackCorruptionError(
                    "cleanup preparing authority exceeds bound"
                )
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_PREPARING_NAME,
                preparing_raw,
                stage_identity=stage_identity,
                expected_names=set(),
                boundary_prefix="authority.preparing.initial",
            )
            stage_names = {_CLEANUP_PREPARING_NAME}
            for index, name in enumerate(names):
                candidate = held[name]
                prefix = f"stage.move.{index}.{name}"
                candidate.revalidate(self)
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                self._cleanup_fault(f"{prefix}.before_move")
                candidate.revalidate(self)
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                _package_callable("_rename_noreplace_between", _rename_noreplace_between)(
                    name,
                    name,
                    self._root_fd,
                    directory_fd,
                )
                moved_any = True
                stage_names.add(name)
                self._cleanup_fault(f"{prefix}.after_move")
                self._sync_cleanup_stage(directory_fd, prefix=prefix)
                self._sync_cleanup_root(prefix=prefix)
                candidate.path_directory_fd = directory_fd
                candidate.refresh_path_identity(self, name)
            self._cleanup_fault("stage.all_moved")
            preparing["candidates"] = [
                {
                    "identity": list(held[name].identity),
                    "name": name,
                    "raw_sha256": canonical_digest(held[name].raw),
                }
                for name in names
            ]
            preparing_raw = self._signed_bytes(
                "abandoned-cleanup-preparing",
                preparing,
            )
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_PREPARING_NAME,
                preparing_raw,
                stage_identity=stage_identity,
                expected_names=stage_names,
                replace=True,
                boundary_prefix="authority.preparing.staged",
            )
            candidate_states = [{"name": name, "state": "pending"} for name in names]
            committed_raw = self._cleanup_committed_bytes(
                preparing_raw,
                preparing,
                candidate_states,
                0,
                tombstone_proofs=(),
                recovery_proof=None,
            )
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_COMMITTED_NAME,
                committed_raw,
                stage_identity=stage_identity,
                expected_names=stage_names,
                boundary_prefix="authority.committed.g0",
            )
            self._sync_cleanup_root(prefix="authority.committed.g0")
            committed = True
            if recovery is not None:
                recovery.close()
                recovery = None
            for candidate in held.values():
                candidate.close()
            held.clear()
            self._resume_committed_cleanup(
                directory_fd,
                preparing_raw,
                preparing,
                committed_raw,
            )
        except _CleanupInjectedCrash:
            raise
        except BaseException:
            if committed:
                raise
            if directory_fd >= 0:
                stage_names = self._bounded_cleanup_staging_names(directory_fd)
                current_stage_identity = self._cleanup_stage_identity_now(directory_fd)
                if (
                    _CLEANUP_COMMITTED_TEMP_NAME in stage_names
                    and _CLEANUP_COMMITTED_NAME not in stage_names
                ):
                    self._dispose_cleanup_temp(
                        directory_fd,
                        _CLEANUP_COMMITTED_TEMP_NAME,
                        current_stage_identity,
                        expected_names=set(stage_names),
                        prefix="exception.dispose.committed",
                    )
                    stage_names = self._bounded_cleanup_staging_names(directory_fd)
                if (
                    _CLEANUP_PREPARING_TEMP_NAME in stage_names
                    and _CLEANUP_PREPARING_NAME not in stage_names
                ):
                    self._dispose_cleanup_temp(
                        directory_fd,
                        _CLEANUP_PREPARING_TEMP_NAME,
                        current_stage_identity,
                        expected_names=set(stage_names),
                        prefix="exception.dispose.preparing",
                    )
                    stage_names = self._bounded_cleanup_staging_names(directory_fd)
                if not moved_any and stage_names == (_CLEANUP_PREPARING_NAME,):
                    self._remove_cleanup_authority(
                        directory_fd,
                        _CLEANUP_PREPARING_NAME,
                        prefix="exception.remove.preparing",
                        stage_identity=current_stage_identity,
                        expected_names=set(stage_names),
                    )
                    self._sync_cleanup_stage(
                        directory_fd,
                        prefix="exception.remove.preparing",
                    )
                    stage_names = ()
                    next_stage_identity = (
                        *current_stage_identity[:5],
                        current_stage_identity[5] - 1,
                    )
                    self._rejoin_cleanup_stage(
                        directory_fd,
                        next_stage_identity,
                        expected_names=set(),
                    )
                    current_stage_identity = next_stage_identity
                if stage_names:
                    assert preparing is not None
                    self._rollback_cleanup_staging(directory_fd, preparing)
                else:
                    self._remove_cleanup_stage(
                        directory_fd,
                        prefix="exception.terminal",
                        stage_identity=current_stage_identity,
                    )
            raise
        finally:
            if recovery is not None:
                recovery.close()
            for candidate in held.values():
                candidate.close()
            if directory_fd >= 0:
                os.close(directory_fd)
        if self._domain == "rollback-journal":
            self._recover_pending_terminal_restorations()
            self._validate_terminal_rollback_quarantines()

__all__ = ['_PinnedSignedDirectoryCleanup']
