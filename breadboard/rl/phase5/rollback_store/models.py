from __future__ import annotations

from ._imports import *

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ROLE_RE = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_MAX_RECORD_BYTES = 4 * 1024 * 1024
_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
_MAX_RECEIPT_PAYLOADS = 64
_MAX_AGGREGATE_RECEIPT_PAYLOAD_BYTES = 2 * 1024 * 1024
_MAX_ROLLBACK_QUARANTINE_PAIRS = 256
_MAX_ROLLBACK_QUARANTINE_BYTES = 64 * 1024 * 1024
_MAX_ROLLBACK_QUARANTINE_TOMBSTONE_BYTES = 64 * 1024
_MAX_ROLLBACK_QUARANTINE_ARTIFACTS = 2 * _MAX_ROLLBACK_QUARANTINE_PAIRS
_MAX_ROOT_ENTRIES = 2048
_MAX_ROOT_NAME_BYTES = 64 * 1024 * 1024
_MAX_ABANDONED_TEMPS = 128
_MAX_ABANDONED_TEMP_NAME_BYTES = 32 * 1024
_MAX_ABANDONED_TEMP_BYTES = 64 * 1024 * 1024
_MAX_CLEANUP_MANIFEST_BYTES = 2 * 1024 * 1024
_CLEANUP_PREPARING_NAME = "preparing"
_CLEANUP_COMMITTED_NAME = "committed"
_CLEANUP_PREPARING_TEMP_NAME = ".preparing.tmp"
_CLEANUP_COMMITTED_TEMP_NAME = ".committed.tmp"
_CLEANUP_RECEIPT_NAME = "receipt"
_CLEANUP_RECEIPT_TEMP_NAME = ".receipt.tmp"
_TEST_CLEANUP_FAULT_HOOK: Any = None


class _CleanupInjectedCrash(BaseException):
    pass


_ROLLBACK_TERMINAL_DIRECTORY = ".terminal-rollback"
_ROLLBACK_TERMINAL_ANCHOR_INDEX = ".terminal-rollback-anchors"
_REQUEST_KEYS = frozenset(
    (
        "affected_episode_ids",
        "approved_tuple",
        "dependent_root_refs",
        "evidence_invalidations",
        "failed_rerun_invalidations",
        "frozen_active_generation",
        "rerun_authoring_input",
        "rerun_source_identities",
        "rerun_input_path",
        "revocation_publish_request",
        "rollback_id",
        "schema_version",
        "source_deletion_plan",
    )
)
_OBSERVATION_KEYS = frozenset(
    (
        "evidence_id",
        "exit_code",
        "graph_alias",
        "kind",
        "observed_bytes_base64",
        "observed_identity",
        "observed_target_node_id",
        "schema_version",
    )
)
_OBSERVATION_KINDS = frozenset(("active_status", "artifact", "identity", "rerun"))


class RollbackStoreError(RuntimeError):
    pass


class RollbackValidationError(RollbackStoreError, ValueError):
    pass


class RollbackConflictError(RollbackStoreError):
    pass


class RollbackIdempotencyConflict(RollbackConflictError):
    pass


class RollbackCorruptionError(RollbackStoreError):
    pass


