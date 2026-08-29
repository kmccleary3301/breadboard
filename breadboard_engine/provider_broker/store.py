"""SQLite-backed credential metadata and secret storage for the provider broker."""

import hashlib
import json
import os
import secrets
import sqlite3
import stat
import threading
import time
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from breadboard_engine import security
from breadboard_engine.security import redaction

_ACTIVE_STATUSES = ("active",)
_LOGIN_EXPIRY_MS = 10 * 60 * 1000
_LOGIN_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "expired"})


class _StoragePathError(OSError):
    """Secret-free refusal for an unsafe or unsupported storage path."""

    def __init__(self, operation: str, path: str) -> None:
        self.operation = operation
        self.path = path
        super().__init__(operation, path)


def _path_error(
    operation: str, path: str, error: BaseException | None = None
) -> _StoragePathError:
    failure = _StoragePathError(operation, path)
    if error is not None:
        failure.__cause__ = error
    return failure


def _secure_directory_flags(path: str) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory:
        raise _path_error("open-directory-no-follow", path)
    return os.O_RDONLY | nofollow | directory | getattr(os, "O_CLOEXEC", 0)


def _secure_file_flags(path: str) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise _path_error("open-file-no-follow", path)
    return (
        os.O_RDONLY
        | nofollow
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _fstat(fd: int, operation: str, path: str) -> os.stat_result:
    try:
        return os.fstat(fd)
    except OSError as error:
        raise _path_error(operation, path, error) from error


def _fchmod(fd: int, mode: int, operation: str, path: str) -> None:
    try:
        os.fchmod(fd, mode)
    except OSError as error:
        raise _path_error(operation, path, error) from error


def _close_quietly(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _validate_directory(fd: int, path: str, *, state_directory: bool) -> None:
    metadata = _fstat(fd, "stat-directory", path)
    if not stat.S_ISDIR(metadata.st_mode):
        raise _path_error("directory-type", path)
    current_uid = os.getuid()
    if state_directory:
        if metadata.st_uid != current_uid:
            raise _path_error("directory-owner", path)
        _fchmod(fd, 0o700, "directory-mode", path)
        repaired = _fstat(fd, "stat-directory", path)
        if stat.S_IMODE(repaired.st_mode) != 0o700:
            raise _path_error("directory-mode", path)
        return
    if metadata.st_uid not in (current_uid, 0):
        raise _path_error("ancestor-owner", path)
    # Writable ancestors are only acceptable when sticky (for example /tmp).
    if not (metadata.st_mode & stat.S_ISVTX) and stat.S_IMODE(metadata.st_mode) & 0o022:
        raise _path_error("ancestor-mode", path)


def _open_state_directory(path: str) -> tuple[int, str]:
    components = path.strip(os.sep).split(os.sep)
    directory_components = components[:-1]
    filename = components[-1] if components else ""
    if not filename or filename in {".", ".."}:
        raise _path_error("storage-path", path)
    flags = _secure_directory_flags(path)
    current_fd: int | None = None
    try:
        current_fd = os.open(os.sep, flags)
        _validate_directory(current_fd, os.sep, state_directory=False)
    except _StoragePathError:
        _close_quietly(current_fd)
        raise
    except OSError as error:
        _close_quietly(current_fd)
        raise _path_error("open-root-directory", path, error) from error
    assert current_fd is not None
    try:
        if not directory_components:
            raise _path_error("state-directory", path)
        for index, component in enumerate(directory_components):
            component_path = os.path.join(os.sep, *directory_components[: index + 1])
            try:
                child_fd = os.open(component, flags, dir_fd=current_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except OSError as error:
                    raise _path_error(
                        "create-directory", component_path, error
                    ) from error
                try:
                    child_fd = os.open(component, flags, dir_fd=current_fd)
                except OSError as error:
                    raise _path_error(
                        "open-directory", component_path, error
                    ) from error
            except OSError as error:
                raise _path_error("open-directory", component_path, error) from error
            _close_quietly(current_fd)
            current_fd = child_fd
            _validate_directory(
                current_fd,
                component_path,
                state_directory=index == len(directory_components) - 1,
            )
        return current_fd, filename
    except BaseException:
        _close_quietly(current_fd)
        raise


def now_ms() -> int:
    return int(time.time() * 1000)


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12).replace('-', '').replace('_', '')}"


def default_store_path() -> Path:
    """Return the user state path, without reading or creating credential data."""
    explicit = os.environ.get("BREADBOARD_CREDENTIAL_STORE_PATH") or os.environ.get(
        "BREADBOARD_CREDENTIAL_DB"
    )
    if explicit:
        return Path(os.path.abspath(os.path.expanduser(explicit)))
    state_dir = os.environ.get("BREADBOARD_STATE_DIR")
    if state_dir:
        return Path(
            os.path.abspath(
                os.path.join(os.path.expanduser(state_dir), "credentials.sqlite3")
            )
        )
    return Path(
        os.path.abspath(
            os.path.join(str(Path.home()), ".breadboard", "credentials.sqlite3")
        )
    )


class SQLiteCredentialStore:
    """Durable metadata store with secret material isolated in a separate table.

    ``accounts`` and ``leases`` are safe to inspect.  Secret material is only
    selected by the broker's narrow execution path and is never returned by
    :meth:`inspect_accounts` or :meth:`list_accounts`.

    Storage-path refusal is typed and secret-free.  Existing owned databases
    and auxiliaries are migrated in place to mode ``0600``; SQLite remains the
    owner of format/schema transactions, with no raw backup or copy.  Hardening
    is monotonic on later initialization failure.  Path hardening runs before
    the store commit; rollback follows SQLite's transaction boundaries,
    including ``executescript`` behavior, without restoring insecure modes.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        supplied_path = (
            str(Path(path).expanduser())
            if path is not None
            else str(default_store_path())
        )
        self._memory = supplied_path == ":memory:"
        self.path = supplied_path
        self._storage_path = (
            supplied_path if self._memory else os.path.abspath(supplied_path)
        )
        if not self._memory:
            register = getattr(security, "register_protected_credential_path", None)
            if register is not None:
                register(self._storage_path, sqlite_sidecars=True)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._transaction_local = threading.local()
        self._state_dir_fd: int | None = None
        self._state_dir_finalizer: weakref.finalize | None = None
        self._db_name: str | None = None
        if not self._memory:
            self._state_dir_fd, self._db_name = _open_state_directory(
                self._storage_path
            )
            try:
                self._prepare_database_file()
                self._initialize()
            except BaseException:
                _close_quietly(self._state_dir_fd)
                self._state_dir_fd = None
                raise
            self._state_dir_finalizer = weakref.finalize(
                self, _close_quietly, self._state_dir_fd
            )
        else:
            self._initialize()

    def _prepare_database_file(self) -> None:
        state_dir_fd = self._state_dir_fd
        db_name = self._db_name
        if state_dir_fd is None or db_name is None:
            return
        flags = _secure_file_flags(self._storage_path)
        created = False
        try:
            try:
                fd = os.open(
                    db_name,
                    flags | os.O_RDWR | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=state_dir_fd,
                )
                created = True
            except FileExistsError:
                fd = os.open(db_name, flags, dir_fd=state_dir_fd)
        except OSError as error:
            raise _path_error("open-database", self._storage_path, error) from error
        try:
            metadata = _fstat(fd, "stat-database", self._storage_path)
            if not stat.S_ISREG(metadata.st_mode):
                raise _path_error("database-type", self._storage_path)
            if metadata.st_uid != os.getuid():
                raise _path_error("database-owner", self._storage_path)
            if metadata.st_nlink != 1:
                raise _path_error("database-link-count", self._storage_path)
            if created or stat.S_IMODE(metadata.st_mode) != 0o600:
                _fchmod(fd, 0o600, "database-mode", self._storage_path)
            repaired = _fstat(fd, "stat-database", self._storage_path)
            if stat.S_IMODE(repaired.st_mode) != 0o600:
                raise _path_error("database-mode", self._storage_path)
        finally:
            _close_quietly(fd)

    def _harden_database_file(self, name: str, path: str) -> None:
        state_dir_fd = self._state_dir_fd
        if state_dir_fd is None:
            return
        flags = _secure_file_flags(path)
        for _ in range(8):
            try:
                fd = os.open(name, flags, dir_fd=state_dir_fd)
            except FileNotFoundError:
                return
            except OSError as error:
                raise _path_error("open-database-auxiliary", path, error) from error
            try:
                metadata = _fstat(fd, "stat-database-auxiliary", path)
                if not stat.S_ISREG(metadata.st_mode):
                    raise _path_error("database-auxiliary-type", path)
                if metadata.st_uid != os.getuid():
                    raise _path_error("database-auxiliary-owner", path)
                if metadata.st_nlink == 0:
                    continue
                if metadata.st_nlink != 1:
                    raise _path_error("database-auxiliary-link-count", path)
                if stat.S_IMODE(metadata.st_mode) != 0o600:
                    _fchmod(fd, 0o600, "database-auxiliary-mode", path)
                repaired = _fstat(fd, "stat-database-auxiliary", path)
                if repaired.st_nlink == 0:
                    continue
                if repaired.st_nlink != 1:
                    raise _path_error("database-auxiliary-link-count", path)
                if stat.S_IMODE(repaired.st_mode) != 0o600:
                    raise _path_error("database-auxiliary-mode", path)
                return
            finally:
                _close_quietly(fd)
        raise _path_error("database-auxiliary-raced", path)

    def _harden_database_files(self) -> None:
        if self._memory or self._db_name is None:
            return
        self._prepare_database_file()
        self._harden_database_file(f"{self._db_name}-wal", f"{self._storage_path}-wal")
        self._harden_database_file(f"{self._db_name}-shm", f"{self._storage_path}-shm")
        self._harden_database_file(
            f"{self._db_name}-journal",
            f"{self._storage_path}-journal",
        )

    def _connect(self) -> sqlite3.Connection:
        if self._memory:
            if self._connection is None:
                self._connection = sqlite3.connect(":memory:", check_same_thread=False)
            return self._connection
        self._harden_database_files()
        connection = sqlite3.connect(self._storage_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        nested = getattr(self._transaction_local, "connection", None)
        if nested is not None:
            if immediate and not nested.in_transaction:
                nested.execute("BEGIN IMMEDIATE")
            yield nested
            return
        callbacks: list[Callable[[], None]] = []
        committed = False
        with self._lock:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            self._transaction_local.connection = connection
            self._transaction_local.after_commit = callbacks
            try:
                if immediate:
                    connection.execute("BEGIN IMMEDIATE")
                yield connection
                # SQLite owns commit/rollback; hardening only removes unsafe
                # mode bits and never attempts a raw-copy or restore.
                self._harden_database_files()
                connection.commit()
                committed = True
            except Exception:
                connection.rollback()
                raise
            finally:
                self._transaction_local.connection = None
                self._transaction_local.after_commit = None
                if not self._memory:
                    connection.close()
        if committed:
            for callback in callbacks:
                callback()

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Group a state change and its durable audit event in one commit."""
        with self._transaction(immediate=True):
            yield

    def after_commit(self, callback: Callable[[], None]) -> None:
        """Publish an effect only after the active transaction commits."""
        callbacks = getattr(self._transaction_local, "after_commit", None)
        if callbacks is None:
            callback()
            return
        callbacks.append(callback)

    def _initialize(self) -> None:
        with self._transaction() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            if not self._memory:
                connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    credential_id TEXT NOT NULL UNIQUE,
                    provider_id TEXT NOT NULL,
                    auth_scheme_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    alias TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'api_key',
                    status TEXT NOT NULL DEFAULT 'active',
                    source TEXT NOT NULL DEFAULT 'broker',
                    secret_version INTEGER NOT NULL DEFAULT 1,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    expires_at_ms INTEGER,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS accounts_provider_idx
                    ON accounts(provider_id, status, label);
                CREATE TABLE IF NOT EXISTS secrets (
                    secret_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    material TEXT NOT NULL,
                    secret_version INTEGER NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    revoked_at_ms INTEGER
                );
                CREATE INDEX IF NOT EXISTS secrets_account_idx
                    ON secrets(account_id, secret_version);
                CREATE TABLE IF NOT EXISTS leases (
                    lease_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL,
                    endpoint_id TEXT NOT NULL,
                    issued_at_ms INTEGER NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    released_at_ms INTEGER
                );
                CREATE INDEX IF NOT EXISTS leases_active_idx
                    ON leases(account_id, expires_at_ms, released_at_ms);
                CREATE TABLE IF NOT EXISTS session_account_bindings (
                    session_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    binding_kind TEXT NOT NULL CHECK (
                        binding_kind IN ('default', 'automatic', 'user')
                    ),
                    reason TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (session_id, provider_id)
                );
                CREATE INDEX IF NOT EXISTS session_account_bindings_account_idx
                    ON session_account_bindings(account_id);
                CREATE TABLE IF NOT EXISTS account_rate_limits (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(account_id) ON DELETE CASCADE,
                    blocked_until_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS credential_refresh_state (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(account_id) ON DELETE CASCADE,
                    owner_id TEXT,
                    expected_secret_version INTEGER,
                    lease_acquired_at_ms INTEGER,
                    lease_expires_at_ms INTEGER,
                    last_failure_class TEXT CHECK (
                        last_failure_class IS NULL
                        OR last_failure_class IN ('transient', 'definitive')
                    ),
                    last_failure_code TEXT,
                    last_failure_at_ms INTEGER,
                    retry_not_before_ms INTEGER,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_sessions (
                    login_session_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    expires_at_ms INTEGER,
                    problem_json TEXT,
                    flow_json TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    event TEXT NOT NULL,
                    occurred_at_ms INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audit_events_occurred_idx
                    ON audit_events(occurred_at_ms, event_id);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(login_sessions)"
                ).fetchall()
            }
            if "flow_json" not in columns:
                connection.execute(
                    "ALTER TABLE login_sessions ADD COLUMN flow_json TEXT"
                )
            if "expires_at_ms" not in columns:
                connection.execute(
                    "ALTER TABLE login_sessions ADD COLUMN expires_at_ms INTEGER"
                )
            connection.execute(
                """UPDATE login_sessions
                   SET expires_at_ms = created_at_ms + ?
                   WHERE status = 'pending'
                     AND (
                         expires_at_ms IS NULL
                         OR expires_at_ms > created_at_ms + ?
                     )""",
                (_LOGIN_EXPIRY_MS, _LOGIN_EXPIRY_MS),
            )
            migration_timestamp = now_ms()
            connection.execute(
                """UPDATE login_sessions
                   SET flow_json = NULL
                   WHERE status IN ('completed', 'failed', 'cancelled', 'expired')
                     AND flow_json IS NOT NULL"""
            )
            connection.execute(
                """UPDATE login_sessions
                   SET status = 'expired', updated_at_ms = ?,
                       problem_json = ?, flow_json = NULL
                   WHERE status = 'pending'
                     AND expires_at_ms IS NOT NULL
                     AND expires_at_ms <= ?""",
                (
                    migration_timestamp,
                    self._json(self._expired_login_problem()),
                    migration_timestamp,
                ),
            )
            connection.execute("DELETE FROM secrets WHERE revoked_at_ms IS NOT NULL")

    @staticmethod
    def _json(value: Any) -> str:
        try:
            return json.dumps(
                value if isinstance(value, Mapping) else {},
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return "{}"

    @staticmethod
    def _decode_json(value: str | None) -> dict[str, Any]:
        try:
            decoded = json.loads(value or "{}")
            return dict(decoded) if isinstance(decoded, Mapping) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    def append_audit_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Persist one scrubbed, secret-free broker audit event."""
        if not isinstance(event, Mapping):
            raise TypeError("audit event must be a mapping")
        scrubbed, _problems = redaction.scrub_structure(dict(event), path="$.audit")
        normalized = dict(scrubbed) if isinstance(scrubbed, Mapping) else {}
        normalized["event_id"] = str(normalized.get("event_id") or _id("bbaudit"))
        try:
            normalized["occurred_at_ms"] = int(
                normalized.get("occurred_at_ms", now_ms())
            )
        except (TypeError, ValueError):
            normalized["occurred_at_ms"] = now_ms()
        normalized["event"] = str(normalized.get("event") or "unknown")[:128]
        normalized["actor"] = str(normalized.get("actor") or "local_process")[:128]
        normalized["origin"] = str(normalized.get("origin") or "provider_broker")[:128]
        normalized["outcome"] = str(normalized.get("outcome") or "success")[:128]
        payload_json = self._json(normalized)
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO audit_events
                   (event_id, event, occurred_at_ms, actor, origin, outcome,
                    payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    normalized["event_id"],
                    normalized["event"],
                    normalized["occurred_at_ms"],
                    normalized["actor"],
                    normalized["origin"],
                    normalized["outcome"],
                    payload_json,
                ),
            )
        return normalized

    def list_audit_events(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Read the durable audit stream in occurrence order."""
        with self._transaction() as connection:
            query = (
                "SELECT payload_json FROM audit_events ORDER BY occurred_at_ms, rowid"
            )
            params: tuple[Any, ...] = ()
            if limit is not None:
                query += " LIMIT ?"
                params = (max(0, int(limit)),)
            rows = connection.execute(query, params).fetchall()
            return [self._decode_json(row["payload_json"]) for row in rows]

    @staticmethod
    def _account_view(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "account_id": row["account_id"],
            "credential_id": row["credential_id"],
            "provider_id": row["provider_id"],
            "auth_scheme_id": row["auth_scheme_id"],
            "label": row["label"],
            "alias": row["alias"],
            "credential_kind": row["kind"],
            "status": row["status"],
            "source": row["source"],
            "secret_version": int(row["secret_version"]),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
            "has_api_key": str(row["kind"]) == "api_key",
            "expires_at_ms": row["expires_at_ms"],
            "metadata": SQLiteCredentialStore._decode_json(row["metadata_json"]),
        }

    @staticmethod
    def _validate_credential_identity(
        view: Mapping[str, Any],
        material: Mapping[str, Any],
    ) -> None:
        secret_values = redaction.credential_secret_values(material)
        with redaction.secret_value_scope(*secret_values, allow_short=True):
            if any(
                redaction.contains_registered_secret_identity(
                    str(view[field]) if view.get(field) is not None else ""
                )
                for field in (
                    "provider_id",
                    "auth_scheme_id",
                    "label",
                    "alias",
                    "account_id",
                    "credential_id",
                    "credential_kind",
                    "source",
                    "secret_version",
                    "created_at_ms",
                    "updated_at_ms",
                    "expires_at_ms",
                )
            ):
                raise ValueError(
                    "credential identity fields cannot contain credential material"
                )

    @staticmethod
    def _safe_metadata(
        metadata: Mapping[str, Any] | None,
        material: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        value = dict(metadata) if isinstance(metadata, Mapping) else {}
        secret_values = redaction.credential_secret_values(material)
        with redaction.secret_value_scope(*secret_values, allow_short=True):
            if redaction.contains_registered_secret_mapping_key(value):
                raise ValueError("metadata keys cannot contain credential material")
            scrubbed, _problems = redaction.scrub_structure(
                value,
                path="$.metadata",
                identity_mapping_keys=True,
            )
        return scrubbed if isinstance(scrubbed, Mapping) else {}

    @staticmethod
    def _refresh_state_view(
        row: sqlite3.Row | None,
        *,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        if row is None:
            return {"status": "idle"}
        current = now_ms() if timestamp is None else int(timestamp)
        owner_present = bool(row["owner_id"])
        lease_expires_at_ms = row["lease_expires_at_ms"]
        if owner_present:
            status = (
                "refreshing"
                if isinstance(lease_expires_at_ms, int)
                and lease_expires_at_ms > current
                else "stale"
            )
        elif row["last_failure_class"]:
            status = "failed"
        else:
            status = "idle"
        view: dict[str, Any] = {"status": status}
        for key in (
            "expected_secret_version",
            "lease_acquired_at_ms",
            "lease_expires_at_ms",
            "last_failure_class",
            "last_failure_code",
            "last_failure_at_ms",
            "retry_not_before_ms",
            "updated_at_ms",
        ):
            if row[key] is not None:
                view[key] = row[key]
        return view

    def put_api_key(
        self,
        *,
        provider_id: str,
        auth_scheme_id: str,
        label: str,
        material: Mapping[str, Any],
        alias: str = "",
        account_id: str | None = None,
        expires_at_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        source: str = "broker",
    ) -> dict[str, Any]:
        provider_id = str(provider_id).strip().lower()
        auth_scheme_id = str(auth_scheme_id or "api_key").strip().lower()
        label = str(label or provider_id).strip()[:128]
        alias = str(alias or "").strip()
        if not provider_id or not label:
            raise ValueError("provider_id and label are required")
        if not isinstance(material, Mapping) or not material.get("api_key"):
            raise ValueError("api_key material is required")
        material_copy = dict(material)
        timestamp = now_ms()
        encoded_metadata = self._json(self._safe_metadata(metadata, material_copy))
        with self._transaction() as connection:
            row = None
            if account_id:
                row = connection.execute(
                    "SELECT * FROM accounts WHERE account_id = ?", (str(account_id),)
                ).fetchone()
                if row is None:
                    raise ValueError("account_id does not exist")
                if str(row["status"]) == "revoked":
                    raise ValueError("revoked account cannot be reactivated")
            if row is None:
                row = connection.execute(
                    """SELECT * FROM accounts
                       WHERE provider_id = ? AND label = ? AND alias = ?
                         AND status != 'revoked'
                       ORDER BY updated_at_ms DESC LIMIT 1""",
                    (provider_id, label, alias),
                ).fetchone()
            if row is not None:
                self._validate_credential_identity(
                    self._account_view(row),
                    material_copy,
                )
            if row is None:
                account_id = _id("bbacct")
                credential_id = _id("bbcred")
                version = 1
                connection.execute(
                    """INSERT INTO accounts
                       (account_id, credential_id, provider_id, auth_scheme_id, label, alias,
                        kind, status, source, secret_version, created_at_ms, updated_at_ms,
                        expires_at_ms, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, 'api_key', 'active', ?, ?, ?, ?, ?, ?)""",
                    (
                        account_id,
                        credential_id,
                        provider_id,
                        auth_scheme_id,
                        label,
                        alias,
                        source,
                        version,
                        timestamp,
                        timestamp,
                        expires_at_ms,
                        encoded_metadata,
                    ),
                )
            else:
                account_id = str(row["account_id"])
                credential_id = str(row["credential_id"])
                version = int(row["secret_version"]) + 1
                connection.execute(
                    """UPDATE accounts SET auth_scheme_id = ?, status = 'active', source = ?,
                       secret_version = ?, updated_at_ms = ?, expires_at_ms = ?, metadata_json = ?
                       WHERE account_id = ?""",
                    (
                        auth_scheme_id,
                        source,
                        version,
                        timestamp,
                        expires_at_ms,
                        encoded_metadata,
                        account_id,
                    ),
                )
                connection.execute(
                    "DELETE FROM secrets WHERE account_id = ?",
                    (account_id,),
                )
                connection.execute(
                    "DELETE FROM credential_refresh_state WHERE account_id = ?",
                    (account_id,),
                )
            secret_id = _id("bbsecret")
            connection.execute(
                """INSERT INTO secrets
                   (secret_id, account_id, material, secret_version, created_at_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (secret_id, account_id, self._json(material_copy), version, timestamp),
            )
            view = self._account_view(
                connection.execute(
                    "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
                ).fetchone()
            )
            self._validate_credential_identity(view, material_copy)
            return view

    def put_oauth(
        self,
        *,
        provider_id: str,
        auth_scheme_id: str,
        label: str,
        material: Mapping[str, Any],
        alias: str = "",
        account_id: str | None = None,
        expires_at_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        source: str = "broker",
    ) -> dict[str, Any]:
        provider_id = str(provider_id).strip().lower()
        auth_scheme_id = str(auth_scheme_id or "oauth2").strip().lower()
        label = str(label or provider_id).strip()[:128]
        alias = str(alias or "").strip()
        if (
            not provider_id
            or not label
            or not material.get("access_token")
            or not material.get("refresh_token")
        ):
            raise ValueError(
                "provider_id, label, access_token, and refresh_token are required"
            )
        material_copy = dict(material)
        encoded_metadata = self._json(self._safe_metadata(metadata, material_copy))
        timestamp = now_ms()
        with self._transaction() as connection:
            row = None
            if account_id:
                row = connection.execute(
                    "SELECT * FROM accounts WHERE account_id = ?", (str(account_id),)
                ).fetchone()
                if row is None:
                    raise ValueError("account_id does not exist")
                if str(row["status"]) == "revoked":
                    raise ValueError("revoked account cannot be reactivated")
            if row is None:
                row = connection.execute(
                    """SELECT * FROM accounts WHERE provider_id = ? AND label = ? AND alias = ?
                       AND status != 'revoked' ORDER BY updated_at_ms DESC LIMIT 1""",
                    (provider_id, label, alias),
                ).fetchone()
            if row is not None:
                self._validate_credential_identity(
                    self._account_view(row),
                    material_copy,
                )
            if row is None:
                account_id = _id("bbacct")
                credential_id = _id("bbcred")
                version = 1
                connection.execute(
                    """INSERT INTO accounts
                       (account_id, credential_id, provider_id, auth_scheme_id, label, alias,
                        kind, status, source, secret_version, created_at_ms, updated_at_ms,
                        expires_at_ms, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, 'oauth2', 'active', ?, ?, ?, ?, ?, ?)""",
                    (
                        account_id,
                        credential_id,
                        provider_id,
                        auth_scheme_id,
                        label,
                        alias,
                        source,
                        version,
                        timestamp,
                        timestamp,
                        expires_at_ms,
                        encoded_metadata,
                    ),
                )
            else:
                account_id = str(row["account_id"])
                credential_id = str(row["credential_id"])
                version = int(row["secret_version"]) + 1
                connection.execute(
                    """UPDATE accounts SET auth_scheme_id = ?, kind = 'oauth2', status = 'active',
                       source = ?, secret_version = ?, updated_at_ms = ?, expires_at_ms = ?, metadata_json = ?
                       WHERE account_id = ?""",
                    (
                        auth_scheme_id,
                        source,
                        version,
                        timestamp,
                        expires_at_ms,
                        encoded_metadata,
                        account_id,
                    ),
                )
                connection.execute(
                    "DELETE FROM secrets WHERE account_id = ?",
                    (account_id,),
                )
                connection.execute(
                    "DELETE FROM credential_refresh_state WHERE account_id = ?",
                    (account_id,),
                )
            connection.execute(
                """INSERT INTO secrets (secret_id, account_id, material, secret_version, created_at_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    _id("bbsecret"),
                    account_id,
                    self._json(material_copy),
                    version,
                    timestamp,
                ),
            )
            view = self._account_view(
                connection.execute(
                    "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
                ).fetchone()
            )
            self._validate_credential_identity(view, material_copy)
            return view

    def list_accounts(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            normalized_provider = (
                str(provider_id).strip().lower() if provider_id else None
            )
            if normalized_provider:
                rows = connection.execute(
                    "SELECT * FROM accounts WHERE provider_id = ? ORDER BY created_at_ms, account_id",
                    (normalized_provider,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM accounts ORDER BY created_at_ms, account_id"
                ).fetchall()
            refresh_rows = connection.execute(
                "SELECT * FROM credential_refresh_state"
            ).fetchall()
            refresh_by_account = {str(row["account_id"]): row for row in refresh_rows}
            timestamp = now_ms()
            values = []
            for row in rows:
                view = self._account_view(row)
                view["refresh_state"] = self._refresh_state_view(
                    refresh_by_account.get(str(row["account_id"])),
                    timestamp=timestamp,
                )
                values.append(view)
            return values

    def inspect_accounts(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        """Return metadata only; this method intentionally never selects ``secrets``."""
        return self.list_accounts(provider_id)

    @staticmethod
    def _candidate_index(
        session_id: str,
        provider_id: str,
        credential_class: str | None,
        total: int,
    ) -> int:
        if total <= 1 or not session_id:
            return 0
        identity = "\0".join(
            (str(session_id), str(provider_id), credential_class or "stored")
        )
        digest = hashlib.sha256(identity.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % total

    @staticmethod
    def _binding_row(
        connection: sqlite3.Connection,
        session_id: str,
        provider_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """SELECT bindings.*, accounts.credential_id AS bound_credential_id,
                      accounts.label AS bound_label, accounts.alias AS bound_alias,
                      accounts.kind AS bound_kind, accounts.source AS bound_source,
                      accounts.status AS bound_status,
                      accounts.expires_at_ms AS bound_expires_at_ms,
                      EXISTS (
                          SELECT 1 FROM secrets AS active_secrets
                          WHERE active_secrets.account_id = bindings.account_id
                            AND active_secrets.revoked_at_ms IS NULL
                      ) AS has_active_secret,
                      limits.blocked_until_ms AS blocked_until_ms,
                      refresh.retry_not_before_ms AS refresh_retry_not_before_ms,
                      refresh.last_failure_class AS refresh_failure_class
               FROM session_account_bindings AS bindings
               LEFT JOIN accounts ON accounts.account_id = bindings.account_id
               LEFT JOIN account_rate_limits AS limits
                    ON limits.account_id = bindings.account_id
               LEFT JOIN credential_refresh_state AS refresh
                    ON refresh.account_id = bindings.account_id
               WHERE bindings.session_id = ? AND bindings.provider_id = ?""",
            (session_id, provider_id),
        ).fetchone()

    @staticmethod
    def _binding_view(
        row: Mapping[str, Any],
        *,
        timestamp: int,
    ) -> dict[str, Any]:
        status = str(row["bound_status"] or "missing")
        expires_at = row["bound_expires_at_ms"]
        blocked_until = row["blocked_until_ms"]
        refresh_retry_at = row["refresh_retry_not_before_ms"]
        if status == "active" and not bool(row["has_active_secret"]):
            availability = "missing_secret"
        elif (
            status == "active"
            and blocked_until is not None
            and int(blocked_until) > timestamp
        ):
            availability = "rate_limited"
        elif (
            status == "active"
            and refresh_retry_at is not None
            and int(refresh_retry_at) > timestamp
        ):
            availability = "refresh_deferred"
        elif (
            status == "active"
            and expires_at is not None
            and int(expires_at) <= timestamp
        ):
            availability = "expired"
        else:
            availability = status
        result: dict[str, Any] = {
            "session_id": str(row["session_id"]),
            "provider_id": str(row["provider_id"]),
            "account_id": str(row["account_id"]),
            "binding_kind": str(row["binding_kind"]),
            "reason": str(row["reason"]),
            "availability": availability,
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }
        for source, target in (
            ("bound_credential_id", "credential_id"),
            ("bound_label", "label"),
            ("bound_alias", "alias"),
        ):
            value = row[source]
            if value:
                result[target] = str(value)
        if expires_at is not None:
            result["expires_at_ms"] = int(expires_at)
        if blocked_until is not None and int(blocked_until) > timestamp:
            result["blocked_until_ms"] = int(blocked_until)
        if refresh_retry_at is not None and int(refresh_retry_at) > timestamp:
            result["refresh_retry_not_before_ms"] = int(refresh_retry_at)
            if row["refresh_failure_class"]:
                result["refresh_failure_class"] = str(row["refresh_failure_class"])
        return result

    def _upsert_session_binding(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        provider_id: str,
        account_id: str,
        binding_kind: str,
        reason: str,
        timestamp: int,
    ) -> tuple[sqlite3.Row, bool]:
        previous = self._binding_row(connection, session_id, provider_id)
        if (
            previous is not None
            and str(previous["account_id"]) == account_id
            and str(previous["binding_kind"]) == binding_kind
        ):
            return previous, False
        connection.execute(
            """INSERT INTO session_account_bindings
               (session_id, provider_id, account_id, binding_kind, reason,
                created_at_ms, updated_at_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id, provider_id) DO UPDATE SET
                   account_id = excluded.account_id,
                   binding_kind = excluded.binding_kind,
                   reason = excluded.reason,
                   updated_at_ms = excluded.updated_at_ms""",
            (
                session_id,
                provider_id,
                account_id,
                binding_kind,
                reason,
                timestamp,
                timestamp,
            ),
        )
        current = self._binding_row(connection, session_id, provider_id)
        assert current is not None
        return current, True

    def _select_account(
        self,
        connection: sqlite3.Connection,
        *,
        provider_id: str,
        account_id: str | None = None,
        credential_id: str | None = None,
        label: str | None = None,
        alias: str | None = None,
        credential_class: str | None = None,
        session_id: str = "",
        minimum_validity_ms: int = 0,
        allow_expired: bool = False,
        persist_binding: bool = False,
        bind_explicit: bool = True,
    ) -> tuple[sqlite3.Row | None, sqlite3.Row | None, bool]:
        timestamp = now_ms()
        threshold = timestamp + max(0, int(minimum_validity_ms))
        clauses = [
            "accounts.provider_id = ?",
            "accounts.status = 'active'",
        ]
        params: list[Any] = [provider_id]
        if not allow_expired:
            clauses.append(
                "(accounts.expires_at_ms IS NULL OR accounts.expires_at_ms > ?)"
            )
            params.append(threshold)
        clauses.extend(
            (
                """EXISTS (
                    SELECT 1 FROM secrets
                    WHERE secrets.account_id = accounts.account_id
                      AND secrets.revoked_at_ms IS NULL
                )""",
                """NOT EXISTS (
                    SELECT 1 FROM account_rate_limits AS limits
                    WHERE limits.account_id = accounts.account_id
                      AND limits.blocked_until_ms > ?
                )""",
                """NOT EXISTS (
                    SELECT 1 FROM credential_refresh_state AS refresh
                    WHERE refresh.account_id = accounts.account_id
                      AND refresh.retry_not_before_ms > ?
                )""",
            )
        )
        params.extend((timestamp, timestamp))
        explicit = any((account_id, credential_id, label, alias))
        if account_id:
            clauses.append("accounts.account_id = ?")
            params.append(account_id)
        elif credential_id:
            clauses.append("accounts.credential_id = ?")
            params.append(credential_id)
        elif label:
            clauses.append("accounts.label = ?")
            params.append(label)
        elif alias:
            clauses.append("accounts.alias = ?")
            params.append(alias)
        if credential_class == "oauth":
            clauses.append("accounts.kind = 'oauth2'")
        elif credential_class == "login_api_key":
            clauses.extend(("accounts.kind = 'api_key'", "accounts.source = 'login'"))
        elif credential_class == "stored_api_key":
            clauses.extend(("accounts.kind = 'api_key'", "accounts.source != 'login'"))
        elif credential_class is not None:
            raise ValueError(f"unsupported credential class: {credential_class}")
        candidates = connection.execute(
            f"""SELECT accounts.* FROM accounts
                WHERE {" AND ".join(clauses)}
                ORDER BY accounts.created_at_ms, accounts.account_id""",
            params,
        ).fetchall()
        binding = (
            self._binding_row(connection, session_id, provider_id)
            if session_id
            else None
        )
        if not candidates:
            return None, binding, False
        if explicit:
            selected = candidates[0]
            if persist_binding and session_id and bind_explicit:
                binding, changed = self._upsert_session_binding(
                    connection,
                    session_id=session_id,
                    provider_id=provider_id,
                    account_id=str(selected["account_id"]),
                    binding_kind="user",
                    reason="user_selected",
                    timestamp=timestamp,
                )
                return selected, binding, changed
            return selected, binding, False
        # A user pin is fail-closed: only another explicit choice or clear may rotate it.
        if binding is not None:
            if str(binding["binding_kind"]) == "user":
                bound = next(
                    (
                        candidate
                        for candidate in candidates
                        if str(candidate["account_id"]) == str(binding["account_id"])
                    ),
                    None,
                )
                return bound, binding, False
            bound = next(
                (
                    candidate
                    for candidate in candidates
                    if str(candidate["account_id"]) == str(binding["account_id"])
                ),
                None,
            )
            if bound is not None:
                return bound, binding, False
        # Default bindings may rotate only to another currently eligible account.
        index = self._candidate_index(
            session_id,
            provider_id,
            credential_class,
            len(candidates),
        )
        selected = candidates[index]
        if not persist_binding or not session_id:
            return selected, binding, False
        previous_class = None
        if binding is not None and binding["bound_kind"] is not None:
            previous_class = (
                "oauth"
                if str(binding["bound_kind"]) == "oauth2"
                else (
                    "login_api_key"
                    if str(binding["bound_source"]) == "login"
                    else "stored_api_key"
                )
            )
        binding_available = bool(
            binding is not None
            and self._binding_view(binding, timestamp=timestamp)["availability"]
            == "active"
        )
        reason = (
            "deterministic_default"
            if binding is None
            else (
                "source_precedence"
                if binding_available and previous_class != credential_class
                else "bound_account_unavailable"
            )
        )
        binding, changed = self._upsert_session_binding(
            connection,
            session_id=session_id,
            provider_id=provider_id,
            account_id=str(selected["account_id"]),
            binding_kind="default" if binding is None else "automatic",
            reason=reason,
            timestamp=timestamp,
        )
        return selected, binding, changed

    @staticmethod
    def _apply_binding_view(
        value: dict[str, Any],
        binding: Mapping[str, Any] | None,
        *,
        timestamp: int,
        changed: bool = False,
    ) -> dict[str, Any]:
        if binding is None or str(binding["account_id"]) != str(value["account_id"]):
            return value
        binding_view = SQLiteCredentialStore._binding_view(
            binding,
            timestamp=timestamp,
        )
        value["session_binding_kind"] = binding_view["binding_kind"]
        value["session_binding_reason"] = binding_view["reason"]
        value["session_binding_changed"] = bool(changed)
        return value

    def select_account_view(
        self,
        *,
        provider_id: str,
        account_id: str | None = None,
        credential_id: str | None = None,
        label: str | None = None,
        alias: str | None = None,
        credential_class: str | None = None,
        session_id: str = "",
        minimum_validity_ms: int = 0,
        allow_expired: bool = False,
    ) -> dict[str, Any] | None:
        """Return the eligible deterministic account for one leg, never its secret."""
        timestamp = now_ms()
        with self._transaction() as connection:
            account, binding, _changed = self._select_account(
                connection,
                provider_id=str(provider_id).strip().lower(),
                account_id=account_id,
                credential_id=credential_id,
                label=label,
                alias=alias,
                credential_class=credential_class,
                session_id=str(session_id),
                minimum_validity_ms=minimum_validity_ms,
                allow_expired=allow_expired,
            )
            if account is None:
                return None
            return self._apply_binding_view(
                self._account_view(account),
                binding,
                timestamp=timestamp,
            )

    def get_session_account_binding(
        self,
        session_id: str,
        provider_id: str,
    ) -> dict[str, Any] | None:
        session = str(session_id).strip()
        provider = str(provider_id).strip().lower()
        if not session or not provider:
            return None
        timestamp = now_ms()
        with self._transaction() as connection:
            binding = self._binding_row(connection, session, provider)
            return (
                self._binding_view(binding, timestamp=timestamp)
                if binding is not None
                else None
            )

    def bind_session_account(
        self,
        *,
        session_id: str,
        provider_id: str,
        account_id: str | None = None,
        credential_id: str | None = None,
        label: str | None = None,
        alias: str | None = None,
    ) -> dict[str, Any] | None:
        session = str(session_id).strip()
        provider = str(provider_id).strip().lower()
        if (
            not session
            or not provider
            or not any((account_id, credential_id, label, alias))
        ):
            raise ValueError(
                "session_id, provider_id, and one account selector are required"
            )
        timestamp = now_ms()
        with self._transaction() as connection:
            account, binding, changed = self._select_account(
                connection,
                provider_id=provider,
                account_id=account_id,
                credential_id=credential_id,
                label=label,
                alias=alias,
                session_id=session,
                persist_binding=True,
            )
            if account is None or binding is None:
                return None
            return self._apply_binding_view(
                self._account_view(account),
                binding,
                timestamp=timestamp,
                changed=changed,
            )

    def clear_session_account_binding(
        self,
        session_id: str,
        provider_id: str,
    ) -> bool:
        with self._transaction() as connection:
            result = connection.execute(
                """DELETE FROM session_account_bindings
                   WHERE session_id = ? AND provider_id = ?""",
                (str(session_id).strip(), str(provider_id).strip().lower()),
            )
            return result.rowcount > 0

    def mark_account_rate_limited(
        self,
        account_id: str,
        blocked_until_ms: int,
    ) -> bool:
        timestamp = now_ms()
        blocked_until = max(timestamp, int(blocked_until_ms))
        with self._transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM accounts WHERE account_id = ?",
                (str(account_id),),
            ).fetchone()
            if exists is None:
                return False
            connection.execute(
                """INSERT INTO account_rate_limits
                   (account_id, blocked_until_ms, updated_at_ms)
                   VALUES (?, ?, ?)
                   ON CONFLICT(account_id) DO UPDATE SET
                       blocked_until_ms = MAX(
                           account_rate_limits.blocked_until_ms,
                           excluded.blocked_until_ms
                       ),
                       updated_at_ms = excluded.updated_at_ms""",
                (str(account_id), blocked_until, timestamp),
            )
            return True

    def inspect_refresh_state(self, account_id: str) -> dict[str, Any]:
        """Return refresh coordination metadata without selecting secret material."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM credential_refresh_state WHERE account_id = ?",
                (str(account_id),),
            ).fetchone()
            return self._refresh_state_view(row)

    def claim_oauth_refresh(
        self,
        *,
        account_id: str,
        expected_secret_version: int,
        owner_id: str,
        lease_duration_ms: int,
    ) -> dict[str, Any]:
        """Claim one durable OAuth refresh flight, recovering expired owners."""
        account_ref = str(account_id)
        owner_ref = str(owner_id).strip()
        if not owner_ref:
            raise ValueError("owner_id is required")
        timestamp = now_ms()
        lease_expires_at_ms = timestamp + max(1, int(lease_duration_ms))
        with self._transaction() as connection:
            account = connection.execute(
                """SELECT status, kind, secret_version FROM accounts
                   WHERE account_id = ?""",
                (account_ref,),
            ).fetchone()
            if (
                account is None
                or str(account["status"]) != "active"
                or str(account["kind"]) != "oauth2"
            ):
                return {"status": "unavailable"}
            current_version = int(account["secret_version"])
            if current_version != int(expected_secret_version):
                return {
                    "status": "superseded",
                    "secret_version": current_version,
                }
            previous = connection.execute(
                "SELECT * FROM credential_refresh_state WHERE account_id = ?",
                (account_ref,),
            ).fetchone()
            if (
                previous is not None
                and previous["owner_id"]
                and str(previous["owner_id"]) != owner_ref
                and previous["lease_expires_at_ms"] is not None
                and int(previous["lease_expires_at_ms"]) > timestamp
            ):
                return {
                    "status": "busy",
                    "lease_expires_at_ms": int(previous["lease_expires_at_ms"]),
                }
            if (
                previous is not None
                and not previous["owner_id"]
                and previous["retry_not_before_ms"] is not None
                and int(previous["retry_not_before_ms"]) > timestamp
            ):
                return {
                    "status": "deferred",
                    "retry_not_before_ms": int(previous["retry_not_before_ms"]),
                }
            recovered = bool(
                previous is not None
                and previous["owner_id"]
                and (
                    previous["lease_expires_at_ms"] is None
                    or int(previous["lease_expires_at_ms"]) <= timestamp
                )
            )
            result = connection.execute(
                """INSERT INTO credential_refresh_state
                   (account_id, owner_id, expected_secret_version,
                    lease_acquired_at_ms, lease_expires_at_ms,
                    retry_not_before_ms, updated_at_ms)
                   VALUES (?, ?, ?, ?, ?, NULL, ?)
                   ON CONFLICT(account_id) DO UPDATE SET
                       owner_id = excluded.owner_id,
                       expected_secret_version = excluded.expected_secret_version,
                       lease_acquired_at_ms = excluded.lease_acquired_at_ms,
                       lease_expires_at_ms = excluded.lease_expires_at_ms,
                       retry_not_before_ms = NULL,
                       updated_at_ms = excluded.updated_at_ms
                   WHERE credential_refresh_state.owner_id IS NULL
                      OR credential_refresh_state.lease_expires_at_ms IS NULL
                      OR credential_refresh_state.lease_expires_at_ms
                         <= excluded.lease_acquired_at_ms
                      OR credential_refresh_state.owner_id = excluded.owner_id""",
                (
                    account_ref,
                    owner_ref,
                    int(expected_secret_version),
                    timestamp,
                    lease_expires_at_ms,
                    timestamp,
                ),
            )
            if result.rowcount == 0:
                active = connection.execute(
                    """SELECT lease_expires_at_ms FROM credential_refresh_state
                       WHERE account_id = ?""",
                    (account_ref,),
                ).fetchone()
                return {
                    "status": "busy",
                    "lease_expires_at_ms": (
                        int(active["lease_expires_at_ms"])
                        if active is not None
                        and active["lease_expires_at_ms"] is not None
                        else timestamp
                    ),
                }
            return {
                "status": "acquired",
                "lease_expires_at_ms": lease_expires_at_ms,
                "recovered_stale_lease": recovered,
            }

    def renew_oauth_refresh(
        self,
        *,
        account_id: str,
        expected_secret_version: int,
        owner_id: str,
        lease_duration_ms: int,
    ) -> bool:
        """Extend a live owner so stale takeover cannot overlap its provider call."""
        timestamp = now_ms()
        lease_expires_at_ms = timestamp + max(1, int(lease_duration_ms))
        with self._transaction() as connection:
            result = connection.execute(
                """UPDATE credential_refresh_state
                   SET lease_expires_at_ms = ?, updated_at_ms = ?
                   WHERE account_id = ? AND owner_id = ?
                     AND expected_secret_version = ?
                     AND EXISTS (
                         SELECT 1 FROM accounts
                         WHERE accounts.account_id =
                               credential_refresh_state.account_id
                           AND accounts.status = 'active'
                           AND accounts.secret_version = ?
                     )""",
                (
                    lease_expires_at_ms,
                    timestamp,
                    str(account_id),
                    str(owner_id),
                    int(expected_secret_version),
                    int(expected_secret_version),
                ),
            )
            return result.rowcount > 0

    def complete_oauth_refresh(
        self,
        *,
        account_id: str,
        expected_secret_version: int,
        owner_id: str,
        material: Mapping[str, Any],
        expires_at_ms: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Commit refreshed material only while the durable claim still owns the CAS."""
        if not material.get("access_token") or not material.get("refresh_token"):
            raise ValueError("refreshed OAuth material is incomplete")
        account_ref = str(account_id)
        with self._transaction(immediate=True) as connection:
            timestamp = now_ms()
            account = connection.execute(
                "SELECT * FROM accounts WHERE account_id = ?",
                (account_ref,),
            ).fetchone()
            state = connection.execute(
                "SELECT * FROM credential_refresh_state WHERE account_id = ?",
                (account_ref,),
            ).fetchone()
            if (
                state is None
                or str(state["owner_id"] or "") != str(owner_id)
                or int(state["expected_secret_version"] or 0)
                != int(expected_secret_version)
                or state["lease_expires_at_ms"] is None
                or int(state["lease_expires_at_ms"]) <= timestamp
            ):
                return {"status": "claim_lost"}
            if account is None or str(account["status"]) != "active":
                return {"status": "unavailable"}
            current_version = int(account["secret_version"])
            if current_version != int(expected_secret_version):
                connection.execute(
                    """UPDATE credential_refresh_state
                       SET owner_id = NULL, expected_secret_version = NULL,
                           lease_acquired_at_ms = NULL, lease_expires_at_ms = NULL,
                           updated_at_ms = ?
                       WHERE account_id = ? AND owner_id = ?
                         AND expected_secret_version = ?""",
                    (
                        timestamp,
                        account_ref,
                        str(owner_id),
                        int(expected_secret_version),
                    ),
                )
                return {
                    "status": "superseded",
                    "secret_version": current_version,
                }
            previous_secret = connection.execute(
                """SELECT material FROM secrets
                   WHERE account_id = ? AND secret_version = ?
                     AND revoked_at_ms IS NULL
                   ORDER BY created_at_ms DESC LIMIT 1""",
                (account_ref, current_version),
            ).fetchone()
            previous_material = (
                self._decode_json(previous_secret["material"])
                if previous_secret is not None
                else {}
            )
            next_version = current_version + 1
            merged_metadata = self._decode_json(account["metadata_json"])
            if isinstance(metadata, Mapping):
                merged_metadata.update(dict(metadata))
            merged_metadata = dict(
                self._safe_metadata(merged_metadata, previous_material)
            )
            merged_metadata = dict(self._safe_metadata(merged_metadata, material))
            account_update = connection.execute(
                """UPDATE accounts
                   SET status = 'active', secret_version = ?, updated_at_ms = ?,
                       expires_at_ms = ?, metadata_json = ?
                   WHERE account_id = ? AND status = 'active'
                     AND secret_version = ?""",
                (
                    next_version,
                    timestamp,
                    int(expires_at_ms),
                    self._json(merged_metadata),
                    account_ref,
                    current_version,
                ),
            )
            if account_update.rowcount != 1:
                raise RuntimeError("credential refresh account CAS failed")
            connection.execute(
                "DELETE FROM secrets WHERE account_id = ?",
                (account_ref,),
            )
            connection.execute(
                """INSERT INTO secrets
                   (secret_id, account_id, material, secret_version, created_at_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    _id("bbsecret"),
                    account_ref,
                    self._json(material),
                    next_version,
                    timestamp,
                ),
            )
            state_update = connection.execute(
                """UPDATE credential_refresh_state
                   SET owner_id = NULL, expected_secret_version = NULL,
                       lease_acquired_at_ms = NULL, lease_expires_at_ms = NULL,
                       last_failure_class = NULL, last_failure_code = NULL,
                       last_failure_at_ms = NULL, retry_not_before_ms = NULL,
                       updated_at_ms = ?
                   WHERE account_id = ? AND owner_id = ?
                     AND expected_secret_version = ?
                     AND lease_expires_at_ms > ?""",
                (
                    timestamp,
                    account_ref,
                    str(owner_id),
                    int(expected_secret_version),
                    timestamp,
                ),
            )
            if state_update.rowcount != 1:
                raise RuntimeError("credential refresh claim CAS failed")
            updated = connection.execute(
                "SELECT * FROM accounts WHERE account_id = ?",
                (account_ref,),
            ).fetchone()
            return {
                "status": "completed",
                "credential": self._account_view(updated),
            }

    def fail_oauth_refresh(
        self,
        *,
        account_id: str,
        expected_secret_version: int,
        owner_id: str,
        failure_class: str,
        failure_code: str,
        retry_not_before_ms: int | None = None,
    ) -> bool:
        """Release a failed flight and tombstone definitive credential failures."""
        classification = str(failure_class).strip().lower()
        if classification not in {"transient", "definitive"}:
            raise ValueError("failure_class must be transient or definitive")
        account_ref = str(account_id)
        owner_ref = str(owner_id)
        with self._transaction(immediate=True) as connection:
            timestamp = now_ms()
            state = connection.execute(
                "SELECT * FROM credential_refresh_state WHERE account_id = ?",
                (account_ref,),
            ).fetchone()
            account = connection.execute(
                "SELECT status, secret_version FROM accounts WHERE account_id = ?",
                (account_ref,),
            ).fetchone()
            if (
                state is None
                or str(state["owner_id"] or "") != owner_ref
                or int(state["expected_secret_version"] or 0)
                != int(expected_secret_version)
                or state["lease_expires_at_ms"] is None
                or int(state["lease_expires_at_ms"]) <= timestamp
                or account is None
                or str(account["status"]) != "active"
                or int(account["secret_version"]) != int(expected_secret_version)
            ):
                return False
            retry_at = (
                max(timestamp, int(retry_not_before_ms))
                if classification == "transient" and retry_not_before_ms is not None
                else None
            )
            if classification == "definitive":
                account_update = connection.execute(
                    """UPDATE accounts SET status = 'revoked', updated_at_ms = ?
                       WHERE account_id = ? AND status = 'active'
                         AND secret_version = ?""",
                    (timestamp, account_ref, int(expected_secret_version)),
                )
                if account_update.rowcount != 1:
                    raise RuntimeError("credential refresh failure CAS failed")
                connection.execute(
                    "DELETE FROM secrets WHERE account_id = ?",
                    (account_ref,),
                )
                connection.execute(
                    """UPDATE leases SET released_at_ms = ?
                       WHERE account_id = ? AND released_at_ms IS NULL""",
                    (timestamp, account_ref),
                )
            state_update = connection.execute(
                """UPDATE credential_refresh_state
                   SET owner_id = NULL, expected_secret_version = NULL,
                       lease_acquired_at_ms = NULL, lease_expires_at_ms = NULL,
                       last_failure_class = ?, last_failure_code = ?,
                       last_failure_at_ms = ?, retry_not_before_ms = ?,
                       updated_at_ms = ?
                   WHERE account_id = ? AND owner_id = ?
                     AND expected_secret_version = ?
                     AND lease_expires_at_ms > ?""",
                (
                    classification,
                    str(failure_code)[:128],
                    timestamp,
                    retry_at,
                    timestamp,
                    account_ref,
                    owner_ref,
                    int(expected_secret_version),
                    timestamp,
                ),
            )
            if state_update.rowcount != 1:
                raise RuntimeError("credential refresh claim CAS failed")
            return True

    def acquire_lease(
        self,
        *,
        provider_id: str,
        session_id: str = "",
        endpoint_id: str = "",
        account_id: str | None = None,
        credential_id: str | None = None,
        label: str | None = None,
        alias: str | None = None,
        credential_class: str | None = None,
        minimum_validity_ms: int = 0,
        allow_expired: bool = False,
        bind_explicit: bool = True,
    ) -> dict[str, Any] | None:
        timestamp = now_ms()
        with self._transaction() as connection:
            connection.execute(
                "UPDATE leases SET released_at_ms = ? WHERE released_at_ms IS NULL AND expires_at_ms <= ?",
                (timestamp, timestamp),
            )
            account, binding, binding_changed = self._select_account(
                connection,
                provider_id=str(provider_id).strip().lower(),
                account_id=account_id,
                credential_id=credential_id,
                label=label,
                alias=alias,
                credential_class=credential_class,
                session_id=str(session_id),
                minimum_validity_ms=minimum_validity_ms,
                allow_expired=allow_expired,
                persist_binding=True,
                bind_explicit=bind_explicit,
            )
            if account is None:
                return None
            secret = connection.execute(
                """SELECT * FROM secrets WHERE account_id = ? AND revoked_at_ms IS NULL
                   ORDER BY secret_version DESC LIMIT 1""",
                (account["account_id"],),
            ).fetchone()
            if secret is None:
                return None
            account_expiry = (
                int(account["expires_at_ms"])
                if account["expires_at_ms"] is not None
                else timestamp + 300_000
            )
            lease_expiry = min(account_expiry, timestamp + 300_000)
            lease_id = _id("bblease")
            connection.execute(
                """INSERT INTO leases
                   (lease_id, account_id, session_id, endpoint_id, issued_at_ms, expires_at_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    lease_id,
                    account["account_id"],
                    str(session_id),
                    str(endpoint_id),
                    timestamp,
                    lease_expiry,
                ),
            )
            material = self._decode_json(secret["material"])
            if not material.get("api_key") and material.get("access_token"):
                material["api_key"] = material["access_token"]
            material["lease_id"] = lease_id
            material["account_id"] = account["account_id"]
            material["credential_id"] = account["credential_id"]
            material["secret_version"] = int(secret["secret_version"])
            material["expires_at_ms"] = account["expires_at_ms"]
            material["provider_id"] = account["provider_id"]
            material["auth_scheme_id"] = account["auth_scheme_id"]
            material["label"] = account["label"]
            material["credential_kind"] = account["kind"]
            material["credential_source"] = account["source"]
            return self._apply_binding_view(
                material,
                binding,
                timestamp=timestamp,
                changed=binding_changed,
            )

    def release_lease(self, lease_id: str) -> bool:
        with self._transaction() as connection:
            result = connection.execute(
                """UPDATE leases SET released_at_ms = ?
                   WHERE lease_id = ? AND released_at_ms IS NULL""",
                (now_ms(), str(lease_id)),
            )
            return result.rowcount > 0

    def disable_accounts(
        self,
        *,
        provider_id: str | None = None,
        account_id: str | None = None,
        credential_id: str | None = None,
        label: str | None = None,
    ) -> int:
        clauses = ["status = 'active'"]
        params: list[Any] = []
        if provider_id:
            clauses.append("provider_id = ?")
            params.append(str(provider_id).strip().lower())
        if account_id:
            clauses.append("account_id = ?")
            params.append(str(account_id))
        if credential_id:
            clauses.append("credential_id = ?")
            params.append(str(credential_id))
        if label:
            clauses.append("label = ?")
            params.append(str(label))
        timestamp = now_ms()
        with self._transaction() as connection:
            rows = connection.execute(
                f"SELECT account_id FROM accounts WHERE {' AND '.join(clauses)}",
                params,
            ).fetchall()
            if not rows:
                return 0
            account_ids = [str(row["account_id"]) for row in rows]
            marks = ",".join("?" for _ in account_ids)
            connection.execute(
                f"""UPDATE accounts SET status = 'disabled', updated_at_ms = ?
                    WHERE account_id IN ({marks})""",
                [timestamp, *account_ids],
            )
            connection.execute(
                f"""UPDATE leases SET released_at_ms = ?
                    WHERE account_id IN ({marks}) AND released_at_ms IS NULL""",
                [timestamp, *account_ids],
            )
            connection.execute(
                f"""UPDATE credential_refresh_state
                    SET owner_id = NULL, expected_secret_version = NULL,
                        lease_acquired_at_ms = NULL, lease_expires_at_ms = NULL,
                        updated_at_ms = ?
                    WHERE account_id IN ({marks})""",
                [timestamp, *account_ids],
            )
            return len(account_ids)

    def revoke_accounts(
        self,
        *,
        provider_id: str | None = None,
        account_id: str | None = None,
        credential_id: str | None = None,
        label: str | None = None,
    ) -> int:
        clauses = ["status != 'revoked'"]
        params: list[Any] = []
        if provider_id:
            clauses.append("provider_id = ?")
            params.append(str(provider_id).strip().lower())
        if account_id:
            clauses.append("account_id = ?")
            params.append(str(account_id))
        if credential_id:
            clauses.append("credential_id = ?")
            params.append(str(credential_id))
        if label:
            clauses.append("label = ?")
            params.append(str(label))
        timestamp = now_ms()
        with self._transaction() as connection:
            rows = connection.execute(
                f"SELECT account_id FROM accounts WHERE {' AND '.join(clauses)}", params
            ).fetchall()
            if not rows:
                return 0
            account_ids = [str(row["account_id"]) for row in rows]
            marks = ",".join("?" for _ in account_ids)
            connection.execute(
                f"UPDATE accounts SET status = 'revoked', updated_at_ms = ? WHERE account_id IN ({marks})",
                [timestamp, *account_ids],
            )
            connection.execute(
                f"DELETE FROM secrets WHERE account_id IN ({marks})",
                account_ids,
            )
            connection.execute(
                f"UPDATE leases SET released_at_ms = ? WHERE account_id IN ({marks}) AND released_at_ms IS NULL",
                [timestamp, *account_ids],
            )
            connection.execute(
                f"""UPDATE credential_refresh_state
                    SET owner_id = NULL, expected_secret_version = NULL,
                        lease_acquired_at_ms = NULL, lease_expires_at_ms = NULL,
                        updated_at_ms = ?
                    WHERE account_id IN ({marks})""",
                [timestamp, *account_ids],
            )
            return len(account_ids)

    @staticmethod
    def _expired_login_problem() -> dict[str, Any]:
        return {
            "code": "oauth_login_expired",
            "message": "OAuth login session expired",
            "details": {},
        }

    def _expire_stale_login(
        self,
        connection: sqlite3.Connection,
        login_session_id: str,
        timestamp: int,
    ) -> None:
        connection.execute(
            """UPDATE login_sessions
               SET status = 'expired', updated_at_ms = ?,
                   problem_json = ?, flow_json = NULL
               WHERE login_session_id = ?
                 AND status IN ('pending', 'completing')
                 AND expires_at_ms IS NOT NULL AND expires_at_ms <= ?""",
            (
                timestamp,
                self._json(self._expired_login_problem()),
                str(login_session_id),
                timestamp,
            ),
        )

    def create_login(
        self,
        provider_id: str,
        status: str,
        problem: Mapping[str, Any] | None = None,
        flow: Mapping[str, Any] | None = None,
        expires_in_ms: int | None = None,
    ) -> dict[str, Any]:
        timestamp = now_ms()
        login_session_id = _id("bblogin")
        normalized_status = str(status)
        expires_at_ms = None
        if normalized_status == "pending":
            duration_ms = (
                _LOGIN_EXPIRY_MS if expires_in_ms is None else expires_in_ms
            )
            if (
                isinstance(duration_ms, bool)
                or not isinstance(duration_ms, int)
                or duration_ms <= 0
                or duration_ms > (2**63 - 1 - timestamp)
            ):
                raise ValueError("login expiry duration is invalid")
            expires_at_ms = timestamp + duration_ms
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO login_sessions
                   (login_session_id, provider_id, status, created_at_ms,
                    updated_at_ms, expires_at_ms, problem_json, flow_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    login_session_id,
                    str(provider_id),
                    normalized_status,
                    timestamp,
                    timestamp,
                    expires_at_ms,
                    self._json(problem),
                    self._json(flow),
                ),
            )
        return self.get_login(login_session_id) or {
            "login_session_id": login_session_id,
            "provider_id": provider_id,
            "status": normalized_status,
            "expires_at_ms": expires_at_ms,
        }

    def get_login(
        self, login_session_id: str, *, include_flow: bool = False
    ) -> dict[str, Any] | None:
        with self._transaction() as connection:
            timestamp = now_ms()
            self._expire_stale_login(connection, str(login_session_id), timestamp)
            row = connection.execute(
                "SELECT * FROM login_sessions WHERE login_session_id = ?",
                (str(login_session_id),),
            ).fetchone()
            if row is None:
                return None
            problem = (
                self._decode_json(row["problem_json"]) if row["problem_json"] else None
            )
            result: dict[str, Any] = {
                "login_session_id": row["login_session_id"],
                "provider_id": row["provider_id"],
                "status": row["status"],
                "created_at_ms": int(row["created_at_ms"]),
                "updated_at_ms": int(row["updated_at_ms"]),
                "expires_at_ms": (
                    int(row["expires_at_ms"])
                    if row["expires_at_ms"] is not None
                    else None
                ),
                "problem": problem or None,
            }
            if include_flow:
                result["flow"] = (
                    self._decode_json(row["flow_json"]) if row["flow_json"] else {}
                )
            return result

    def claim_pending_login(self, login_session_id: str) -> bool:
        with self._transaction() as connection:
            timestamp = now_ms()
            self._expire_stale_login(connection, str(login_session_id), timestamp)
            result = connection.execute(
                """UPDATE login_sessions
                   SET status = 'completing', updated_at_ms = ?
                   WHERE login_session_id = ? AND status = 'pending'""",
                (timestamp, str(login_session_id)),
            )
            return result.rowcount > 0


    def finish_claimed_login(
        self,
        login_session_id: str,
        status: str,
        problem: Mapping[str, Any] | None = None,
    ) -> bool:
        normalized_status = str(status)
        timestamp = now_ms()
        with self._transaction() as connection:
            self._expire_stale_login(
                connection,
                str(login_session_id),
                timestamp,
            )
            result = connection.execute(
                """UPDATE login_sessions
                   SET status = ?, updated_at_ms = ?, problem_json = ?,
                       flow_json = CASE
                           WHEN ? IN ('completed', 'failed', 'cancelled', 'expired')
                           THEN NULL ELSE flow_json END
                   WHERE login_session_id = ? AND status = 'completing'""",
                (
                    normalized_status,
                    timestamp,
                    self._json(problem),
                    normalized_status,
                    str(login_session_id),
                ),
            )
            return result.rowcount > 0

    def cancel_login(self, login_session_id: str) -> bool:
        with self._transaction() as connection:
            timestamp = now_ms()
            self._expire_stale_login(connection, str(login_session_id), timestamp)
            result = connection.execute(
                """UPDATE login_sessions
                   SET status = 'cancelled', updated_at_ms = ?, flow_json = NULL
                   WHERE login_session_id = ?
                     AND status IN ('pending', 'completing')""",
                (timestamp, str(login_session_id)),
            )
            return result.rowcount > 0
