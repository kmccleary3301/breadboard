"""SQLite-backed credential metadata and secret storage for the provider broker."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping


_ACTIVE_STATUSES = ("active",)


def now_ms() -> int:
    return int(time.time() * 1000)


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12).replace('-', '').replace('_', '')}"


def default_store_path() -> Path:
    """Return the user state path, without reading or creating credential data."""
    explicit = (
        os.environ.get("BREADBOARD_CREDENTIAL_STORE_PATH")
        or os.environ.get("BREADBOARD_CREDENTIAL_DB")
    )
    if explicit:
        return Path(explicit).expanduser().resolve()
    state_dir = os.environ.get("BREADBOARD_STATE_DIR")
    root = Path(state_dir).expanduser() if state_dir else Path.home() / ".breadboard"
    return (root / "credentials.sqlite3").resolve()


class SQLiteCredentialStore:
    """Durable metadata store with secret material isolated in a separate table.

    ``accounts`` and ``leases`` are safe to inspect.  Secret material is only
    selected by the broker's narrow execution path and is never returned by
    :meth:`inspect_accounts` or :meth:`list_accounts`.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = str(Path(path).expanduser()) if path is not None else str(default_store_path())
        self._memory = self.path == ":memory:"
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        if not self._memory:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._memory:
            if self._connection is None:
                self._connection = sqlite3.connect(":memory:", check_same_thread=False)
            return self._connection
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if not self._memory:
                    connection.close()

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
                CREATE TABLE IF NOT EXISTS login_sessions (
                    login_session_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    problem_json TEXT,
                    flow_json TEXT
                );
                """
            )
            columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(login_sessions)").fetchall()}
            if "flow_json" not in columns:
                connection.execute("ALTER TABLE login_sessions ADD COLUMN flow_json TEXT")

    @staticmethod
    def _json(value: Any) -> str:
        try:
            return json.dumps(value if isinstance(value, Mapping) else {}, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return "{}"

    @staticmethod
    def _decode_json(value: str | None) -> dict[str, Any]:
        try:
            decoded = json.loads(value or "{}")
            return dict(decoded) if isinstance(decoded, Mapping) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

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
        timestamp = now_ms()
        encoded_metadata = self._json(metadata)
        material_copy = dict(material)
        with self._transaction() as connection:
            row = None
            if account_id:
                row = connection.execute(
                    "SELECT * FROM accounts WHERE account_id = ?", (str(account_id),)
                ).fetchone()
            if row is None:
                row = connection.execute(
                    """SELECT * FROM accounts
                       WHERE provider_id = ? AND label = ? AND alias = ?
                         AND status != 'revoked'
                       ORDER BY updated_at_ms DESC LIMIT 1""",
                    (provider_id, label, alias),
                ).fetchone()
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
                    """UPDATE secrets SET revoked_at_ms = ?
                       WHERE account_id = ? AND revoked_at_ms IS NULL""",
                    (timestamp, account_id),
                )
            secret_id = _id("bbsecret")
            connection.execute(
                """INSERT INTO secrets
                   (secret_id, account_id, material, secret_version, created_at_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (secret_id, account_id, self._json(material_copy), version, timestamp),
            )
            return self._account_view(
                connection.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
            )

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
        if not provider_id or not label or not material.get("access_token") or not material.get("refresh_token"):
            raise ValueError("provider_id, label, access_token, and refresh_token are required")
        timestamp = now_ms()
        with self._transaction() as connection:
            row = None
            if account_id:
                row = connection.execute("SELECT * FROM accounts WHERE account_id = ?", (str(account_id),)).fetchone()
            if row is None:
                row = connection.execute(
                    """SELECT * FROM accounts WHERE provider_id = ? AND label = ? AND alias = ?
                       AND status != 'revoked' ORDER BY updated_at_ms DESC LIMIT 1""",
                    (provider_id, label, alias),
                ).fetchone()
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
                    (account_id, credential_id, provider_id, auth_scheme_id, label, alias, source,
                     version, timestamp, timestamp, expires_at_ms, self._json(metadata)),
                )
            else:
                account_id = str(row["account_id"])
                credential_id = str(row["credential_id"])
                version = int(row["secret_version"]) + 1
                connection.execute(
                    """UPDATE accounts SET auth_scheme_id = ?, kind = 'oauth2', status = 'active',
                       source = ?, secret_version = ?, updated_at_ms = ?, expires_at_ms = ?, metadata_json = ?
                       WHERE account_id = ?""",
                    (auth_scheme_id, source, version, timestamp, expires_at_ms, self._json(metadata), account_id),
                )
                connection.execute(
                    """UPDATE secrets SET revoked_at_ms = ? WHERE account_id = ? AND revoked_at_ms IS NULL""",
                    (timestamp, account_id),
                )
            connection.execute(
                """INSERT INTO secrets (secret_id, account_id, material, secret_version, created_at_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (_id("bbsecret"), account_id, self._json(material), version, timestamp),
            )
            return self._account_view(connection.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone())

    def list_accounts(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            if provider_id:
                rows = connection.execute(
                    "SELECT * FROM accounts WHERE provider_id = ? ORDER BY created_at_ms, account_id",
                    (str(provider_id).strip().lower(),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM accounts ORDER BY created_at_ms, account_id"
                ).fetchall()
            return [self._account_view(row) for row in rows]

    def inspect_accounts(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        """Return metadata only; this method intentionally never selects ``secrets``."""
        return self.list_accounts(provider_id)

    def _select_account(
        self,
        connection: sqlite3.Connection,
        *,
        provider_id: str,
        account_id: str | None = None,
        credential_id: str | None = None,
        label: str | None = None,
    ) -> sqlite3.Row | None:
        if account_id:
            return connection.execute(
                "SELECT * FROM accounts WHERE account_id = ? AND provider_id = ?",
                (account_id, provider_id),
            ).fetchone()
        if credential_id:
            return connection.execute(
                "SELECT * FROM accounts WHERE credential_id = ? AND provider_id = ?",
                (credential_id, provider_id),
            ).fetchone()
        if label:
            return connection.execute(
                """SELECT * FROM accounts WHERE provider_id = ? AND label = ?
                   ORDER BY updated_at_ms DESC LIMIT 1""",
                (provider_id, label),
            ).fetchone()
        return connection.execute(
            """SELECT * FROM accounts WHERE provider_id = ? AND status = 'active'
               ORDER BY updated_at_ms DESC LIMIT 1""",
            (provider_id,),
        ).fetchone()

    def acquire_lease(
        self,
        *,
        provider_id: str,
        session_id: str = "",
        endpoint_id: str = "",
        account_id: str | None = None,
        credential_id: str | None = None,
        label: str | None = None,
        minimum_validity_ms: int = 0,
    ) -> dict[str, Any] | None:
        timestamp = now_ms()
        with self._transaction() as connection:
            connection.execute(
                "UPDATE leases SET released_at_ms = ? WHERE released_at_ms IS NULL AND expires_at_ms <= ?",
                (timestamp, timestamp),
            )
            account = self._select_account(
                connection,
                provider_id=str(provider_id).strip().lower(),
                account_id=account_id,
                credential_id=credential_id,
                label=label,
            )
            if account is None or account["status"] != "active":
                return None
            if account["expires_at_ms"] is not None:
                expires = int(account["expires_at_ms"])
                if expires <= timestamp + max(0, int(minimum_validity_ms)):
                    return None
            secret = connection.execute(
                """SELECT * FROM secrets WHERE account_id = ? AND revoked_at_ms IS NULL
                   ORDER BY secret_version DESC LIMIT 1""",
                (account["account_id"],),
            ).fetchone()
            if secret is None:
                return None
            account_expiry = int(account["expires_at_ms"]) if account["expires_at_ms"] is not None else timestamp + 300_000
            lease_expiry = min(account_expiry, timestamp + 300_000)
            lease_id = _id("bblease")
            connection.execute(
                """INSERT INTO leases
                   (lease_id, account_id, session_id, endpoint_id, issued_at_ms, expires_at_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (lease_id, account["account_id"], str(session_id), str(endpoint_id), timestamp, lease_expiry),
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
            return material

    def redeem_lease(
        self,
        *,
        lease_id: str,
        provider_id: str,
        endpoint_id: str = "",
    ) -> dict[str, Any] | None:
        timestamp = now_ms()
        normalized_lease_id = str(lease_id).strip()
        normalized_provider_id = str(provider_id).strip().lower()
        normalized_endpoint_id = str(endpoint_id)
        if not normalized_lease_id or not normalized_provider_id:
            return None
        with self._transaction() as connection:
            lease = connection.execute(
                """SELECT * FROM leases
                   WHERE lease_id = ? AND released_at_ms IS NULL AND expires_at_ms > ?""",
                (normalized_lease_id, timestamp),
            ).fetchone()
            if lease is None:
                return None
            if lease["endpoint_id"] and str(lease["endpoint_id"]) != normalized_endpoint_id:
                return None
            account = connection.execute(
                """SELECT * FROM accounts
                   WHERE account_id = ? AND provider_id = ? AND status = 'active'""",
                (lease["account_id"], normalized_provider_id),
            ).fetchone()
            if account is None:
                return None
            if account["expires_at_ms"] is not None and int(account["expires_at_ms"]) <= timestamp:
                return None
            secret = connection.execute(
                """SELECT * FROM secrets WHERE account_id = ? AND revoked_at_ms IS NULL
                   ORDER BY secret_version DESC LIMIT 1""",
                (account["account_id"],),
            ).fetchone()
            if secret is None:
                return None
            material = self._decode_json(secret["material"])
            if not material.get("api_key") and material.get("access_token"):
                material["api_key"] = material["access_token"]
            material["lease_id"] = normalized_lease_id
            material["lease_expires_at_ms"] = int(lease["expires_at_ms"])
            material["account_id"] = account["account_id"]
            material["credential_id"] = account["credential_id"]
            material["secret_version"] = int(secret["secret_version"])
            material["expires_at_ms"] = account["expires_at_ms"]
            material["provider_id"] = account["provider_id"]
            material["auth_scheme_id"] = account["auth_scheme_id"]
            material["label"] = account["label"]
            return material

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
        with self._transaction() as connection:
            result = connection.execute(
                f"UPDATE accounts SET status = 'disabled', updated_at_ms = ? WHERE {' AND '.join(clauses)}",
                [now_ms(), *params],
            )
            return result.rowcount

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
                f"UPDATE secrets SET revoked_at_ms = ? WHERE account_id IN ({marks}) AND revoked_at_ms IS NULL",
                [timestamp, *account_ids],
            )
            connection.execute(
                f"UPDATE leases SET released_at_ms = ? WHERE account_id IN ({marks}) AND released_at_ms IS NULL",
                [timestamp, *account_ids],
            )
            return len(account_ids)

    def create_login(
        self,
        provider_id: str,
        status: str,
        problem: Mapping[str, Any] | None = None,
        flow: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        timestamp = now_ms()
        login_session_id = _id("bblogin")
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO login_sessions
                   (login_session_id, provider_id, status, created_at_ms, updated_at_ms, problem_json, flow_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (login_session_id, str(provider_id), str(status), timestamp, timestamp,
                 self._json(problem), self._json(flow)),
            )
        return self.get_login(login_session_id) or {
            "login_session_id": login_session_id,
            "provider_id": provider_id,
            "status": status,
        }

    def get_login(self, login_session_id: str, *, include_flow: bool = False) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM login_sessions WHERE login_session_id = ?", (str(login_session_id),)
            ).fetchone()
            if row is None:
                return None
            problem = self._decode_json(row["problem_json"]) if row["problem_json"] else None
            result: dict[str, Any] = {
                "login_session_id": row["login_session_id"],
                "provider_id": row["provider_id"],
                "status": row["status"],
                "created_at_ms": int(row["created_at_ms"]),
                "updated_at_ms": int(row["updated_at_ms"]),
                "problem": problem or None,
            }
            if include_flow:
                result["flow"] = self._decode_json(row["flow_json"]) if row["flow_json"] else {}
            return result

    def finish_login(
        self,
        login_session_id: str,
        status: str,
        problem: Mapping[str, Any] | None = None,
    ) -> bool:
        with self._transaction() as connection:
            result = connection.execute(
                """UPDATE login_sessions SET status = ?, updated_at_ms = ?, problem_json = ?
                   WHERE login_session_id = ?""",
                (str(status), now_ms(), self._json(problem), str(login_session_id)),
            )
            return result.rowcount > 0

    def cancel_login(self, login_session_id: str) -> bool:
        with self._transaction() as connection:
            result = connection.execute(
                """UPDATE login_sessions SET status = 'cancelled', updated_at_ms = ?
                   WHERE login_session_id = ? AND status NOT IN ('cancelled', 'completed')""",
                (now_ms(), str(login_session_id)),
            )
            return result.rowcount > 0