class DependentIneligibleError(RollbackStoreError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("value is not canonical JSON") from error


def canonical_digest(value: bytes) -> str:
    if type(value) is not bytes:
        raise RollbackValidationError("digest input must be exact bytes")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _require_digest(value: object, name: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise RollbackValidationError(f"{name} must be a lowercase sha256 digest")
    return value


def _require_id(value: object, name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise RollbackValidationError(f"{name} has an invalid identity")
    return value


def _require_role(value: object) -> str:
    if type(value) is not str or _ROLE_RE.fullmatch(value) is None:
        raise RollbackValidationError("tuple reference role is invalid")
    return value


def _require_int(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise RollbackValidationError(f"{name} must be an integer >= {minimum}")
    return value


def _require_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise RollbackValidationError(f"{name} must be an exact boolean")
    return value


def _require_object(
    value: object, keys: frozenset[str], name: str
) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise RollbackValidationError(f"{name} must contain exactly {sorted(keys)}")
    return value


def _require_tuple(value: object, name: str) -> list[Any]:
    if type(value) is not list:
        raise RollbackValidationError(f"{name} must be a canonical array")
    return value


def _decode_canonical_payload(raw: bytes, name: str) -> Mapping[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_PAYLOAD_BYTES:
        raise RollbackValidationError(
            f"{name} must be exact non-empty bytes within the size bound"
        )
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RollbackValidationError(f"{name} must be canonical JSON") from error
    if type(decoded) is not dict or raw != canonical_json_bytes(decoded):
        raise RollbackValidationError(f"{name} must be a canonical JSON object")
    return decoded


def _require_sorted_unique_array(value: object, name: str) -> list[Any]:
    items = _require_tuple(value, name)
    canonical_items = tuple(canonical_json_bytes(item) for item in items)
    if canonical_items != tuple(sorted(canonical_items)) or len(
        set(canonical_items)
    ) != len(canonical_items):
        raise RollbackValidationError(f"{name} must be unique and sorted")
    return items


def _validate_exact_model(value: object, model_type: Any, name: str) -> Any:
    if type(value) is not dict:
        raise RollbackValidationError(f"{name} must be an exact object")
    try:
        model = model_type.model_validate_json(canonical_json_bytes(value), strict=True)
    except (TypeError, ValueError) as error:
        raise RollbackValidationError(f"{name} is invalid") from error
    if model.model_dump(mode="json") != value:
        raise RollbackValidationError(f"{name} projection is not exact")
    return model


def _validate_absolute_normalized_path(value: object, name: str) -> str:
    if (
        type(value) is not str
        or not value.startswith("/")
        or os.path.normpath(value) != value
        or len(value) > 4096
    ):
        raise RollbackValidationError(f"{name} must be an absolute normalized path")
    return value


@dataclass(frozen=True, slots=True)
class _ImmutableFileIdentity:
    device: int
    inode: int
    size_bytes: int
    mtime_ns: str
    ctime_ns: str
    owner_uid: int
    mode: int
    nlink: int

    @classmethod
    def from_object(cls, value: object, name: str) -> "_ImmutableFileIdentity":
        item = _require_object(
            value,
            frozenset(
                (
                    "ctime_ns",
                    "device",
                    "inode",
                    "mode",
                    "mtime_ns",
                    "nlink",
                    "owner_uid",
                    "size_bytes",
                )
            ),
            name,
        )
        for field_name in (
            "device",
            "inode",
            "mode",
            "nlink",
            "owner_uid",
            "size_bytes",
        ):
            _require_int(item[field_name], f"{name} {field_name}")
        for field_name in ("ctime_ns", "mtime_ns"):
            value = item[field_name]
            if type(value) is not str or not value.isascii() or not value.isdecimal():
                raise RollbackValidationError(
                    f"{name} {field_name} must be decimal nanoseconds"
                )
        identity = cls(
            device=item["device"],
            inode=item["inode"],
            size_bytes=item["size_bytes"],
            mtime_ns=item["mtime_ns"],
            ctime_ns=item["ctime_ns"],
            owner_uid=item["owner_uid"],
            mode=item["mode"],
            nlink=item["nlink"],
        )
        if (
            identity.inode < 1
            or identity.size_bytes < 1
            or identity.nlink != 1
            or identity.mode & 0o222
        ):
            raise RollbackValidationError(
                f"{name} must bind a non-writable, single-link regular file"
            )
        return identity

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "_ImmutableFileIdentity":
        return cls(
            device=value.st_dev,
            inode=value.st_ino,
            size_bytes=value.st_size,
            mtime_ns=str(value.st_mtime_ns),
            ctime_ns=str(value.st_ctime_ns),
            owner_uid=value.st_uid,
            mode=stat.S_IMODE(value.st_mode),
            nlink=value.st_nlink,
        )

    def canonical_object(self) -> dict[str, object]:
        return {
            "ctime_ns": self.ctime_ns,
            "device": self.device,
            "inode": self.inode,
            "mode": self.mode,
            "mtime_ns": self.mtime_ns,
            "nlink": self.nlink,
            "owner_uid": self.owner_uid,
            "size_bytes": self.size_bytes,
        }


def _open_pinned_parent(path: str, name: str) -> tuple[int, str]:
    normalized = _validate_absolute_normalized_path(path, f"{name} path")
    parts = normalized.split("/")
    leaf = parts[-1]
    if not leaf:
        raise RollbackValidationError(f"{name} path must name a file")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open("/", flags)
    try:
        for component in parts[1:-1]:
            next_descriptor = os.open(
                component,
                flags,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, leaf
    except BaseException:
        os.close(descriptor)
        raise


@dataclass(slots=True)
class _PinnedImmutableSource:
    path: str
    name: str
    parent_fd: int
    file_fd: int
    parent_identity: tuple[int, int, int, int]
    identity: _ImmutableFileIdentity
    raw: bytes
    digest: str

    @classmethod
    def capture(
        cls,
        path: str,
        name: str,
        expected_digest: str,
        expected_identity: _ImmutableFileIdentity,
    ) -> "_PinnedImmutableSource":
        try:
            parent_fd, leaf = _open_pinned_parent(path, name)
        except OSError as error:
            raise RollbackValidationError(
                f"{name} parent authority is not securely readable"
            ) from error
        file_fd = -1
        try:
            file_fd = os.open(
                leaf,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            before = os.fstat(file_fd)
            observed = _ImmutableFileIdentity.from_stat(before)
            if (
                not stat.S_ISREG(before.st_mode)
                or observed != expected_identity
                or observed.nlink != 1
                or observed.mode & 0o222
                or observed.size_bytes > _MAX_RECORD_BYTES
            ):
                raise RollbackValidationError(
                    f"{name} immutable file identity mismatch"
                )
            chunks: list[bytes] = []
            remaining = observed.size_bytes
            while remaining:
                chunk = os.read(file_fd, min(65536, remaining))
                if not chunk:
                    raise RollbackValidationError(f"{name} changed during pinned read")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(file_fd, 1):
                raise RollbackValidationError(f"{name} grew during pinned read")
            raw = b"".join(chunks)
            after = os.fstat(file_fd)
            path_state = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if (
                _ImmutableFileIdentity.from_stat(after) != observed
                or _ImmutableFileIdentity.from_stat(path_state) != observed
                or canonical_digest(raw) != expected_digest
            ):
                raise RollbackValidationError(
                    f"{name} pinned bytes or identity changed"
                )
            parent = os.fstat(parent_fd)
            return cls(
                path=path,
                name=name,
                parent_fd=parent_fd,
                file_fd=file_fd,
                parent_identity=(
                    parent.st_dev,
                    parent.st_ino,
                    parent.st_uid,
                    stat.S_IMODE(parent.st_mode),
                ),
                identity=observed,
                raw=raw,
                digest=expected_digest,
            )
        except OSError as error:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(parent_fd)
            raise RollbackValidationError(f"{name} is not securely readable") from error
        except BaseException:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(parent_fd)
            raise

    def revalidate(self) -> None:
        fresh_parent_fd, leaf = _open_pinned_parent(self.path, self.name)
        try:
            fresh_parent = os.fstat(fresh_parent_fd)
            path_state = os.stat(leaf, dir_fd=fresh_parent_fd, follow_symlinks=False)
            if (
                (
                    fresh_parent.st_dev,
                    fresh_parent.st_ino,
                    fresh_parent.st_uid,
                    stat.S_IMODE(fresh_parent.st_mode),
                )
                != self.parent_identity
                or _ImmutableFileIdentity.from_stat(path_state) != self.identity
                or _ImmutableFileIdentity.from_stat(os.fstat(self.file_fd))
                != self.identity
            ):
                raise RollbackValidationError(f"{self.name} pinned authority changed")
        finally:
            os.close(fresh_parent_fd)

    def close(self) -> None:
        os.close(self.file_fd)
        os.close(self.parent_fd)


def _revalidate_source_capsules(
    capsules: Sequence[_PinnedImmutableSource],
) -> None:
    for capsule in capsules:
        capsule.revalidate()


def _source_identity_from_projection(value: object) -> Any:
    from breadboard.rl.phase5.g4_source_deletion import (
        SourceOwnershipIdentity,
    )

    item = _require_object(
        value,
        frozenset(
            (
                "ctime_ns",
                "device",
                "inode",
                "kind",
                "relative_path",
                "root_authority_id",
                "root_path",
                "sha256",
                "size_bytes",
            )
        ),
        "owned source identity",
    )
    numbers: dict[str, int] = {}
    for field in ("ctime_ns", "device", "inode", "size_bytes"):
        raw = item[field]
        if type(raw) is not str or not raw.isdigit() or str(int(raw)) != raw:
            raise RollbackValidationError(
                f"owned source {field} must be a canonical unsigned integer"
            )
        numbers[field] = int(raw)
    try:
        source = SourceOwnershipIdentity(
            root_authority_id=item["root_authority_id"],
            root_path=item["root_path"],
            relative_path=item["relative_path"],
            device=numbers["device"],
            inode=numbers["inode"],
            ctime_ns=numbers["ctime_ns"],
            size_bytes=numbers["size_bytes"],
            sha256=item["sha256"],
            kind=item["kind"],
        )
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("owned source identity is invalid") from error
    return source


def _validate_observation(value: object) -> Mapping[str, Any]:
    item = _require_object(value, _OBSERVATION_KEYS, "evidence observation")
    if (
        item["schema_version"] != "bb.rl.phase5.g4-evidence-observation.v1"
        or item["kind"] not in _OBSERVATION_KINDS
        or type(item["graph_alias"]) is not str
        or not item["graph_alias"]
    ):
        raise RollbackValidationError("evidence observation identity is invalid")
    kind = item["kind"]
    evidence_id = item["evidence_id"]
    if kind != "active_status":
        _require_id(evidence_id, "evidence observation id")
    elif evidence_id is not None:
        raise RollbackValidationError(
            "active-status observation cannot carry evidence id"
        )
    expected_non_null = {
        "artifact": frozenset(("evidence_id",)),
        "rerun": frozenset(("evidence_id", "exit_code")),
        "identity": frozenset(("evidence_id", "observed_identity")),
        "active_status": frozenset(("observed_target_node_id",)),
    }[kind]
    nullable = (
        "evidence_id",
        "exit_code",
        "observed_identity",
        "observed_target_node_id",
    )
    for field in nullable:
        if (field in expected_non_null) != (item[field] is not None):
            raise RollbackValidationError(
                "evidence observation kind fields are incoherent"
            )
    if kind == "artifact":
        encoded = item["observed_bytes_base64"]
        if encoded is not None:
            if type(encoded) is not str:
                raise RollbackValidationError(
                    "artifact observation base64 must be exact"
                )
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise RollbackValidationError(
                    "artifact observation base64 is invalid"
                ) from error
            if base64.b64encode(decoded).decode("ascii") != encoded:
                raise RollbackValidationError(
                    "artifact observation base64 is not canonical"
                )
    elif item["observed_bytes_base64"] is not None:
        raise RollbackValidationError(
            "non-artifact observation cannot carry observed bytes"
        )
    if kind == "rerun" and type(item["exit_code"]) is not int:
        raise RollbackValidationError(
            "rerun observation exit code must be an exact integer"
        )
    if kind == "identity":
        identity = _require_object(
            item["observed_identity"],
            frozenset(
                (
                    "config_digest",
                    "model_digest",
                    "run_id",
                    "source_head",
                    "task_digest",
                    "threshold_digest",
                )
            ),
            "observed evidence identity",
        )
        _require_id(identity["run_id"], "observed evidence run id")
        for field in (
            "config_digest",
            "model_digest",
            "source_head",
            "task_digest",
            "threshold_digest",
        ):
            _require_digest(identity[field], f"observed evidence {field}")
    if kind == "active_status":
        _require_id(
            item["observed_target_node_id"],
            "active-status target node id",
        )
    return item


def _validate_f6_input_and_sources(
    authoring: Any,
    rerun_path: str,
    affected_episode_ids: Sequence[str],
    identity_projection: object,
    capsules: list[_PinnedImmutableSource],
) -> tuple[bytes, Any]:
    from scripts.rl_phase5.run_f6_restart_replay import F6RestartReplayInput

    identity_root = _require_object(
        identity_projection,
        frozenset(
            (
                "authority_bundle",
                "composition_descriptor",
                "composition_manifest",
                "original_request",
                "rerun_input",
                "secret_files",
            )
        ),
        "F6 rerun source identities",
    )
    secret_identity_items = _require_object(
        identity_root["secret_files"],
        frozenset(authoring.secret_files),
        "F6 secret source identities",
    )

    def binding(value: object, name: str) -> tuple[str, _ImmutableFileIdentity]:
        item = _require_object(
            value,
            frozenset(("identity", "sha256")),
            f"{name} binding",
        )
        return (
            _require_digest(item["sha256"], f"{name} digest"),
            _ImmutableFileIdentity.from_object(item["identity"], f"{name} identity"),
        )

    source_specs = (
        (
            "composition descriptor",
            authoring.composition_descriptor,
            binding(
                identity_root["composition_descriptor"],
                "composition descriptor",
            ),
        ),
        (
            "composition manifest",
            authoring.composition_manifest,
            binding(
                identity_root["composition_manifest"],
                "composition manifest",
            ),
        ),
        (
            "authority bundle",
            authoring.authority_bundle,
            binding(identity_root["authority_bundle"], "authority bundle"),
        ),
        (
            "original request",
            authoring.original_request,
            binding(identity_root["original_request"], "original request"),
        ),
        *(
            (
                f"secret file {handle_id}",
                source,
                binding(
                    secret_identity_items[handle_id],
                    f"secret file {handle_id}",
                ),
            )
            for handle_id, source in sorted(authoring.secret_files.items())
        ),
    )
    source_payloads: dict[str, bytes] = {}
    for source_name, source, (expected_digest, expected_identity) in source_specs:
        if expected_digest != source.sha256:
            raise RollbackValidationError(
                f"{source_name} request digest binding mismatch"
            )
        capsule = _PinnedImmutableSource.capture(
            source.path,
            source_name,
            expected_digest,
            expected_identity,
        )
        capsules.append(capsule)
        source_payloads[source_name] = capsule.raw

    rerun_digest, rerun_identity = binding(identity_root["rerun_input"], "rerun input")
    rerun_capsule = _PinnedImmutableSource.capture(
        rerun_path,
        "rerun input",
        rerun_digest,
        rerun_identity,
    )
    capsules.append(rerun_capsule)
    input_raw = rerun_capsule.raw
    try:
        input_model = F6RestartReplayInput.model_validate_json(input_raw, strict=True)
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("immutable F6 rerun input is invalid") from error
    if canonical_json_bytes(input_model.model_dump(mode="json")) != input_raw:
        raise RollbackValidationError("immutable F6 rerun input is not canonical")

    production = input_model.production
    input_secrets = {
        handle_id: {"path": source.path, "sha256": source.sha256}
        for handle_id, source in production.secret_files.items()
    }
    authoring_secrets = {
        handle_id: {"path": source.path, "sha256": source.sha256}
        for handle_id, source in authoring.secret_files.items()
    }
    input_secret_identities = {
        handle_id: source.identity.model_dump(mode="json")
        for handle_id, source in production.secret_files.items()
    }
    supplied_secret_identities = {
        handle_id: binding(
            secret_identity_items[handle_id],
            f"secret file {handle_id}",
        )[1].canonical_object()
        for handle_id in sorted(secret_identity_items)
    }
    original_projection = canonical_json_bytes(
        input_model.original_request.model_dump(mode="json")
    )
    if (
        source_payloads["original request"] != original_projection
        or input_model.original_request.episode_id not in affected_episode_ids
        or input_model.fresh_live_request.episode_id != authoring.fresh_episode_id
        or input_model.target != authoring.target
        or input_model.task_input != authoring.task_input
        or input_model.run_context != authoring.run_context
        or input_model.report_path != authoring.report_path
        or production.composition_ref_path != authoring.composition_descriptor.path
        or production.composition_descriptor_ref.digest
        != authoring.composition_descriptor.sha256
        or production.composition_manifest_ref.digest
        != authoring.composition_manifest.sha256
        or production.authority_bundle_ref.digest != authoring.authority_bundle.sha256
        or input_secrets != authoring_secrets
        or supplied_secret_identities != input_secret_identities
    ):
        raise RollbackValidationError("F6 rerun authoring source binding mismatch")
    return input_raw, input_model


def _validate_request_payload_with_capsules(
    raw: bytes,
    rollback_id: str,
    request_digest: str,
    source_capsules: list[_PinnedImmutableSource],
) -> Mapping[str, Any]:
    from breadboard.rl.phase5.f6_restart_replay_authoring import (
        F6RestartReplayAuthoringInput,
    )

    value = _require_object(
        _decode_canonical_payload(raw, "rollback request payload"),
        _REQUEST_KEYS,
        "rollback request payload",
    )
    if (
        value["schema_version"] != "bb.rl.phase5.g4-rollback-request.v1"
        or value["rollback_id"] != rollback_id
        or canonical_digest(raw) != request_digest
    ):
        raise RollbackValidationError(
            "rollback request payload identity or digest mismatch"
        )
    _require_int(
        value["frozen_active_generation"],
        "frozen active generation",
        minimum=1,
    )
    episodes = _require_tuple(value["affected_episode_ids"], "affected episode ids")
    if not episodes or len(set(episodes)) != len(episodes):
        raise RollbackValidationError(
            "affected episode ids must be nonempty and unique"
        )
    for episode_id in episodes:
        _require_id(episode_id, "affected episode id")
    _active_tuple_from_object(value["approved_tuple"])

    revocation = _require_object(
        value["revocation_publish_request"],
        frozenset(
            (
                "binding",
                "expected_epoch",
                "expected_generation",
                "operation_id",
                "scope_digest",
            )
        ),
        "revocation publish request",
    )
    if revocation["operation_id"] != f"{rollback_id}.revocation":
        raise RollbackValidationError(
            "revocation publication operation does not bind rollback"
        )
    _require_digest(revocation["scope_digest"], "revocation scope digest")
    binding = _require_object(
        revocation["binding"],
        frozenset(("epoch", "scope_digest", "state_digest")),
        "revocation binding",
    )
    _require_int(binding["epoch"], "revocation binding epoch")
    _require_digest(binding["scope_digest"], "revocation binding scope digest")
    _require_digest(binding["state_digest"], "revocation binding state digest")
    if binding["scope_digest"] != revocation["scope_digest"]:
        raise RollbackValidationError("revocation scope binding drifted")
    expected_generation = revocation["expected_generation"]
    expected_epoch = revocation["expected_epoch"]
    if (expected_generation is None) != (expected_epoch is None):
        raise RollbackValidationError("revocation expectations must be paired")
    if expected_generation is not None:
        _require_int(
            expected_generation,
            "expected revocation generation",
            minimum=1,
        )
        _require_int(expected_epoch, "expected revocation epoch")

    authoring = _validate_exact_model(
        value["rerun_authoring_input"],
        F6RestartReplayAuthoringInput,
        "F6 rerun authoring input",
    )
    if authoring.fresh_episode_id in episodes:
        raise RollbackValidationError(
            "fresh rerun episode id overlaps affected episode"
        )
    rerun_path = _validate_absolute_normalized_path(
        value["rerun_input_path"], "rerun input path"
    )
    source_paths = {
        authoring.composition_descriptor.path,
        authoring.composition_manifest.path,
        authoring.authority_bundle.path,
        authoring.original_request.path,
        authoring.report_path,
        *(source.path for source in authoring.secret_files.values()),
    }
    if rerun_path in source_paths:
        raise RollbackValidationError(
            "rerun input path must be exclusive from source/report paths"
        )
    _validate_f6_input_and_sources(
        authoring,
        rerun_path,
        episodes,
        value["rerun_source_identities"],
        source_capsules,
    )

    root_items = _require_tuple(value["dependent_root_refs"], "dependent root refs")
    if not root_items:
        raise RollbackValidationError("dependent root refs must be nonempty")
    roots = tuple(_immutable_ref_from_object(item) for item in root_items)
    if roots != tuple(sorted(roots, key=lambda item: item.identity_digest)) or len(
        {root.identity_digest for root in roots}
    ) != len(roots):
        raise RollbackValidationError(
            "dependent root refs must be identity-sorted and unique"
        )

    observations_by_field: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for field in ("evidence_invalidations", "failed_rerun_invalidations"):
        items = _require_tuple(value[field], field)
        observations = tuple(_validate_observation(item) for item in items)
        identities = tuple(
            (
                item["graph_alias"],
                item["kind"],
                item["evidence_id"],
                item["observed_target_node_id"],
            )
            for item in observations
        )
        if len(set(identities)) != len(identities):
            raise RollbackValidationError(f"{field} observations must be unique")
        observations_by_field[field] = observations
    for item in observations_by_field["failed_rerun_invalidations"]:
        if item["kind"] != "rerun" or item["exit_code"] == 0:
            raise RollbackValidationError(
                "failed rerun invalidations require nonzero rerun observations"
            )

    deletion = _require_object(
        value["source_deletion_plan"],
        frozenset(("operation_id", "owned_sources", "schema_version")),
        "source deletion plan",
    )
    if (
        deletion["schema_version"] != "bb.rl.phase5.g4-source-deletion-plan.v1"
        or deletion["operation_id"] != f"{rollback_id}.source-deletion"
    ):
        raise RollbackValidationError("source deletion plan identity is invalid")
    source_items = _require_tuple(
        deletion["owned_sources"], "source deletion owned sources"
    )
    if not source_items:
        raise RollbackValidationError("source deletion sources must be nonempty")
    sources = tuple(_source_identity_from_projection(item) for item in source_items)
    keys = tuple(source.key for source in sources)
    physical = tuple((source.device, source.inode) for source in sources)
    if (
        keys != tuple(sorted(keys))
        or len(set(keys)) != len(keys)
        or len(set(physical)) != len(physical)
    ):
        raise RollbackValidationError(
            "source deletion ownership must be sorted and unique"
        )
    return value


def _validate_request_payload(
    raw: bytes, rollback_id: str, request_digest: str
) -> Mapping[str, Any]:
    source_capsules: list[_PinnedImmutableSource] = []
    try:
        return _validate_request_payload_with_capsules(
            raw,
            rollback_id,
            request_digest,
            source_capsules,
        )
    finally:
        for capsule in reversed(source_capsules):
            capsule.close()


@dataclass(frozen=True, slots=True)
class ImmutableObjectRef:
    reference: str
    digest: str

    def __post_init__(self) -> None:
        if (
            type(self.reference) is not str
            or not self.reference
            or len(self.reference) > 4096
            or any(character.isspace() for character in self.reference)
        ):
            raise RollbackValidationError("immutable reference is invalid")
        _require_digest(self.digest, "immutable reference digest")

    def canonical_object(self) -> dict[str, str]:
        return {"digest": self.digest, "reference": self.reference}

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def identity_digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class ApprovedTupleRef:
    role: str
    object_ref: ImmutableObjectRef

    def __post_init__(self) -> None:
        _require_role(self.role)
        if type(self.object_ref) is not ImmutableObjectRef:
            raise RollbackValidationError("tuple object reference must be exact")

    def canonical_object(self) -> dict[str, Any]:
        return {"object_ref": self.object_ref.canonical_object(), "role": self.role}


@dataclass(frozen=True, slots=True)
class ActiveApprovedTuple:
    immutable_refs: tuple[ApprovedTupleRef, ...]
    tuple_digest: str
    schema_version: str = "bb.rl.phase5.active-approved-tuple.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "bb.rl.phase5.active-approved-tuple.v1":
            raise RollbackValidationError("active tuple schema is invalid")
        if type(self.immutable_refs) is not tuple or not self.immutable_refs:
            raise RollbackValidationError("active tuple requires immutable references")
        if any(type(item) is not ApprovedTupleRef for item in self.immutable_refs):
            raise RollbackValidationError("active tuple references must be exact")
        roles = tuple(item.role for item in self.immutable_refs)
        if roles != tuple(sorted(roles)) or len(set(roles)) != len(roles):
            raise RollbackValidationError(
                "active tuple roles must be unique and sorted"
            )
        _require_digest(self.tuple_digest, "active tuple digest")
        if self.tuple_digest != canonical_digest(
            canonical_json_bytes(
                {
                    "immutable_refs": [
                        item.canonical_object() for item in self.immutable_refs
                    ],
                    "schema_version": self.schema_version,
                }
            )
        ):
            raise RollbackValidationError("active tuple digest does not match its refs")

    @classmethod
    def from_refs(
        cls, immutable_refs: Sequence[ApprovedTupleRef]
    ) -> ActiveApprovedTuple:
        refs = tuple(immutable_refs)
        payload = {
            "immutable_refs": [item.canonical_object() for item in refs],
            "schema_version": "bb.rl.phase5.active-approved-tuple.v1",
        }
        return cls(refs, canonical_digest(canonical_json_bytes(payload)))

    def canonical_object(self) -> dict[str, Any]:
        return {
            "immutable_refs": [item.canonical_object() for item in self.immutable_refs],
            "schema_version": self.schema_version,
            "tuple_digest": self.tuple_digest,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())


class RollbackPhase(str, Enum):
    PREPARED = "prepared"
    EPISODES_CLOSED_OR_QUARANTINED = "episodes_closed_or_quarantined"
    REVOCATION_PUBLISHED = "revocation_published"
    DEPENDENTS_QUARANTINED = "dependents_quarantined"
    ACTIVE_TUPLE_RESTORED = "active_tuple_restored"
    RERUN_RECORDED = "rerun_recorded"
    SOURCE_DELETED = "source_deleted"
    COMPLETE = "complete"
    QUARANTINED = "quarantined"


_PHASE_ORDER = (
    RollbackPhase.PREPARED,
    RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    RollbackPhase.REVOCATION_PUBLISHED,
    RollbackPhase.DEPENDENTS_QUARANTINED,
    RollbackPhase.ACTIVE_TUPLE_RESTORED,
    RollbackPhase.RERUN_RECORDED,
    RollbackPhase.SOURCE_DELETED,
    RollbackPhase.COMPLETE,
)
_TERMINAL_PHASES = frozenset((RollbackPhase.COMPLETE, RollbackPhase.QUARANTINED))
_MAX_ROLLBACK_HISTORY_GENERATIONS = (
    len(_PHASE_ORDER) + 2 * _MAX_ROLLBACK_QUARANTINE_PAIRS
)
_MAX_ROLLBACK_HISTORY_BYTES = _MAX_ROLLBACK_HISTORY_GENERATIONS * _MAX_RECORD_BYTES

_PHASE_RECEIPT_KEYS = frozenset(
    (
        "body",
        "journal_generation",
        "journal_revision",
        "phase",
        "request_digest",
        "rollback_id",
        "schema_version",
    )
)
_PHASE_BODY_KEYS = {
    RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED: frozenset(
        ("episode_receipts", "reconcile_receipts")
    ),
    RollbackPhase.REVOCATION_PUBLISHED: frozenset(("revocation_receipt",)),
    RollbackPhase.DEPENDENTS_QUARANTINED: frozenset(
        ("dependent_quarantine_receipts", "evidence_invalidations")
    ),
    RollbackPhase.ACTIVE_TUPLE_RESTORED: frozenset(("active_tuple_state",)),
    RollbackPhase.RERUN_RECORDED: frozenset(("rerun_report",)),
    RollbackPhase.SOURCE_DELETED: frozenset(
        ("source_deletion_receipt", "source_deletion_request")
    ),
    RollbackPhase.COMPLETE: frozenset(("prior_phase_receipt_digests",)),
    RollbackPhase.QUARANTINED: frozenset(
        ("cleanup_receipts", "failed_phase", "leaf_errors")
    ),
}


def _validate_cleanup_receipt(value: object) -> None:
    from breadboard.rl.harness.materialization import (
        CleanupState,
        CleanupStepReceipt,
        SandboxCleanupReceipt,
    )

    item = _require_object(
        value,
        frozenset(("lease_id", "state", "steps")),
        "reconcile receipt",
    )
    _require_id(item["lease_id"], "reconcile lease id")
    steps: list[CleanupStepReceipt] = []
    for raw_step in _require_tuple(item["steps"], "reconcile cleanup steps"):
        step = _require_object(
            raw_step,
            frozenset(("detail", "resource", "state")),
            "reconcile cleanup step",
        )
        if (
            type(step["detail"]) is not str
            or type(step["resource"]) is not str
            or not step["resource"]
        ):
            raise RollbackValidationError("reconcile cleanup step is invalid")
        try:
            state = CleanupState(step["state"])
        except (TypeError, ValueError) as error:
            raise RollbackValidationError(
                "reconcile cleanup state is invalid"
            ) from error
        steps.append(CleanupStepReceipt(step["resource"], state, step["detail"]))
    expected = SandboxCleanupReceipt.from_steps(item["lease_id"], tuple(steps))
    if item["state"] != expected.state.value or expected.state is CleanupState.FAILED:
        raise RollbackValidationError("reconcile aggregate cleanup state is invalid")


def _validate_episode_receipts(
    body: Mapping[str, Any], request: Mapping[str, Any]
) -> None:
    from breadboard.rl.harness.contracts import ArtifactRef

    receipts = _require_tuple(body["episode_receipts"], "episode receipts")
    if [
        item.get("episode_id") if type(item) is dict else None for item in receipts
    ] != request["affected_episode_ids"]:
        raise RollbackValidationError(
            "episode receipts must bind affected episodes in request order"
        )
    for raw_receipt in receipts:
        receipt = _require_object(
            raw_receipt,
            frozenset(
                (
                    "cancellation_reason",
                    "cancellation_requested",
                    "cleanup_disposition",
                    "closed_envelope_ref",
                    "episode_id",
                    "terminal_state",
                    "transition_head_digest",
                    "transition_sequence",
                )
            ),
            "episode rollback receipt",
        )
        if (
            type(receipt["cancellation_reason"]) is not str
            or not receipt["cancellation_reason"]
        ):
            raise RollbackValidationError("episode cancellation reason is invalid")
        _require_bool(
            receipt["cancellation_requested"],
            "episode cancellation requested",
        )
        _require_digest(
            receipt["transition_head_digest"],
            "episode transition head digest",
        )
        _require_int(
            receipt["transition_sequence"],
            "episode transition sequence",
        )
        terminal = receipt["terminal_state"]
        disposition = receipt["cleanup_disposition"]
        closed_ref = receipt["closed_envelope_ref"]
        if terminal == "closed":
            if disposition != "released" or closed_ref is None:
                raise RollbackValidationError(
                    "closed episode receipt lacks released envelope"
                )
            _validate_exact_model(
                closed_ref, ArtifactRef, "closed episode envelope ref"
            )
        elif terminal == "quarantined":
            if disposition != "quarantined" or closed_ref is not None:
                raise RollbackValidationError(
                    "quarantined episode receipt is incoherent"
                )
        else:
            raise RollbackValidationError("episode terminal state is invalid")
    reconcile = _require_tuple(body["reconcile_receipts"], "reconcile receipts")
    lease_ids: list[str] = []
    for receipt in reconcile:
        _validate_cleanup_receipt(receipt)
        lease_ids.append(receipt["lease_id"])
    if len(set(lease_ids)) != len(lease_ids):
        raise RollbackValidationError("reconcile lease ids must be unique")


def _validate_evidence_invalidation(value: object, request: Mapping[str, Any]) -> None:
    from breadboard.rl.phase5.evidence_graph import EvidenceState

    item = _require_object(
        value,
        frozenset(
            (
                "affected_node_ids",
                "award_allowed",
                "effective_states",
                "graph_alias",
                "graph_root",
                "observation_digest",
                "promotion_allowed",
                "rejection_code",
                "root_node_id",
                "schema_version",
            )
        ),
        "evidence invalidation receipt",
    )
    if (
        item["schema_version"] != "bb.rl.phase5.g4-evidence-invalidation-receipt.v1"
        or item["award_allowed"] is not False
        or item["promotion_allowed"] is not False
        or type(item["graph_alias"]) is not str
        or not item["graph_alias"]
        or type(item["rejection_code"]) is not str
        or not item["rejection_code"]
    ):
        raise RollbackValidationError("evidence invalidation receipt flags are invalid")
    _require_digest(item["graph_root"], "evidence graph root")
    _require_id(item["root_node_id"], "evidence root node id")
    _require_digest(item["observation_digest"], "evidence observation digest")
    observations = (
        request["evidence_invalidations"] + request["failed_rerun_invalidations"]
    )
    matching = [
        observation
        for observation in observations
        if observation["graph_alias"] == item["graph_alias"]
        and canonical_digest(canonical_json_bytes(observation))
        == item["observation_digest"]
    ]
    if len(matching) != 1:
        raise RollbackValidationError(
            "evidence receipt does not bind one request observation"
        )
    affected = _require_tuple(item["affected_node_ids"], "affected evidence node ids")
    if (
        not affected
        or any(type(node) is not str or not node for node in affected)
        or affected != sorted(set(affected))
    ):
        raise RollbackValidationError(
            "affected evidence node ids must be sorted and unique"
        )
    effective = _require_tuple(item["effective_states"], "effective evidence states")
    pairs: list[tuple[str, str]] = []
    for raw_pair in effective:
        pair = _require_tuple(raw_pair, "effective evidence state pair")
        if len(pair) != 2 or type(pair[0]) is not str:
            raise RollbackValidationError("effective evidence state pair is invalid")
        try:
            EvidenceState(pair[1])
        except (TypeError, ValueError) as error:
            raise RollbackValidationError(
                "effective evidence state is invalid"
            ) from error
        pairs.append((pair[0], pair[1]))
    if pairs != sorted(set(pairs)) or not set(affected) <= {node for node, _ in pairs}:
        raise RollbackValidationError(
            "effective evidence states are incomplete or unordered"
        )


def _validate_revocation_receipt(value: object, request: Mapping[str, Any]) -> None:
    from breadboard.rl.phase5.revocation_publication import (
        RevocationSnapshotPublishReceipt,
    )

    receipt = _validate_exact_model(
        value,
        RevocationSnapshotPublishReceipt,
        "revocation publication receipt",
    )
    publish_request = request["revocation_publish_request"]
    expected_generation = publish_request["expected_generation"]
    if expected_generation is None:
        expected_generation = 0
    if (
        receipt.operation_id != publish_request["operation_id"]
        or receipt.request_digest
        != canonical_digest(canonical_json_bytes(publish_request))
        or receipt.generation != expected_generation + 1
    ):
        raise RollbackValidationError("revocation publication receipt binding mismatch")


def _validate_dependent_receipts(
    body: Mapping[str, Any],
    request: Mapping[str, Any],
    ref: RollbackPayloadRef,
) -> None:
    root_digests = {
        _immutable_ref_from_object(item).identity_digest
        for item in request["dependent_root_refs"]
    }
    receipts = _require_tuple(
        body["dependent_quarantine_receipts"],
        "dependent quarantine receipts",
    )
    if not receipts:
        raise RollbackValidationError("dependent quarantine receipts must be nonempty")
    object_digests: list[str] = []
    for raw_receipt in receipts:
        receipt = _quarantine_receipt_from_object(raw_receipt)
        if (
            receipt.rollback_id != ref.rollback_id
            or receipt.cause_digest != ref.request_digest
            or not set(receipt.causal_root_digests) <= root_digests
        ):
            raise RollbackValidationError(
                "dependent quarantine receipt binding mismatch"
            )
        object_digests.append(receipt.object_ref.identity_digest)
    if len(set(object_digests)) != len(object_digests):
        raise RollbackValidationError("dependent quarantine object refs must be unique")
    invalidations = _require_tuple(
        body["evidence_invalidations"], "evidence invalidation receipts"
    )
    expected_observations = len(request["evidence_invalidations"]) + len(
        request["failed_rerun_invalidations"]
    )
    if len(invalidations) != expected_observations:
        raise RollbackValidationError(
            "evidence invalidation receipt coverage is incomplete"
        )
    for invalidation in invalidations:
        _validate_evidence_invalidation(invalidation, request)


def _validate_active_tuple_receipt(
    value: object, request: Mapping[str, Any], rollback_id: str
) -> None:
    state = _active_state_from_object(value)
    if (
        state.approved_tuple.canonical_object() != request["approved_tuple"]
        or state.operation_id != f"{rollback_id}.active-tuple"
        or state.generation != request["frozen_active_generation"] + 1
        or state.previous_state_digest is None
    ):
        raise RollbackValidationError("active tuple rollback state binding mismatch")


def _validate_rerun_receipt(value: object, request: Mapping[str, Any]) -> None:
    from breadboard.rl.phase5.f6_restart_replay_authoring import (
        F6RestartReplayAuthoringInput,
    )
    from scripts.rl_phase5.run_f6_restart_replay import (
        F6RestartReplayReport,
    )

    report = _validate_exact_model(value, F6RestartReplayReport, "F6 rerun report")
    authoring_model = _validate_exact_model(
        request["rerun_authoring_input"],
        F6RestartReplayAuthoringInput,
        "F6 rerun authoring input",
    )
    receipt_capsules: list[_PinnedImmutableSource] = []
    try:
        input_raw, input_model = _validate_f6_input_and_sources(
            authoring_model,
            request["rerun_input_path"],
            request["affected_episode_ids"],
            request["rerun_source_identities"],
            receipt_capsules,
        )
    finally:
        for capsule in reversed(receipt_capsules):
            capsule.close()
    authoring = request["rerun_authoring_input"]
    input_production = input_model.production
    original_episode_id = input_model.original_request.episode_id
    original_request_digest = canonical_digest(
        canonical_json_bytes(input_model.original_request.model_dump(mode="json"))
    )
    input_secret_sources = {
        handle_id: {"path": source.path, "sha256": source.sha256}
        for handle_id, source in input_production.secret_files.items()
    }
    normalized_request = input_model.original_request.model_dump(mode="json")
    normalized_request["episode_id"] = "<episode-id>"
    immutable_digest = canonical_digest(
        canonical_json_bytes(
            {
                "immutable_identity": input_model.immutable_identity.model_dump(
                    mode="json"
                ),
                "request": normalized_request,
                "run_context": input_model.run_context,
                "schema_version": "bb.rl.phase5-f6-immutable-input.v1",
                "task_input": input_model.task_input,
            }
        )
    )
    if (
        report.input_digest != canonical_digest(input_raw)
        or report.immutable_input_digest != immutable_digest
        or report.immutable_identity != input_model.immutable_identity
        or report.target != input_model.target
        or report.original.episode_id != original_episode_id
        or report.cached.episode_id != original_episode_id
        or report.fresh_live.episode_id != input_model.fresh_live_request.episode_id
        or input_model.target.model_dump(mode="json") != authoring["target"]
        or input_model.task_input != authoring["task_input"]
        or input_model.run_context != authoring["run_context"]
        or input_model.report_path != authoring["report_path"]
        or report.fresh_live.episode_id != authoring["fresh_episode_id"]
        or input_model.fresh_live_request.episode_id != authoring["fresh_episode_id"]
        or input_model.original_request.episode_id
        not in request["affected_episode_ids"]
        or authoring["original_request"]["sha256"] != original_request_digest
        or input_production.composition_ref_path
        != authoring["composition_descriptor"]["path"]
        or input_production.composition_descriptor_ref.digest
        != authoring["composition_descriptor"]["sha256"]
        or input_production.composition_manifest_ref.digest
        != authoring["composition_manifest"]["sha256"]
        or input_production.authority_bundle_ref.digest
        != authoring["authority_bundle"]["sha256"]
        or input_secret_sources != authoring["secret_files"]
        or report.production.composition_descriptor_digest
        != authoring["composition_descriptor"]["sha256"]
        or report.production.composition_manifest_digest
        != authoring["composition_manifest"]["sha256"]
        or report.production.authority_bundle_digest
        != authoring["authority_bundle"]["sha256"]
        or report.production.composition_descriptor_digest
        != input_production.composition_descriptor_ref.digest
        or report.production.composition_manifest_digest
        != input_production.composition_manifest_ref.digest
        or report.production.authority_bundle_digest
        != input_production.authority_bundle_ref.digest
    ):
        raise RollbackValidationError("F6 rerun report binding mismatch")


def _source_deletion_request_from_projection(value: object) -> Any:
    from breadboard.rl.phase5.g4_source_deletion import (
        SourceDeletionGateReceipt,
        SourceDeletionGateReceipts,
        SourceDeletionRequest,
    )

    item = _require_object(
        value,
        frozenset(
            (
                "gates",
                "journal_request_digest",
                "operation_id",
                "owned_sources",
                "rollback_id",
                "schema_version",
            )
        ),
        "source deletion request",
    )
    if item["schema_version"] != "bb.rl.g4.source-deletion-request.v2":
        raise RollbackValidationError("source deletion request schema is invalid")
    gates_value = _require_object(
        item["gates"],
        frozenset(
            (
                "active_tuple_history_ref",
                "dependent_quarantine_refs",
                "episode_terminal_refs",
                "rerun_receipt_ref",
                "revocation_snapshot_ref",
            )
        ),
        "source deletion gates",
    )

    def gate_ref(raw: object) -> SourceDeletionGateReceipt:
        gate = _require_object(
            raw,
            frozenset(("path", "schema_version", "sha256")),
            "source deletion gate ref",
        )
        try:
            receipt = SourceDeletionGateReceipt(
                gate["path"], gate["sha256"], gate["schema_version"]
            )
        except (TypeError, ValueError) as error:
            raise RollbackValidationError(
                "source deletion gate ref is invalid"
            ) from error
        if receipt.projection() != gate:
            raise RollbackValidationError(
                "source deletion gate ref projection is not exact"
            )
        return receipt

    try:
        gates = SourceDeletionGateReceipts(
            tuple(
                gate_ref(raw)
                for raw in _require_tuple(
                    gates_value["episode_terminal_refs"],
                    "episode terminal gate refs",
                )
            ),
            gate_ref(gates_value["revocation_snapshot_ref"]),
            tuple(
                gate_ref(raw)
                for raw in _require_tuple(
                    gates_value["dependent_quarantine_refs"],
                    "dependent quarantine gate refs",
                )
            ),
            gate_ref(gates_value["active_tuple_history_ref"]),
            gate_ref(gates_value["rerun_receipt_ref"]),
        )
        request = SourceDeletionRequest(
            operation_id=item["operation_id"],
            rollback_id=item["rollback_id"],
            journal_request_digest=item["journal_request_digest"],
            owned_sources=tuple(
                _source_identity_from_projection(source)
                for source in _require_tuple(
                    item["owned_sources"], "source deletion request sources"
                )
            ),
            gates=gates,
        )
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("source deletion request is invalid") from error
    if request.projection() != item:
        raise RollbackValidationError("source deletion request projection is not exact")
    return request


def _source_deletion_receipt_from_projection(value: object) -> Any:
    from breadboard.rl.phase5.g4_source_deletion import (
        SourceAbsenceProof,
        SourceDeletionReceipt,
    )

    item = _require_object(
        value,
        frozenset(
            (
                "absence_proofs",
                "already_absent",
                "authority_signature",
                "completed_at",
                "completion_digest",
                "deleted",
                "operation_id",
                "request_digest",
                "schema_version",
            )
        ),
        "source deletion receipt",
    )
    if item["schema_version"] != "bb.rl.g4.source-deletion-receipt.v2":
        raise RollbackValidationError("source deletion receipt schema is invalid")
    proofs = []
    for raw in _require_tuple(item["absence_proofs"], "source absence proofs"):
        proof = _require_object(
            raw,
            frozenset(
                (
                    "absence_anchor_relative_path",
                    "anchor_device",
                    "anchor_inode",
                    "observed_at",
                    "prior_ctime_ns",
                    "prior_device",
                    "prior_inode",
                    "prior_kind",
                    "prior_sha256",
                    "prior_size_bytes",
                    "relative_path",
                    "root_authority_id",
                    "root_path",
                )
            ),
            "source absence proof",
        )
        numbers: dict[str, int] = {}
        for field in (
            "anchor_device",
            "anchor_inode",
            "prior_ctime_ns",
            "prior_device",
            "prior_inode",
            "prior_size_bytes",
        ):
            raw_number = proof[field]
            if (
                type(raw_number) is not str
                or not raw_number.isdigit()
                or str(int(raw_number)) != raw_number
            ):
                raise RollbackValidationError(
                    f"source absence proof {field} is not canonical"
                )
            numbers[field] = int(raw_number)
        try:
            parsed = SourceAbsenceProof(
                root_authority_id=proof["root_authority_id"],
                root_path=proof["root_path"],
                relative_path=proof["relative_path"],
                prior_device=numbers["prior_device"],
                prior_inode=numbers["prior_inode"],
                prior_ctime_ns=numbers["prior_ctime_ns"],
                prior_size_bytes=numbers["prior_size_bytes"],
                prior_sha256=proof["prior_sha256"],
                prior_kind=proof["prior_kind"],
                observed_at=proof["observed_at"],
                absence_anchor_relative_path=proof["absence_anchor_relative_path"],
                anchor_device=numbers["anchor_device"],
                anchor_inode=numbers["anchor_inode"],
            )
        except (TypeError, ValueError) as error:
            raise RollbackValidationError("source absence proof is invalid") from error
        if parsed.projection() != proof:
            raise RollbackValidationError(
                "source absence proof projection is not exact"
            )
        proofs.append(parsed)
    try:
        receipt = SourceDeletionReceipt(
            operation_id=item["operation_id"],
            request_digest=item["request_digest"],
            deleted=tuple(_require_tuple(item["deleted"], "deleted source keys")),
            already_absent=tuple(
                _require_tuple(item["already_absent"], "already absent source keys")
            ),
            absence_proofs=tuple(proofs),
            completed_at=item["completed_at"],
            completion_digest=item["completion_digest"],
            authority_signature=item["authority_signature"],
        )
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("source deletion receipt is invalid") from error
    if receipt.projection() != item:
        raise RollbackValidationError("source deletion receipt projection is not exact")
    return receipt


def _validate_source_deletion_body(
    body: Mapping[str, Any],
    request: Mapping[str, Any],
    ref: RollbackPayloadRef,
    prior_receipt_refs: tuple[RollbackPayloadRef, ...],
    store_root: Path,
) -> None:
    deletion_request = _source_deletion_request_from_projection(
        body["source_deletion_request"]
    )
    plan = request["source_deletion_plan"]
    if (
        deletion_request.operation_id != plan["operation_id"]
        or deletion_request.rollback_id != ref.rollback_id
        or deletion_request.journal_request_digest != ref.request_digest
        or deletion_request.projection()["owned_sources"] != plan["owned_sources"]
    ):
        raise RollbackValidationError(
            "source deletion request does not bind rollback plan"
        )
    by_phase: dict[RollbackPhase, list[RollbackPayloadRef]] = {}
    for prior_ref in prior_receipt_refs:
        by_phase.setdefault(prior_ref.phase, []).append(prior_ref)

    def gate_projection(payload_ref: RollbackPayloadRef) -> dict[str, str]:
        return {
            "path": str(store_root / payload_ref.relative_path),
            "schema_version": "bb.rl.g4.source-deletion-gate-ref.v2",
            "sha256": payload_ref.payload_digest,
        }

    expected_gates = {
        "active_tuple_history_ref": gate_projection(
            by_phase[RollbackPhase.ACTIVE_TUPLE_RESTORED][0]
        ),
        "dependent_quarantine_refs": [
            gate_projection(item)
            for item in by_phase[RollbackPhase.DEPENDENTS_QUARANTINED]
        ],
        "episode_terminal_refs": [
            gate_projection(item)
            for item in by_phase[RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED]
        ],
        "rerun_receipt_ref": gate_projection(by_phase[RollbackPhase.RERUN_RECORDED][0]),
        "revocation_snapshot_ref": gate_projection(
            by_phase[RollbackPhase.REVOCATION_PUBLISHED][0]
        ),
    }
    if deletion_request.projection()["gates"] != expected_gates:
        raise RollbackValidationError(
            "source deletion gates do not bind authoritative payload refs"
        )
    receipt = _source_deletion_receipt_from_projection(body["source_deletion_receipt"])
    source_keys = {source.key for source in deletion_request.owned_sources}
    proof_by_key = {proof.key: proof for proof in receipt.absence_proofs}
    if (
        receipt.operation_id != deletion_request.operation_id
        or receipt.request_digest != deletion_request.request_digest
        or set(receipt.deleted) | set(receipt.already_absent) != source_keys
        or set(proof_by_key) != source_keys
    ):
        raise RollbackValidationError(
            "source deletion receipt coverage or request binding mismatch"
        )
    source_by_key = {source.key: source for source in deletion_request.owned_sources}
    for key, proof in proof_by_key.items():
        source = source_by_key[key]
        if (
            proof.root_path != source.root_path
            or proof.relative_path != source.relative_path
            or proof.prior_device != source.device
            or proof.prior_inode != source.inode
            or proof.prior_ctime_ns != source.ctime_ns
            or proof.prior_size_bytes != source.size_bytes
            or proof.prior_sha256 != source.sha256
            or proof.prior_kind != source.kind
        ):
            raise RollbackValidationError(
                "source absence proof does not bind owned source identity"
            )


def _validate_receipt_payload(
    raw: bytes,
    *,
    ref: RollbackPayloadRef,
    leaf_errors: tuple[RollbackLeafError, ...],
    prior_receipt_digests: tuple[str, ...],
    prior_receipt_refs: tuple[RollbackPayloadRef, ...],
    request: Mapping[str, Any],
    store_root: Path,
) -> Mapping[str, Any]:
    value = _require_object(
        _decode_canonical_payload(raw, "rollback phase receipt payload"),
        _PHASE_RECEIPT_KEYS,
        "rollback phase receipt payload",
    )
    if (
        value["schema_version"] != "bb.rl.phase5.g4-phase-receipt.v1"
        or value["rollback_id"] != ref.rollback_id
        or value["request_digest"] != ref.request_digest
        or value["phase"] != ref.phase.value
        or value["journal_generation"] != ref.journal_generation
        or value["journal_revision"] != ref.journal_revision
        or canonical_digest(raw) != ref.payload_digest
    ):
        raise RollbackValidationError("rollback phase receipt payload binding mismatch")
    body = _require_object(
        value["body"],
        _PHASE_BODY_KEYS[ref.phase],
        "rollback phase receipt body",
    )
    if ref.phase is not RollbackPhase.QUARANTINED and leaf_errors:
        raise RollbackValidationError(
            "non-quarantine receipt cannot carry journal leaf errors"
        )
    if ref.phase is RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED:
        _validate_episode_receipts(body, request)
    elif ref.phase is RollbackPhase.REVOCATION_PUBLISHED:
        _validate_revocation_receipt(body["revocation_receipt"], request)
    elif ref.phase is RollbackPhase.DEPENDENTS_QUARANTINED:
        _validate_dependent_receipts(body, request, ref)
    elif ref.phase is RollbackPhase.ACTIVE_TUPLE_RESTORED:
        _validate_active_tuple_receipt(
            body["active_tuple_state"], request, ref.rollback_id
        )
    elif ref.phase is RollbackPhase.RERUN_RECORDED:
        _validate_rerun_receipt(body["rerun_report"], request)
    elif ref.phase is RollbackPhase.SOURCE_DELETED:
        _validate_source_deletion_body(
            body,
            request,
            ref,
            prior_receipt_refs,
            store_root,
        )
    elif ref.phase is RollbackPhase.COMPLETE:
        digests = _require_tuple(
            body["prior_phase_receipt_digests"],
            "complete prior phase receipt digests",
        )
        required_phases = _PHASE_ORDER[1:-1]
        if (
            tuple(digests) != prior_receipt_digests
            or len(digests) != len(required_phases)
            or tuple(item.phase for item in prior_receipt_refs) != required_phases
        ):
            raise RollbackValidationError(
                "complete receipt does not bind exactly six prior phases"
            )
        for digest in digests:
            _require_digest(digest, "complete prior phase receipt digest")
    else:
        if not leaf_errors or body["leaf_errors"] != [
            error.canonical_object() for error in leaf_errors
        ]:
            raise RollbackValidationError(
                "quarantine receipt leaf errors do not match journal"
            )
        try:
            failed_phase = RollbackPhase(body["failed_phase"])
        except (TypeError, ValueError) as error:
            raise RollbackValidationError(
                "quarantine failed phase is invalid"
            ) from error
        prior_phase = (
            RollbackPhase.PREPARED
            if not prior_receipt_refs
            else prior_receipt_refs[-1].phase
        )
        if prior_phase in _TERMINAL_PHASES:
            raise RollbackValidationError("quarantine cannot follow a terminal phase")
        expected_failed = _PHASE_ORDER[_PHASE_ORDER.index(prior_phase) + 1]
        if failed_phase is not expected_failed or failed_phase in _TERMINAL_PHASES:
            raise RollbackValidationError(
                "quarantine failed phase does not match attempted phase"
            )
        cleanup_receipts = _require_tuple(
            body["cleanup_receipts"], "quarantine cleanup receipts"
        )
        canonical_cleanups = tuple(
            canonical_json_bytes(item) for item in cleanup_receipts
        )
        if len(set(canonical_cleanups)) != len(canonical_cleanups):
            raise RollbackValidationError("quarantine cleanup receipts must be unique")
        for cleanup in cleanup_receipts:
            if type(cleanup) is not dict:
                raise RollbackValidationError(
                    "quarantine cleanup receipt must be typed"
                )
            if set(cleanup) == {"lease_id", "state", "steps"}:
                _validate_cleanup_receipt(cleanup)
            else:
                _validate_evidence_invalidation(cleanup, request)
    return value


class RollbackPayloadKind(str, Enum):
    REQUEST = "request"
    PHASE_RECEIPT = "phase_receipt"


def _payload_relative_path(
    rollback_id: str,
    kind: RollbackPayloadKind,
    phase: RollbackPhase,
    generation: int,
    revision: int,
    payload_digest: str,
) -> str:
    return (
        f"payload.{rollback_id}.g{generation:020d}.r{revision:020d}."
        f"{phase.value}.{kind.value}.{payload_digest[7:]}.json"
    )


@dataclass(frozen=True, slots=True)
class RollbackPayloadRef:
    rollback_id: str
    request_digest: str
    payload_digest: str
    kind: RollbackPayloadKind
    phase: RollbackPhase
    journal_generation: int
    journal_revision: int
    relative_path: str
    schema_version: str = "bb.rl.phase5.rollback-payload-ref.v1"

    def __post_init__(self) -> None:
        _require_id(self.rollback_id, "rollback payload rollback id")
        _require_digest(self.request_digest, "rollback payload request digest")
        _require_digest(self.payload_digest, "rollback payload digest")
        if type(self.kind) is not RollbackPayloadKind:
            raise RollbackValidationError("rollback payload kind must be exact")
        if type(self.phase) is not RollbackPhase:
            raise RollbackValidationError("rollback payload phase must be exact")
        _require_int(
            self.journal_generation,
            "rollback payload journal generation",
            minimum=1,
        )
        _require_int(self.journal_revision, "rollback payload journal revision")
        lineage_offset = self.journal_generation - self.journal_revision - 1
        if (
            lineage_offset < 0
            or lineage_offset % 2 != 0
            or lineage_offset > 2 * _MAX_ROLLBACK_QUARANTINE_PAIRS
        ):
            raise RollbackValidationError(
                "rollback payload generation/revision lineage is incoherent"
            )
        if self.kind is RollbackPayloadKind.REQUEST and (
            self.phase is not RollbackPhase.PREPARED
            or self.journal_generation != 1
            or self.journal_revision != 0
            or self.payload_digest != self.request_digest
        ):
            raise RollbackValidationError("rollback request payload ref is incoherent")
        if self.kind is RollbackPayloadKind.PHASE_RECEIPT and (
            self.phase is RollbackPhase.PREPARED
            or self.journal_generation < 2
            or self.journal_revision < 1
        ):
            raise RollbackValidationError("rollback receipt payload ref is incoherent")
        expected_path = _payload_relative_path(
            self.rollback_id,
            self.kind,
            self.phase,
            self.journal_generation,
            self.journal_revision,
            self.payload_digest,
        )
        if self.relative_path != expected_path:
            raise RollbackValidationError(
                "rollback payload authoritative path mismatch"
            )
        if self.schema_version != "bb.rl.phase5.rollback-payload-ref.v1":
            raise RollbackValidationError("rollback payload ref schema is invalid")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "journal_generation": self.journal_generation,
            "journal_revision": self.journal_revision,
            "kind": self.kind.value,
            "payload_digest": self.payload_digest,
            "phase": self.phase.value,
            "relative_path": self.relative_path,
            "request_digest": self.request_digest,
            "rollback_id": self.rollback_id,
            "schema_version": self.schema_version,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class RollbackLeafError:
    adapter: str
    object_ref: str
    error_code: str
    error_digest: str

    def __post_init__(self) -> None:
        _require_id(self.adapter, "leaf error adapter")
        if (
            type(self.object_ref) is not str
            or not self.object_ref
            or len(self.object_ref) > 4096
        ):
            raise RollbackValidationError("leaf error object reference is invalid")
        _require_id(self.error_code, "leaf error code")
        _require_digest(self.error_digest, "leaf error digest")

    def canonical_object(self) -> dict[str, str]:
        return {
            "adapter": self.adapter,
            "error_code": self.error_code,
            "error_digest": self.error_digest,
            "object_ref": self.object_ref,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class RollbackPhaseReceipt:
    phase: RollbackPhase
    receipt_digests: tuple[str, ...]
    receipt_refs: tuple[RollbackPayloadRef, ...]
    leaf_errors: tuple[RollbackLeafError, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.phase) is not RollbackPhase
            or self.phase is RollbackPhase.PREPARED
        ):
            raise RollbackValidationError("phase receipt has an invalid phase")
        if type(self.receipt_digests) is not tuple or not self.receipt_digests:
            raise RollbackValidationError(
                "phase receipt requires exact receipt digests"
            )
        for digest in self.receipt_digests:
            _require_digest(digest, "phase receipt digest")
        if len(set(self.receipt_digests)) != len(self.receipt_digests):
            raise RollbackValidationError("phase receipt digests must be unique")
        if (
            type(self.receipt_refs) is not tuple
            or len(self.receipt_refs) != len(self.receipt_digests)
            or any(type(ref) is not RollbackPayloadRef for ref in self.receipt_refs)
        ):
            raise RollbackValidationError(
                "phase receipt requires one exact authoritative ref per digest"
            )
        if any(
            ref.kind is not RollbackPayloadKind.PHASE_RECEIPT
            or ref.phase is not self.phase
            or ref.payload_digest != digest
            for ref, digest in zip(self.receipt_refs, self.receipt_digests, strict=True)
        ):
            raise RollbackValidationError(
                "phase receipt authoritative refs do not match digests"
            )
        if type(self.leaf_errors) is not tuple or any(
            type(error) is not RollbackLeafError for error in self.leaf_errors
        ):
            raise RollbackValidationError("phase leaf errors must be exact")
        if len({error.error_digest for error in self.leaf_errors}) != len(
            self.leaf_errors
        ):
            raise RollbackValidationError("phase leaf errors must be unique")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "leaf_errors": [error.canonical_object() for error in self.leaf_errors],
            "phase": self.phase.value,
            "receipt_digests": list(self.receipt_digests),
            "receipt_refs": [ref.canonical_object() for ref in self.receipt_refs],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class RollbackTerminalQuarantineRef:
    transaction_id: str
    rollback_id: str
    predecessor_generation: int
    predecessor_record_digest: str
    successor_generation: int
    successor_record_digest: str
    successor_raw_digest: str
    successor_name: str
    tombstone_name: str
    tombstone_raw_digest: str
    schema_version: str = "bb.rl.phase5.rollback-terminal-quarantine-ref.v1"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", self.transaction_id):
            raise RollbackValidationError(
                "terminal quarantine transaction id is invalid"
            )
        _require_id(self.rollback_id, "terminal quarantine rollback id")
        _require_int(
            self.predecessor_generation,
            "terminal quarantine predecessor generation",
            minimum=1,
        )
        _require_int(
            self.successor_generation,
            "terminal quarantine successor generation",
            minimum=2,
        )
        if self.successor_generation != self.predecessor_generation + 1:
            raise RollbackValidationError(
                "terminal quarantine generations are not adjacent"
            )
        for value, name in (
            (self.predecessor_record_digest, "predecessor record digest"),
            (self.successor_record_digest, "successor record digest"),
            (self.successor_raw_digest, "successor raw digest"),
            (self.tombstone_raw_digest, "tombstone raw digest"),
        ):
            _require_digest(value, f"terminal quarantine {name}")
        expected_successor, expected_tombstone = (
            _PinnedSignedDirectory._rollback_quarantine_names(
                self.transaction_id,
                self.rollback_id,
                self.successor_record_digest,
            )
        )
        if (
            self.successor_name != expected_successor
            or self.tombstone_name != expected_tombstone
        ):
            raise RollbackValidationError(
                "terminal quarantine artifact names are invalid"
            )
        if self.schema_version != "bb.rl.phase5.rollback-terminal-quarantine-ref.v1":
            raise RollbackValidationError("terminal quarantine ref schema is invalid")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "predecessor_generation": self.predecessor_generation,
            "predecessor_record_digest": self.predecessor_record_digest,
            "rollback_id": self.rollback_id,
            "schema_version": self.schema_version,
            "successor_generation": self.successor_generation,
            "successor_name": self.successor_name,
            "successor_raw_digest": self.successor_raw_digest,
            "successor_record_digest": self.successor_record_digest,
            "tombstone_name": self.tombstone_name,
            "tombstone_raw_digest": self.tombstone_raw_digest,
            "transaction_id": self.transaction_id,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class RollbackJournalRecord:
    rollback_id: str
    request_digest: str
    request_payload_ref: RollbackPayloadRef
    generation: int
    revision: int
    phase: RollbackPhase
    phase_receipts: tuple[RollbackPhaseReceipt, ...]
    previous_record_digest: str | None
    terminal_quarantine_refs: tuple[RollbackTerminalQuarantineRef, ...] = ()
    schema_version: str = "bb.rl.phase5.rollback-journal.v3"

    def __post_init__(self) -> None:
        _require_id(self.rollback_id, "rollback id")
        _require_digest(self.request_digest, "rollback request digest")
        if (
            type(self.request_payload_ref) is not RollbackPayloadRef
            or self.request_payload_ref.kind is not RollbackPayloadKind.REQUEST
            or self.request_payload_ref.rollback_id != self.rollback_id
            or self.request_payload_ref.request_digest != self.request_digest
        ):
            raise RollbackValidationError(
                "rollback journal request payload ref mismatch"
            )
        _require_int(self.generation, "journal generation", minimum=1)
        _require_int(self.revision, "journal revision")
        if type(self.phase) is not RollbackPhase:
            raise RollbackValidationError("journal phase must be exact")
        if type(self.phase_receipts) is not tuple or any(
            type(item) is not RollbackPhaseReceipt for item in self.phase_receipts
        ):
            raise RollbackValidationError("journal phase receipts must be exact")
        if self.revision != len(self.phase_receipts):
            raise RollbackValidationError("journal revision must match receipt count")
        if type(self.terminal_quarantine_refs) is not tuple or any(
            type(item) is not RollbackTerminalQuarantineRef
            for item in self.terminal_quarantine_refs
        ):
            raise RollbackValidationError(
                "journal terminal quarantine refs must be exact"
            )
        if any(
            item.rollback_id != self.rollback_id
            for item in self.terminal_quarantine_refs
        ):
            raise RollbackValidationError(
                "journal terminal quarantine rollback binding mismatch"
            )
        if len({item.transaction_id for item in self.terminal_quarantine_refs}) != len(
            self.terminal_quarantine_refs
        ):
            raise RollbackValidationError(
                "journal terminal quarantine refs must be unique"
            )
        if self.generation != (
            self.revision + 1 + 2 * len(self.terminal_quarantine_refs)
        ):
            raise RollbackValidationError(
                "journal generation/revision lineage is incoherent"
            )
        if len(self.terminal_quarantine_refs) > _MAX_ROLLBACK_QUARANTINE_PAIRS:
            raise RollbackValidationError(
                "journal terminal quarantine ref count exceeds fixed bound"
            )
        successor_generations = tuple(
            item.successor_generation for item in self.terminal_quarantine_refs
        )
        if (
            successor_generations != tuple(sorted(successor_generations))
            or len(set(successor_generations)) != len(successor_generations)
            or any(
                generation >= self.generation for generation in successor_generations
            )
        ):
            raise RollbackValidationError(
                "journal terminal quarantine chronology is invalid"
            )
        previous_receipt_generation = 1
        for index, receipt in enumerate(self.phase_receipts):
            expected_revision = index + 1
            receipt_generations = {
                ref.journal_generation for ref in receipt.receipt_refs
            }
            if (
                len(receipt_generations) != 1
                or next(iter(receipt_generations)) <= previous_receipt_generation
                or next(iter(receipt_generations)) > self.generation
                or any(
                    ref.rollback_id != self.rollback_id
                    or ref.request_digest != self.request_digest
                    or ref.journal_revision != expected_revision
                    for ref in receipt.receipt_refs
                )
            ):
                raise RollbackValidationError(
                    "journal receipt authoritative ref binding mismatch"
                )
            previous_receipt_generation = next(iter(receipt_generations))
            receipt_generation = next(iter(receipt_generations))
            restoration_count = sum(
                1
                for ref in self.terminal_quarantine_refs
                if ref.successor_generation < receipt_generation
            )
            if receipt_generation != (expected_revision + 1 + 2 * restoration_count):
                raise RollbackValidationError(
                    "journal receipt generation lineage is incoherent"
                )
            is_last = index == len(self.phase_receipts) - 1
            if receipt.phase is RollbackPhase.QUARANTINED:
                if not is_last:
                    raise RollbackValidationError(
                        "terminal quarantine must be the final journal receipt"
                    )
            elif receipt.phase is not _PHASE_ORDER[index + 1]:
                raise RollbackValidationError(
                    "journal phase receipt sequence is not monotonic"
                )
        all_receipt_digests = tuple(
            digest
            for receipt in self.phase_receipts
            for digest in receipt.receipt_digests
        )
        if len(set(all_receipt_digests)) != len(all_receipt_digests):
            raise RollbackValidationError(
                "journal receipt payload digests must be globally unique"
            )
        if self.generation == 1:
            if (
                self.phase is not RollbackPhase.PREPARED
                or self.phase_receipts
                or self.previous_record_digest is not None
                or self.terminal_quarantine_refs
            ):
                raise RollbackValidationError("initial journal record is invalid")
        else:
            _require_digest(
                self.previous_record_digest,
                "previous journal record digest",
            )
            if self.phase is RollbackPhase.PREPARED:
                if self.phase_receipts or not self.terminal_quarantine_refs:
                    raise RollbackValidationError(
                        "restored prepared journal is invalid"
                    )
            elif (
                not self.phase_receipts
                or self.phase_receipts[-1].phase is not self.phase
            ):
                raise RollbackValidationError(
                    "journal phase must match its last receipt"
                )
        if self.schema_version != "bb.rl.phase5.rollback-journal.v3":
            raise RollbackValidationError("rollback journal schema is invalid")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "phase": self.phase.value,
            "phase_receipts": [item.canonical_object() for item in self.phase_receipts],
            "previous_record_digest": self.previous_record_digest,
            "request_digest": self.request_digest,
            "request_payload_ref": self.request_payload_ref.canonical_object(),
            "revision": self.revision,
            "rollback_id": self.rollback_id,
            "terminal_quarantine_refs": [
                item.canonical_object() for item in self.terminal_quarantine_refs
            ],
            "schema_version": self.schema_version,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class ActiveApprovedTupleState:
    generation: int
    approved_tuple: ActiveApprovedTuple
    operation_id: str
    previous_state_digest: str | None
    schema_version: str = "bb.rl.phase5.active-approved-tuple-state.v1"

    def __post_init__(self) -> None:
        _require_int(self.generation, "active tuple generation", minimum=1)
        if type(self.approved_tuple) is not ActiveApprovedTuple:
            raise RollbackValidationError(
                "approved tuple state requires an exact tuple"
            )
        _require_id(self.operation_id, "active tuple operation id")
        if self.generation == 1:
            if self.previous_state_digest is not None:
                raise RollbackValidationError(
                    "initial active tuple cannot have a predecessor"
                )
        else:
            _require_digest(
                self.previous_state_digest, "previous active tuple state digest"
            )
        if self.schema_version != "bb.rl.phase5.active-approved-tuple-state.v1":
            raise RollbackValidationError("active tuple state schema is invalid")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "approved_tuple": self.approved_tuple.canonical_object(),
            "generation": self.generation,
            "operation_id": self.operation_id,
            "previous_state_digest": self.previous_state_digest,
            "schema_version": self.schema_version,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class ActiveApprovedTupleHistoryEntry:
    state: ActiveApprovedTupleState
    state_digest: str

    def __post_init__(self) -> None:
        if type(self.state) is not ActiveApprovedTupleState:
            raise RollbackValidationError("history entry state must be exact")
        _require_digest(self.state_digest, "active tuple history digest")
        if self.state_digest != self.state.digest:
            raise RollbackValidationError("active tuple history digest mismatch")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "state": self.state.canonical_object(),
            "state_digest": self.state_digest,
        }


class DependentObjectKind(str, Enum):
    REWARD = "reward"
    CHECKPOINT = "checkpoint"
    EVIDENCE = "evidence"


@dataclass(frozen=True, slots=True)
class DependentOwnership:
    registration_id: str
    approved_tuple_digest: str
    episode_id: str
    run_id: str
    object_kind: DependentObjectKind
    object_ref: ImmutableObjectRef
    parent_refs: tuple[ImmutableObjectRef, ...] = ()
    schema_version: str = "bb.rl.phase5.dependent-ownership.v1"

    def __post_init__(self) -> None:
        _require_id(self.registration_id, "dependent registration id")
        _require_digest(self.approved_tuple_digest, "dependent approved tuple digest")
        _require_id(self.episode_id, "dependent episode id")
        _require_id(self.run_id, "dependent run id")
        if type(self.object_kind) is not DependentObjectKind:
            raise RollbackValidationError("dependent object kind must be exact")
        if type(self.object_ref) is not ImmutableObjectRef:
            raise RollbackValidationError("dependent object ref must be exact")
        if type(self.parent_refs) is not tuple or any(
            type(item) is not ImmutableObjectRef for item in self.parent_refs
        ):
            raise RollbackValidationError("dependent parents must be exact")
        identities = tuple(item.identity_digest for item in self.parent_refs)
        if identities != tuple(sorted(identities)) or len(set(identities)) != len(
            identities
        ):
            raise RollbackValidationError("dependent parents must be unique and sorted")
        if self.object_ref.identity_digest in identities:
            raise RollbackValidationError("dependent object cannot own itself")
        if self.schema_version != "bb.rl.phase5.dependent-ownership.v1":
            raise RollbackValidationError("dependent ownership schema is invalid")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "approved_tuple_digest": self.approved_tuple_digest,
            "episode_id": self.episode_id,
            "object_kind": self.object_kind.value,
            "object_ref": self.object_ref.canonical_object(),
            "parent_refs": [item.canonical_object() for item in self.parent_refs],
            "registration_id": self.registration_id,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(canonical_json_bytes(self.canonical_object()))


@dataclass(frozen=True, slots=True)
class DependentQuarantineReceipt:
    rollback_id: str
    cause_digest: str
    object_ref: ImmutableObjectRef
    ownership_digest: str
    causal_root_digests: tuple[str, ...]
    generation: int
    schema_version: str = "bb.rl.phase5.dependent-quarantine-receipt.v1"

    def __post_init__(self) -> None:
        _require_id(self.rollback_id, "dependent quarantine rollback id")
        _require_digest(self.cause_digest, "dependent quarantine cause digest")
        if type(self.object_ref) is not ImmutableObjectRef:
            raise RollbackValidationError(
                "dependent quarantine object ref must be exact"
            )
        _require_digest(self.ownership_digest, "dependent ownership digest")
        if type(self.causal_root_digests) is not tuple or not self.causal_root_digests:
            raise RollbackValidationError("dependent quarantine requires causal roots")
        for digest in self.causal_root_digests:
            _require_digest(digest, "dependent quarantine causal root digest")
        if self.causal_root_digests != tuple(sorted(set(self.causal_root_digests))):
            raise RollbackValidationError(
                "dependent causal roots must be unique and sorted"
            )
        _require_int(self.generation, "dependent quarantine generation", minimum=2)
        if self.schema_version != "bb.rl.phase5.dependent-quarantine-receipt.v1":
            raise RollbackValidationError(
                "dependent quarantine receipt schema is invalid"
            )

    def canonical_object(self) -> dict[str, Any]:
        return {
            "causal_root_digests": list(self.causal_root_digests),
            "cause_digest": self.cause_digest,
            "generation": self.generation,
            "object_ref": self.object_ref.canonical_object(),
            "ownership_digest": self.ownership_digest,
            "rollback_id": self.rollback_id,
            "schema_version": self.schema_version,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(canonical_json_bytes(self.canonical_object()))


@dataclass(frozen=True, slots=True)
class DependentOwnershipRecord:
    generation: int
    ownership: DependentOwnership
    promotion_eligible: bool
    export_eligible: bool
    quarantine_receipts: tuple[DependentQuarantineReceipt, ...]
    previous_record_digest: str | None
    schema_version: str = "bb.rl.phase5.dependent-ownership-record.v1"

    def __post_init__(self) -> None:
        _require_int(self.generation, "dependent generation", minimum=1)
        if type(self.ownership) is not DependentOwnership:
            raise RollbackValidationError("dependent ownership record must be exact")
        _require_bool(self.promotion_eligible, "promotion eligibility")
        _require_bool(self.export_eligible, "export eligibility")
        if self.promotion_eligible != self.export_eligible:
            raise RollbackValidationError(
                "promotion/export eligibility must fail closed together"
            )
        if type(self.quarantine_receipts) is not tuple or any(
            type(item) is not DependentQuarantineReceipt
            for item in self.quarantine_receipts
        ):
            raise RollbackValidationError("dependent quarantine receipts must be exact")
        if self.generation != len(self.quarantine_receipts) + 1:
            raise RollbackValidationError(
                "dependent generation must match quarantine history"
            )
        if self.generation == 1:
            if (
                not self.promotion_eligible
                or self.quarantine_receipts
                or self.previous_record_digest is not None
            ):
                raise RollbackValidationError("new dependent must begin eligible")
        else:
            if self.promotion_eligible or not self.quarantine_receipts:
                raise RollbackValidationError(
                    "quarantined dependent cannot be eligible"
                )
            _require_digest(
                self.previous_record_digest, "previous dependent record digest"
            )
        event_keys = tuple(
            (item.rollback_id, item.cause_digest) for item in self.quarantine_receipts
        )
        if len(set(event_keys)) != len(event_keys):
            raise RollbackValidationError("dependent quarantine event must be unique")
        if any(
            item.object_ref != self.ownership.object_ref
            or item.ownership_digest != self.ownership.digest
            for item in self.quarantine_receipts
        ):
            raise RollbackValidationError(
                "dependent quarantine receipt ownership mismatch"
            )
        if self.schema_version != "bb.rl.phase5.dependent-ownership-record.v1":
            raise RollbackValidationError(
                "dependent ownership record schema is invalid"
            )

    def canonical_object(self) -> dict[str, Any]:
        return {
            "export_eligible": self.export_eligible,
            "generation": self.generation,
            "ownership": self.ownership.canonical_object(),
            "previous_record_digest": self.previous_record_digest,
            "promotion_eligible": self.promotion_eligible,
            "quarantine_receipts": [
                item.canonical_object() for item in self.quarantine_receipts
            ],
            "schema_version": self.schema_version,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


class RollbackJournalStore(Protocol):
    def prepare(
        self, rollback_id: str, request_digest: str, request_payload: bytes
    ) -> RollbackJournalRecord: ...

    def get(self, rollback_id: str) -> RollbackJournalRecord | None: ...
    def get_request(self, rollback_id: str) -> bytes: ...
    def get_request_ref(self, rollback_id: str) -> RollbackPayloadRef: ...

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
    ) -> RollbackJournalRecord: ...

    def get_receipt_payload(self, rollback_id: str, receipt_digest: str) -> bytes: ...
    def get_receipt_ref(
        self, rollback_id: str, receipt_digest: str
    ) -> RollbackPayloadRef: ...

    def history(self, rollback_id: str) -> tuple[RollbackJournalRecord, ...]: ...


class ActiveApprovedTupleStore(Protocol):
    def get(self) -> ActiveApprovedTupleState | None: ...

    def compare_and_swap(
        self,
        expected_generation: int | None,
        approved_tuple: ActiveApprovedTuple,
        operation_id: str,
    ) -> ActiveApprovedTupleState: ...

    def history(self) -> tuple[ActiveApprovedTupleHistoryEntry, ...]: ...


class DependentQuarantineStore(Protocol):
    def register(self, ownership: DependentOwnership) -> DependentOwnershipRecord: ...

    def get(
        self, object_ref: ImmutableObjectRef
    ) -> DependentOwnershipRecord | None: ...

    def quarantine_causal(
        self,
        rollback_id: str,
        cause_digest: str,
        root_refs: tuple[ImmutableObjectRef, ...],
    ) -> tuple[DependentQuarantineReceipt, ...]: ...

    def list_owned(
        self,
        *,
        approved_tuple_digest: str | None = None,
        episode_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[DependentOwnershipRecord, ...]: ...

    def assert_promotion_eligible(self, object_ref: ImmutableObjectRef) -> None: ...

    def assert_export_eligible(self, object_ref: ImmutableObjectRef) -> None: ...
    def read_fence(self) -> Iterator[tuple[DependentOwnershipRecord, ...]]: ...



def _immutable_ref_from_object(value: object) -> ImmutableObjectRef:
    item = _require_object(
        value, frozenset(("digest", "reference")), "immutable object reference"
    )
    return ImmutableObjectRef(item["reference"], item["digest"])


def _approved_tuple_ref_from_object(value: object) -> ApprovedTupleRef:
    item = _require_object(value, frozenset(("object_ref", "role")), "tuple reference")
    return ApprovedTupleRef(
        item["role"], _immutable_ref_from_object(item["object_ref"])
    )


def _active_tuple_from_object(value: object) -> ActiveApprovedTuple:
    item = _require_object(
        value,
        frozenset(("immutable_refs", "schema_version", "tuple_digest")),
        "active approved tuple",
    )
    return ActiveApprovedTuple(
        tuple(
            _approved_tuple_ref_from_object(ref)
            for ref in _require_tuple(item["immutable_refs"], "active tuple refs")
        ),
        item["tuple_digest"],
        item["schema_version"],
    )


def _payload_ref_from_object(value: object) -> RollbackPayloadRef:
    item = _require_object(
        value,
        frozenset(
            (
                "journal_generation",
                "journal_revision",
                "kind",
                "payload_digest",
                "phase",
                "relative_path",
                "request_digest",
                "rollback_id",
                "schema_version",
            )
        ),
        "rollback payload ref",
    )
    try:
        kind = RollbackPayloadKind(item["kind"])
        phase = RollbackPhase(item["phase"])
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("rollback payload ref enum is invalid") from error
    return RollbackPayloadRef(
        item["rollback_id"],
        item["request_digest"],
        item["payload_digest"],
        kind,
        phase,
        item["journal_generation"],
        item["journal_revision"],
        item["relative_path"],
        item["schema_version"],
    )


def _leaf_error_from_object(value: object) -> RollbackLeafError:
    item = _require_object(
        value,
        frozenset(("adapter", "error_code", "error_digest", "object_ref")),
        "rollback leaf error",
    )
    return RollbackLeafError(
        item["adapter"], item["object_ref"], item["error_code"], item["error_digest"]
    )


def _phase_receipt_from_object(value: object) -> RollbackPhaseReceipt:
    item = _require_object(
        value,
        frozenset(("leaf_errors", "phase", "receipt_digests", "receipt_refs")),
        "rollback phase receipt",
    )
    try:
        phase = RollbackPhase(item["phase"])
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("rollback phase is invalid") from error
    return RollbackPhaseReceipt(
        phase,
        tuple(_require_tuple(item["receipt_digests"], "phase receipt digests")),
        tuple(
            _payload_ref_from_object(ref)
            for ref in _require_tuple(item["receipt_refs"], "phase receipt refs")
        ),
        tuple(
            _leaf_error_from_object(error)
            for error in _require_tuple(item["leaf_errors"], "phase leaf errors")
        ),
    )


def _terminal_quarantine_ref_from_object(
    value: object,
) -> RollbackTerminalQuarantineRef:
    item = _require_object(
        value,
        frozenset(
            (
                "predecessor_generation",
                "predecessor_record_digest",
                "rollback_id",
                "schema_version",
                "successor_generation",
                "successor_name",
                "successor_raw_digest",
                "successor_record_digest",
                "tombstone_name",
                "tombstone_raw_digest",
                "transaction_id",
            )
        ),
        "rollback terminal quarantine ref",
    )
    return RollbackTerminalQuarantineRef(
        item["transaction_id"],
        item["rollback_id"],
        item["predecessor_generation"],
        item["predecessor_record_digest"],
        item["successor_generation"],
        item["successor_record_digest"],
        item["successor_raw_digest"],
        item["successor_name"],
        item["tombstone_name"],
        item["tombstone_raw_digest"],
        item["schema_version"],
    )


def _journal_from_object(value: object) -> RollbackJournalRecord:
    item = _require_object(
        value,
        frozenset(
            (
                "generation",
                "phase",
                "phase_receipts",
                "previous_record_digest",
                "request_digest",
                "request_payload_ref",
                "revision",
                "rollback_id",
                "terminal_quarantine_refs",
                "schema_version",
            )
        ),
        "rollback journal",
    )
    try:
        phase = RollbackPhase(item["phase"])
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("rollback journal phase is invalid") from error
    return RollbackJournalRecord(
        item["rollback_id"],
        item["request_digest"],
        _payload_ref_from_object(item["request_payload_ref"]),
        item["generation"],
        item["revision"],
        phase,
        tuple(
            _phase_receipt_from_object(receipt)
            for receipt in _require_tuple(
                item["phase_receipts"], "journal phase receipts"
            )
        ),
        item["previous_record_digest"],
        tuple(
            _terminal_quarantine_ref_from_object(ref)
            for ref in _require_tuple(
                item["terminal_quarantine_refs"],
                "journal terminal quarantine refs",
            )
        ),
        item["schema_version"],
    )


def _active_state_from_object(value: object) -> ActiveApprovedTupleState:
    item = _require_object(
        value,
        frozenset(
            (
                "approved_tuple",
                "generation",
                "operation_id",
                "previous_state_digest",
                "schema_version",
            )
        ),
        "active tuple state",
    )
    return ActiveApprovedTupleState(
        item["generation"],
        _active_tuple_from_object(item["approved_tuple"]),
        item["operation_id"],
        item["previous_state_digest"],
        item["schema_version"],
    )


def _ownership_from_object(value: object) -> DependentOwnership:
    item = _require_object(
        value,
        frozenset(
            (
                "approved_tuple_digest",
                "episode_id",
                "object_kind",
                "object_ref",
                "parent_refs",
                "registration_id",
                "run_id",
                "schema_version",
            )
        ),
        "dependent ownership",
    )
    try:
        kind = DependentObjectKind(item["object_kind"])
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("dependent object kind is invalid") from error
    return DependentOwnership(
        item["registration_id"],
        item["approved_tuple_digest"],
        item["episode_id"],
        item["run_id"],
        kind,
        _immutable_ref_from_object(item["object_ref"]),
        tuple(
            _immutable_ref_from_object(parent)
            for parent in _require_tuple(item["parent_refs"], "dependent parent refs")
        ),
        item["schema_version"],
    )


def _quarantine_receipt_from_object(value: object) -> DependentQuarantineReceipt:
    item = _require_object(
        value,
        frozenset(
            (
                "causal_root_digests",
                "cause_digest",
                "generation",
                "object_ref",
                "ownership_digest",
                "rollback_id",
                "schema_version",
            )
        ),
        "dependent quarantine receipt",
    )
    return DependentQuarantineReceipt(
        item["rollback_id"],
        item["cause_digest"],
        _immutable_ref_from_object(item["object_ref"]),
        item["ownership_digest"],
        tuple(_require_tuple(item["causal_root_digests"], "dependent causal roots")),
        item["generation"],
        item["schema_version"],
    )


def _dependent_record_from_object(value: object) -> DependentOwnershipRecord:
    item = _require_object(
        value,
        frozenset(
            (
                "export_eligible",
                "generation",
                "ownership",
                "previous_record_digest",
                "promotion_eligible",
                "quarantine_receipts",
                "schema_version",
            )
        ),
        "dependent ownership record",
    )
    return DependentOwnershipRecord(
        item["generation"],
        _ownership_from_object(item["ownership"]),
        item["promotion_eligible"],
        item["export_eligible"],
        tuple(
            _quarantine_receipt_from_object(receipt)
            for receipt in _require_tuple(
                item["quarantine_receipts"], "dependent quarantine receipts"
            )
        ),
        item["previous_record_digest"],
        item["schema_version"],
    )

__all__ = ['_DIGEST_RE', '_ID_RE', '_ROLE_RE', '_MAX_RECORD_BYTES', '_MAX_PAYLOAD_BYTES', '_MAX_RECEIPT_PAYLOADS', '_MAX_AGGREGATE_RECEIPT_PAYLOAD_BYTES', '_MAX_ROLLBACK_QUARANTINE_PAIRS', '_MAX_ROLLBACK_QUARANTINE_BYTES', '_MAX_ROLLBACK_QUARANTINE_TOMBSTONE_BYTES', '_MAX_ROLLBACK_QUARANTINE_ARTIFACTS', '_MAX_ROOT_ENTRIES', '_MAX_ROOT_NAME_BYTES', '_MAX_ABANDONED_TEMPS', '_MAX_ABANDONED_TEMP_NAME_BYTES', '_MAX_ABANDONED_TEMP_BYTES', '_MAX_CLEANUP_MANIFEST_BYTES', '_CLEANUP_PREPARING_NAME', '_CLEANUP_COMMITTED_NAME', '_CLEANUP_PREPARING_TEMP_NAME', '_CLEANUP_COMMITTED_TEMP_NAME', '_CLEANUP_RECEIPT_NAME', '_CLEANUP_RECEIPT_TEMP_NAME', '_TEST_CLEANUP_FAULT_HOOK', '_CleanupInjectedCrash', '_ROLLBACK_TERMINAL_DIRECTORY', '_ROLLBACK_TERMINAL_ANCHOR_INDEX', '_REQUEST_KEYS', '_OBSERVATION_KEYS', '_OBSERVATION_KINDS', 'RollbackStoreError', 'RollbackValidationError', 'RollbackConflictError', 'RollbackIdempotencyConflict', 'RollbackCorruptionError', 'DependentIneligibleError', 'canonical_json_bytes', 'canonical_digest', '_require_digest', '_require_id', '_require_role', '_require_int', '_require_bool', '_require_object', '_require_tuple', '_decode_canonical_payload', '_require_sorted_unique_array', '_validate_exact_model', '_validate_absolute_normalized_path', '_ImmutableFileIdentity', '_open_pinned_parent', '_PinnedImmutableSource', '_revalidate_source_capsules', '_source_identity_from_projection', '_validate_observation', '_validate_f6_input_and_sources', '_validate_request_payload_with_capsules', '_validate_request_payload', 'ImmutableObjectRef', 'ApprovedTupleRef', 'ActiveApprovedTuple', 'RollbackPhase', '_PHASE_ORDER', '_TERMINAL_PHASES', '_MAX_ROLLBACK_HISTORY_GENERATIONS', '_MAX_ROLLBACK_HISTORY_BYTES', '_PHASE_RECEIPT_KEYS', '_PHASE_BODY_KEYS', '_validate_cleanup_receipt', '_validate_episode_receipts', '_validate_evidence_invalidation', '_validate_revocation_receipt', '_validate_dependent_receipts', '_validate_active_tuple_receipt', '_validate_rerun_receipt', '_source_deletion_request_from_projection', '_source_deletion_receipt_from_projection', '_validate_source_deletion_body', '_validate_receipt_payload', 'RollbackPayloadKind', '_payload_relative_path', 'RollbackPayloadRef', 'RollbackLeafError', 'RollbackPhaseReceipt', 'RollbackTerminalQuarantineRef', 'RollbackJournalRecord', 'ActiveApprovedTupleState', 'ActiveApprovedTupleHistoryEntry', 'DependentObjectKind', 'DependentOwnership', 'DependentQuarantineReceipt', 'DependentOwnershipRecord', 'RollbackJournalStore', 'ActiveApprovedTupleStore', 'DependentQuarantineStore', '_immutable_ref_from_object', '_approved_tuple_ref_from_object', '_active_tuple_from_object', '_payload_ref_from_object', '_leaf_error_from_object', '_phase_receipt_from_object', '_terminal_quarantine_ref_from_object', '_journal_from_object', '_active_state_from_object', '_ownership_from_object', '_quarantine_receipt_from_object', '_dependent_record_from_object']
