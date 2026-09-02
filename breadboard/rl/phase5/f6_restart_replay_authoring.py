from __future__ import annotations

import hashlib
import os
import subprocess
import stat
from typing import Any, Literal, Mapping

from breadboard_engine.compilation.contracts import (
    ConfigBundleManifest,
    CompiledConfigManifest,
    canonical_json_bytes,
    canonical_json_loads,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    ArtifactFileRefV1,
    AuthorityBundleV1,
    CompositionRefV1,
    HarnessCompositionManifestV1,
    _ARTIFACT_MEDIA_TYPES,
    _build_authority_graph,
    _load_json_exact,
    _secure_read,
    _validate_secret,
    _validate_installed_registry_graph,
    _verify_config_bundle_cas,
)
from breadboard.rl.phase5.f4_campaign import ImmutableRef
from breadboard.artifacts.cas import FilesystemCAS
from scripts.rl_phase5.run_f6_restart_replay import (
    F6ImmutableIdentity,
    F6FileIdentity,
    F6ProductionBinding,
    F6SecretFileRef,
    F6RestartReplayInput,
    F6TargetIdentity,
)

_MAX_SOURCE_BYTES = 64 * 1024 * 1024


class F6RestartReplayAuthoringError(ValueError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest(value: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("lowercase sha256 digest required")
    return value


def _absolute(value: str) -> str:
    if type(value) is not str or not value.startswith("/") or os.path.normpath(value) != value:
        raise ValueError("absolute normalized path required")
    return value


def _identifier(value: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 256
        or value != value.strip()
        or any(character in "\r\n\x00" for character in value)
    ):
        raise ValueError("bounded normalized identifier required")
    return value


class F6ImmutableFileSource(_ExactModel):
    path: str
    sha256: str

    _path = field_validator("path")(_absolute)
    _sha256 = field_validator("sha256")(_digest)


class F6RestartReplayAuthoringInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f6-restart-replay-authoring-input.v1"]
    composition_descriptor: F6ImmutableFileSource
    composition_manifest: F6ImmutableFileSource
    authority_bundle: F6ImmutableFileSource
    original_request: F6ImmutableFileSource
    target: F6TargetIdentity
    fresh_episode_id: str
    task_input: dict[str, Any]
    run_context: dict[str, Any]
    secret_files: dict[str, F6ImmutableFileSource]
    report_path: str

    _fresh_episode_id = field_validator("fresh_episode_id")(_identifier)
    _report_path = field_validator("report_path")(_absolute)

    @field_validator("secret_files")
    @classmethod
    def exact_secret_sources(
        cls, value: dict[str, F6ImmutableFileSource]
    ) -> dict[str, F6ImmutableFileSource]:
        if not value or any(type(key) is not str or not key for key in value):
            raise ValueError("secret source map must be nonempty")
        paths = tuple(source.path for source in value.values())
        if len(paths) != len(set(paths)):
            raise ValueError("secret source paths must be unique")
        return value

    @model_validator(mode="after")
    def canonical_values(self) -> "F6RestartReplayAuthoringInput":
        canonical_json_bytes(self.task_input)
        canonical_json_bytes(self.run_context)
        sources = (
            self.composition_descriptor.path,
            self.composition_manifest.path,
            self.authority_bundle.path,
            self.original_request.path,
            *(source.path for source in self.secret_files.values()),
        )
        if len(sources) != len(set(sources)):
            raise ValueError("source artifact paths must be exclusive")
        if self.report_path in sources:
            raise ValueError("report path must be exclusive from source artifacts")
        return self


class F6RestartReplayInputBuildDescriptor(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f6-restart-replay-input-build.v1"]
    target_input_path: str
    target_input_sha256: str
    target_input_identity: F6FileIdentity
    composition_descriptor_sha256: str
    composition_manifest_sha256: str
    authority_bundle_sha256: str
    original_request_sha256: str
    immutable_identity_sha256: str
    payload_ready: Literal[True]

    _path = field_validator("target_input_path")(_absolute)
    _digests = field_validator(
        "target_input_sha256",
        "composition_descriptor_sha256",
        "composition_manifest_sha256",
        "authority_bundle_sha256",
        "original_request_sha256",
        "immutable_identity_sha256",
    )(_digest)


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_uid,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_nlink,
    )


def _capture_secret_ref(
    source: F6ImmutableFileSource,
) -> tuple[F6SecretFileRef, int]:
    try:
        raw, descriptor = _secure_read(
            source.path,
            expected_digest=source.sha256,
            secret=True,
        )
        opened = os.fstat(descriptor)
        current = os.stat(source.path, follow_symlinks=False)
        if (
            _metadata_identity(opened) != _metadata_identity(current)
            or _sha256(raw) != source.sha256
        ):
            raise ValueError("secret authority changed while being captured")
        identity = F6FileIdentity(
            device=opened.st_dev,
            inode=opened.st_ino,
            size_bytes=opened.st_size,
            mtime_ns=str(opened.st_mtime_ns),
            ctime_ns=str(opened.st_ctime_ns),
            owner_uid=opened.st_uid,
            mode=stat.S_IMODE(opened.st_mode),
            nlink=opened.st_nlink,
        )
        return (
            F6SecretFileRef(
                path=source.path,
                sha256=source.sha256,
                identity=identity,
            ),
            descriptor,
        )
    except (OSError, ValueError) as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise F6RestartReplayAuthoringError(
            f"secret authority is unavailable or unsafe: {source.path}"
        ) from exc


def _revalidate_secret_ref(
    source: F6SecretFileRef,
    descriptor: int,
) -> None:
    try:
        opened = os.fstat(descriptor)
        current = os.stat(source.path, follow_symlinks=False)
    except OSError as exc:
        raise F6RestartReplayAuthoringError(
            "secret authority changed before target publication"
        ) from exc
    expected = (
        source.identity.device,
        source.identity.inode,
        source.identity.size_bytes,
        int(source.identity.mtime_ns),
        int(source.identity.ctime_ns),
        source.identity.owner_uid,
        source.identity.mode,
        source.identity.nlink,
    )
    if (
        _metadata_identity(opened) != expected
        or _metadata_identity(current) != expected
    ):
        raise F6RestartReplayAuthoringError(
            "secret authority changed before target publication"
        )


def _read_source(
    source: F6ImmutableFileSource,
    *,
    canonical_json: bool,
    secret: bool = False,
) -> bytes:
    try:
        raw, descriptor = _secure_read(
            source.path,
            expected_digest=source.sha256,
            secret=secret,
        )
    except (OSError, ValueError) as exc:
        raise F6RestartReplayAuthoringError(
            f"immutable source is unavailable or changed: {source.path}"
        ) from exc
    try:
        if len(raw) > _MAX_SOURCE_BYTES:
            raise F6RestartReplayAuthoringError("immutable source exceeds authoring bound")
        if canonical_json:
            try:
                value = canonical_json_loads(raw)
            except Exception as exc:
                raise F6RestartReplayAuthoringError(
                    f"immutable source is not exact JSON: {source.path}"
                ) from exc
            if canonical_json_bytes(value) != raw:
                raise F6RestartReplayAuthoringError(
                    f"immutable source is not canonical JSON: {source.path}"
                )
        return raw
    finally:
        os.close(descriptor)


def _read_composition_json(source: F6ImmutableFileSource) -> bytes:
    raw = _read_source(source, canonical_json=False)
    try:
        _load_json_exact(raw)
    except ValueError as exc:
        raise F6RestartReplayAuthoringError(
            f"immutable source is not canonical composition JSON: {source.path}"
        ) from exc
    return raw


def _read_artifact_ref(
    ref: ArtifactFileRefV1,
    source_paths: set[str],
    *,
    canonical_json: bool,
) -> bytes:
    source_paths.add(ref.path)
    source = F6ImmutableFileSource(path=ref.path, sha256=ref.sha256)
    raw = _read_source(source, canonical_json=canonical_json)
    if len(raw) != ref.size_bytes:
        raise F6RestartReplayAuthoringError(
            f"immutable artifact size mismatch: {ref.path}"
        )
    return raw


def _immutable_ref(label: str, digest: str) -> ImmutableRef:
    return ImmutableRef(
        reference=f"artifact://phase5-f6/{label}@{digest}",
        digest=digest,
    )


def _verify_composition_sources(
    spec: F6RestartReplayAuthoringInput,
) -> tuple[
    CompositionRefV1,
    HarnessCompositionManifestV1,
    AuthorityBundleV1,
    set[str],
]:
    descriptor_raw = _read_composition_json(spec.composition_descriptor)
    descriptor = CompositionRefV1.model_validate_json(descriptor_raw, strict=True)
    if (
        descriptor.manifest_path != spec.composition_manifest.path
        or descriptor.manifest_sha256 != spec.composition_manifest.sha256
    ):
        raise F6RestartReplayAuthoringError(
            "composition descriptor does not bind the supplied manifest"
        )

    manifest_raw = _read_composition_json(spec.composition_manifest)
    if len(manifest_raw) != descriptor.manifest_size_bytes:
        raise F6RestartReplayAuthoringError("composition manifest size mismatch")
    manifest = HarnessCompositionManifestV1.model_validate_json(manifest_raw, strict=True)
    if (
        manifest.authority_bundle_ref.path != spec.authority_bundle.path
        or manifest.authority_bundle_ref.sha256 != spec.authority_bundle.sha256
    ):
        raise F6RestartReplayAuthoringError(
            "composition manifest does not bind the supplied authority bundle"
        )

    authority_raw = _read_composition_json(spec.authority_bundle)
    if len(authority_raw) != manifest.authority_bundle_ref.size_bytes:
        raise F6RestartReplayAuthoringError("authority bundle size mismatch")
    authority = AuthorityBundleV1.model_validate_json(authority_raw, strict=True)

    if (
        len(manifest.selector_catalog.direct) != 1
        or manifest.selector_catalog.weighted
        or len(authority.compiled_manifest_refs) != 1
        or len(authority.admission_receipt_refs) != 1
    ):
        raise F6RestartReplayAuthoringError(
            "F6 requires one exact current F3 direct-selection authority"
        )

    source_paths = {
        spec.composition_descriptor.path,
        spec.composition_manifest.path,
        spec.authority_bundle.path,
        spec.original_request.path,
        *(source.path for source in spec.secret_files.values()),
    }
    return descriptor, manifest, authority, source_paths


def _pin_file_authority(
    path: str,
    digest: str,
    pinned_fds: list[int],
    *,
    owner_uid: int | None = None,
    mode: int | None = None,
) -> int:
    _, descriptor = _secure_read(path, expected_digest=digest)
    try:
        opened = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        if (
            (opened.st_dev, opened.st_ino)
            != (current.st_dev, current.st_ino)
            or (owner_uid is not None and opened.st_uid != owner_uid)
            or (mode is not None and stat.S_IMODE(opened.st_mode) != mode)
        ):
            raise F6RestartReplayAuthoringError(
                "pinned file authority identity mismatch"
            )
    except BaseException:
        os.close(descriptor)
        raise
    pinned_fds.append(descriptor)
    return descriptor


def _pin_manifest_authorities(
    manifest: HarnessCompositionManifestV1,
    pinned_fds: list[int],
) -> None:
    for runtime in manifest.installed.runtimes:
        _pin_file_authority(
            runtime.executable_path,
            runtime.measured_binary_digest,
            pinned_fds,
        )
    daemon = manifest.installed.private_docker_daemon
    if daemon is not None:
        files = [
            daemon.dockerd,
            daemon.docker,
            daemon.runc,
            daemon.containerd,
            *(image.archive for image in daemon.images),
        ]
        for item in files:
            _pin_file_authority(
                item.path,
                item.digest,
                pinned_fds,
                owner_uid=item.owner_uid,
                mode=item.mode,
            )
    if manifest.host_runtime_authority is not None:
        item = manifest.host_runtime_authority.python_executable
        _pin_file_authority(
            item.path,
            item.digest,
            pinned_fds,
            owner_uid=item.owner_uid,
            mode=item.mode,
        )
    if manifest.openssl_authority is not None:
        item = manifest.openssl_authority
        descriptor = _pin_file_authority(
            item.path,
            item.sha256,
            pinned_fds,
            owner_uid=item.owner_uid,
            mode=item.mode,
        )
        metadata = os.fstat(descriptor)
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_ctime_ns,
            metadata.st_size,
        ) != (
            item.device,
            item.inode,
            int(item.ctime_ns),
            item.size_bytes,
        ):
            raise F6RestartReplayAuthoringError(
                "OpenSSL executable authority mismatch"
            )
        version = subprocess.run(
            (item.path, "version"),
            executable=f"/proc/{os.getpid()}/fd/{descriptor}",
            pass_fds=(descriptor,),
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
        if (
            version.returncode != 0
            or _sha256(version.stdout) != item.version_stdout_sha256
            or version.stdout.decode("utf-8", "strict").strip() != item.version
        ):
            raise F6RestartReplayAuthoringError(
                "OpenSSL version authority mismatch"
            )
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for value in manifest.stores.model_dump(mode="json").values():
        if not isinstance(value, dict):
            continue
        descriptor = os.open(value["path"], directory_flags)
        try:
            current = os.fstat(descriptor)
            actual = (
                current.st_dev,
                current.st_ino,
                current.st_uid,
                f"0{stat.S_IMODE(current.st_mode):03o}",
            )
            expected = (
                value["device"],
                value["inode"],
                value["owner_uid"],
                value["mode"],
            )
            if not stat.S_ISDIR(current.st_mode) or actual != expected:
                raise F6RestartReplayAuthoringError(
                    "directory authority mismatch"
                )
        except BaseException:
            os.close(descriptor)
            raise
        pinned_fds.append(descriptor)


def _resolve_immutable_identity(
    spec: F6RestartReplayAuthoringInput,
    manifest: HarnessCompositionManifestV1,
    authority: AuthorityBundleV1,
    source_paths: set[str],
) -> tuple[c.ResolveEpisodeRequest, F6ImmutableIdentity, list[int]]:
    request_raw = _read_source(spec.original_request, canonical_json=True)
    try:
        request = c.ResolveEpisodeRequest.model_validate_json(request_raw, strict=True)
    except Exception as exc:
        raise F6RestartReplayAuthoringError(
            "original request source is not an exact ResolveEpisodeRequest"
        ) from exc
    if request.episode_id == spec.fresh_episode_id:
        raise F6RestartReplayAuthoringError(
            "fresh episode ID must differ from the original episode ID"
        )

    supplied_handles = set(spec.secret_files)
    required_handles = {record.handle_id for record in manifest.secret_handles.records}
    if supplied_handles != required_handles:
        raise F6RestartReplayAuthoringError("secret handle closure mismatch")
    purpose_by_handle = {
        record.handle_id: record.purpose for record in manifest.secret_handles.records
    }
    secret_bytes = {
        handle_id: _validate_secret(
            _read_source(source, canonical_json=False, secret=True),
            purpose_by_handle[handle_id],
        )
        for handle_id, source in spec.secret_files.items()
    }

    admitted_raw = _read_artifact_ref(
        manifest.admitted_set_ref, source_paths, canonical_json=True
    )
    admitted = c.AdmittedSetManifest.model_validate_json(admitted_raw, strict=True)
    selector_ref = manifest.selector_catalog.direct[0]
    selector_raw = _read_artifact_ref(selector_ref, source_paths, canonical_json=True)
    selector = c.DirectSelector.model_validate_json(selector_raw, strict=True)
    if request.selector.digest != selector_ref.sha256:
        raise F6RestartReplayAuthoringError(
            "original request selector is not the composition selector"
        )

    config_bundle_raw = _read_artifact_ref(
        manifest.config_bundle_ref, source_paths, canonical_json=True
    )
    config_bundle = ConfigBundleManifest.from_json(config_bundle_raw)
    if config_bundle.canonical_bytes() != config_bundle_raw:
        raise F6RestartReplayAuthoringError("config bundle is not canonical")

    control_payloads = {
        "admission policy": _read_artifact_ref(
            manifest.control_plane.admission_policy_ref,
            source_paths,
            canonical_json=True,
        ),
        "registry snapshot": _read_artifact_ref(
            manifest.control_plane.registry_snapshot_ref,
            source_paths,
            canonical_json=True,
        ),
        "revocation snapshot": _read_artifact_ref(
            manifest.control_plane.revocation_snapshot_ref,
            source_paths,
            canonical_json=True,
        ),
        "policy capability snapshot": _read_artifact_ref(
            manifest.control_plane.policy_capability_snapshot_ref,
            source_paths,
            canonical_json=True,
        ),
    }
    expected_controls = {
        "admission policy": authority.admission_policy.canonical_bytes(),
        "registry snapshot": authority.registries.canonical_bytes(),
        "revocation snapshot": canonical_json_bytes(
            [item.model_dump(mode="json") for item in authority.revocations]
        ),
        "policy capability snapshot": canonical_json_bytes(
            [item.model_dump(mode="json") for item in authority.policy_capabilities]
        ),
    }
    if control_payloads != expected_controls:
        raise F6RestartReplayAuthoringError(
            "control-plane authority cross-reference mismatch"
        )
    if (
        admitted.admission_policy_digest
        != authority.admission_policy.canonical_digest()
        or admitted.registry_snapshot_digest
        != authority.registries.digests.snapshot_digest
        or {
            item.scope_digest: item for item in authority.revocations
        }.get(admitted.revocation.scope_digest)
        != admitted.revocation
        or selector.admitted_set_root != admitted.canonical_digest()
    ):
        raise F6RestartReplayAuthoringError(
            "admitted-set authority cross-reference mismatch"
        )

    if any(
        ref.media_type
        != _ARTIFACT_MEDIA_TYPES[c.ArtifactKind.COMPILED_MANIFEST]
        for ref in authority.compiled_manifest_refs
    ):
        raise F6RestartReplayAuthoringError(
            "compiled manifest media type mismatch"
        )
    if any(
        ref.media_type
        != _ARTIFACT_MEDIA_TYPES[c.ArtifactKind.ADMISSION_RECEIPT]
        for ref in authority.admission_receipt_refs
    ):
        raise F6RestartReplayAuthoringError(
            "admission receipt media type mismatch"
        )
    compiled_payloads = {
        ref.sha256: _read_artifact_ref(ref, source_paths, canonical_json=True)
        for ref in authority.compiled_manifest_refs
    }
    receipt_payloads = {
        ref.sha256: _read_artifact_ref(ref, source_paths, canonical_json=True)
        for ref in authority.admission_receipt_refs
    }
    receipts = tuple(
        c.AdmissionReceipt.model_validate_json(payload, strict=True)
        for payload in receipt_payloads.values()
    )
    if set(receipt_payloads) != set(admitted.receipt_digests) or {
        receipt.compiled.manifest_digest for receipt in receipts
    } != set(compiled_payloads):
        raise F6RestartReplayAuthoringError(
            "compiled manifest or receipt membership mismatch"
        )
    expected_compiler = manifest.control_plane.compiler
    for payload in compiled_payloads.values():
        compiler = CompiledConfigManifest.from_json(payload).compiler
        if (
            compiler.compiler_id,
            compiler.compiler_version,
            compiler.compiler_code_digest,
            compiler.config_schema_digest,
            compiler.manifest_schema_digest,
            compiler.canonicalizer_id,
            compiler.runtime_abi,
        ) != (
            expected_compiler.compiler_id,
            expected_compiler.semantic_version,
            expected_compiler.code_digest,
            expected_compiler.source_schema_digest,
            expected_compiler.manifest_schema_digest,
            expected_compiler.canonicalizer_id,
            expected_compiler.runtime_abi,
        ):
            raise F6RestartReplayAuthoringError(
                "compiler authority identity mismatch"
            )

    manifest_policy_routes = {
        item.handle_id: item.route_ids
        for item in manifest.secret_handles.records
        if item.purpose == "policy_callback"
    }
    graph_policy_routes = {
        item.handle_id: item.route_ids
        for item in authority.policy_http.secret_bindings
    }
    registry_policy_routes = {
        item.grant.handle_id: item.route_ids
        for item in authority.registries.secret_handles
        if item.grant.handle_id in manifest_policy_routes
    }
    if not (
        manifest_policy_routes == graph_policy_routes == registry_policy_routes
    ):
        raise F6RestartReplayAuthoringError(
            "policy secret route authority mismatch"
        )
    registry_secrets = {
        item.grant.handle_id: item
        for item in authority.registries.secret_handles
    }
    for binding in authority.policy_http.secret_bindings:
        record = registry_secrets.get(binding.handle_id)
        if (
            record is None
            or record.grant.handle_version_digest
            != binding.handle_version_digest
            or record.grant.scope_digest != binding.scope_digest
        ):
            raise F6RestartReplayAuthoringError(
                "policy secret identity authority mismatch"
            )

    tls_ca_by_route = {
        trust.route_id: _read_artifact_ref(
            trust.ca_bundle_ref, source_paths, canonical_json=False
        )
        for trust in authority.tls_trust
    }
    if any(
        not pem.startswith(b"-----BEGIN CERTIFICATE-----\n")
        or not pem.endswith(b"-----END CERTIFICATE-----\n")
        or pem.count(b"-----BEGIN CERTIFICATE-----") != 1
        for pem in tls_ca_by_route.values()
    ):
        raise F6RestartReplayAuthoringError("TLS CA authority is not canonical")
    artifact_refs = [
        manifest.authority_bundle_ref,
        manifest.config_bundle_ref,
        manifest.admitted_set_ref,
        *manifest.selector_catalog.direct,
        *manifest.selector_catalog.weighted,
        manifest.control_plane.admission_policy_ref,
        manifest.control_plane.registry_snapshot_ref,
        manifest.control_plane.revocation_snapshot_ref,
        manifest.control_plane.policy_capability_snapshot_ref,
        *authority.compiled_manifest_refs,
        *authority.admission_receipt_refs,
        *(trust.ca_bundle_ref for trust in authority.tls_trust),
    ]
    if manifest.host_runtime_authority is not None:
        artifact_refs.append(manifest.host_runtime_authority.build_report_ref)
        _read_artifact_ref(
            manifest.host_runtime_authority.build_report_ref,
            source_paths,
            canonical_json=True,
        )
    if manifest.openssl_authority is not None:
        artifact_refs.append(manifest.openssl_authority.discovery_report_ref)
        _read_artifact_ref(
            manifest.openssl_authority.discovery_report_ref,
            source_paths,
            canonical_json=True,
        )
    if manifest.tls_callback_runtime_input is not None:
        callback = manifest.tls_callback_runtime_input
        artifact_refs.extend(
            (callback.ca_certificate_ref, callback.leaf_certificate_ref)
        )
        for ref in (callback.ca_certificate_ref, callback.leaf_certificate_ref):
            pem = _read_artifact_ref(
                ref, source_paths, canonical_json=False
            )
            if (
                not pem.startswith(b"-----BEGIN CERTIFICATE-----\n")
                or not pem.endswith(b"-----END CERTIFICATE-----\n")
                or pem.count(b"-----BEGIN CERTIFICATE-----") != 1
            ):
                raise F6RestartReplayAuthoringError(
                    "TLS callback certificate authority is not canonical"
                )
    if manifest.evidence_receipt_signing_authority is not None:
        ref = manifest.evidence_receipt_signing_authority.public_key_ref
        artifact_refs.append(ref)
        public_key = _read_artifact_ref(
            ref, source_paths, canonical_json=False
        )
        if (
            not public_key.startswith(b"-----BEGIN PUBLIC KEY-----\n")
            or not public_key.endswith(b"-----END PUBLIC KEY-----\n")
        ):
            raise F6RestartReplayAuthoringError(
                "evidence public-key authority is not canonical"
            )

    if spec.report_path in source_paths:
        raise F6RestartReplayAuthoringError(
            "report path must be exclusive from transitive source artifacts"
        )
    if os.path.lexists(spec.report_path):
        raise F6RestartReplayAuthoringError("report output already exists")

    pinned_fds: list[int] = []
    cas: FilesystemCAS | None = None
    try:
        _pin_manifest_authorities(manifest, pinned_fds)
        for source in (
            spec.composition_descriptor,
            spec.composition_manifest,
            spec.authority_bundle,
            spec.original_request,
            *spec.secret_files.values(),
        ):
            _pin_file_authority(
                source.path, source.sha256, pinned_fds
            )
        for ref in artifact_refs:
            _pin_file_authority(
                ref.path, ref.sha256, pinned_fds
            )
        cas = FilesystemCAS(manifest.stores.cas.path)
        cas_stat = os.fstat(cas._root_fd)
        if (cas_stat.st_dev, cas_stat.st_ino) != (
            manifest.stores.cas.device,
            manifest.stores.cas.inode,
        ):
            raise F6RestartReplayAuthoringError(
                "CAS directory authority mismatch"
            )
        _verify_config_bundle_cas(
            cas,
            {config_bundle.bundle_digest: config_bundle},
            compiled_payloads,
        )
        _validate_installed_registry_graph(
            manifest.installed,
            authority.registries,
            receipts,
            manifest.evidence_bindings,
        )
        receipt_handle = (
            manifest.control_plane.receipt_authenticator.secret_handle_id
        )
        graph = _build_authority_graph(
            cas=cas,
            policy=authority.admission_policy,
            registries=authority.registries,
            revocations=authority.revocations,
            policy_capabilities=authority.policy_capabilities,
            admitted_set=admitted,
            direct_selectors=(selector,),
            weighted_selectors=(),
            compiled_manifests=compiled_payloads,
            admission_receipts=receipt_payloads,
            policy_http=authority.policy_http,
            tls_trust=authority.tls_trust,
            tls_ca_pem_by_route=tls_ca_by_route,
            receipt_key_id=manifest.control_plane.receipt_authenticator.key_id,
            receipt_key=secret_bytes[receipt_handle],
        )
        resolved = graph.config_runtime.resolve_episode(request)
        selection_raw = graph.store.load(
            resolved.selection_record_ref.sha256,
            kind=c.ArtifactKind.SELECTION_RECORD,
            max_bytes=_MAX_SOURCE_BYTES,
        )
        selection = c.SelectionRecord.model_validate_json(
            selection_raw, strict=True
        )
        plan = resolved.effective_plan
        if (
            len(plan.policy_slots) != 1
            or plan.task.repository_snapshot_digest is None
        ):
            raise F6RestartReplayAuthoringError(
                "F3 plan does not close one model slot and repository identity"
            )
        slot = plan.policy_slots[0]
        identity = F6ImmutableIdentity(
            selection_algorithm=selection.algorithm,
            selected_candidate_id=selection.selected_candidate_id,
            selector_digest=selection.selector_digest,
            config_set_digest=selection.config_set_digest,
            compiled_manifest_digest=plan.base_compiled.manifest_digest,
            config_bundle_digest=plan.base_compiled.bundle_digest,
            dependency_closure_digest=plan.base_compiled.closure_digest,
            compiler_identity_digest=plan.base_compiled.compiler.canonical_digest(),
            base_receipt_digest=plan.base_receipt_digest,
            final_receipt_digest=plan.final_receipt_digest,
            runner_adapter_id=plan.runner.adapter_id,
            runner_runtime_abi=plan.runner.runtime_abi,
            runner_implementation_digest=plan.runner.implementation_digest,
            task_binding_digest=plan.task.task_binding_digest,
            task_contract_digest=plan.task.task_contract_digest,
            repository_snapshot_digest=plan.task.repository_snapshot_digest,
            model_digest=slot.model_digest,
            tokenizer_digest=slot.tokenizer_digest,
            checkpoint_digest=slot.checkpoint_digest,
            primary_image_digest=plan.sandbox.image_digest,
            verifier_image_digest=plan.verifier.image_digest,
            verifier_implementation_digest=plan.verifier.implementation_digest,
        )
    except Exception as exc:
        for descriptor in reversed(pinned_fds):
            os.close(descriptor)
        if isinstance(exc, F6RestartReplayAuthoringError):
            raise
        raise F6RestartReplayAuthoringError(
            "production authority closure is stale, mismatched, or does not admit the original request"
        ) from exc
    finally:
        if cas is not None:
            cas.close()
    return request, identity, pinned_fds


def _write_exclusive(path: str, payload: bytes) -> F6FileIdentity:
    normalized = _absolute(path)
    parent, name = os.path.split(normalized)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_descriptor = os.open(parent, directory_flags)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=parent_descriptor,
        )
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short F6 target input write")
            offset += written
        os.fsync(descriptor)
        persisted = os.fstat(descriptor)
        if (
            not stat.S_ISREG(persisted.st_mode)
            or persisted.st_uid != os.geteuid()
            or persisted.st_nlink != 1
            or stat.S_IMODE(persisted.st_mode) != 0o400
            or persisted.st_size != len(payload)
        ):
            raise F6RestartReplayAuthoringError(
                "persisted F6 target input identity is unsafe"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        reread = bytearray()
        while len(reread) <= len(payload):
            chunk = os.read(
                descriptor,
                min(64 * 1024, len(payload) + 1 - len(reread)),
            )
            if not chunk:
                break
            reread.extend(chunk)
        after = os.fstat(descriptor)
        current = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            bytes(reread) != payload
            or _metadata_identity(persisted) != _metadata_identity(after)
            or _metadata_identity(after) != _metadata_identity(current)
        ):
            raise F6RestartReplayAuthoringError(
                "persisted F6 target input readback mismatch"
            )
        os.fsync(parent_descriptor)
        current = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if _metadata_identity(after) != _metadata_identity(current):
            raise F6RestartReplayAuthoringError(
                "persisted F6 target input changed during directory fsync"
            )
        return F6FileIdentity(
            device=after.st_dev,
            inode=after.st_ino,
            size_bytes=after.st_size,
            mtime_ns=str(after.st_mtime_ns),
            ctime_ns=str(after.st_ctime_ns),
            owner_uid=after.st_uid,
            mode=stat.S_IMODE(after.st_mode),
            nlink=after.st_nlink,
        )
    except BaseException:
        if descriptor >= 0:
            opened = os.fstat(descriptor)
            try:
                current = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                current = None
            if (
                current is not None
                and (opened.st_dev, opened.st_ino)
                == (current.st_dev, current.st_ino)
            ):
                os.unlink(name, dir_fd=parent_descriptor)
                try:
                    os.fsync(parent_descriptor)
                except OSError:
                    pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def build_f6_restart_replay_input(
    spec: F6RestartReplayAuthoringInput,
    output_path: str,
) -> F6RestartReplayInputBuildDescriptor:
    if type(spec) is not F6RestartReplayAuthoringInput:
        raise TypeError("exact F6RestartReplayAuthoringInput required")
    output_path = _absolute(output_path)
    explicit_sources = {
        spec.composition_descriptor.path,
        spec.composition_manifest.path,
        spec.authority_bundle.path,
        spec.original_request.path,
        *(source.path for source in spec.secret_files.values()),
    }
    if output_path in explicit_sources or output_path == spec.report_path:
        raise F6RestartReplayAuthoringError(
            "target input output must be exclusive from source and report paths"
        )
    if os.path.lexists(output_path):
        raise F6RestartReplayAuthoringError(
            "target input output already exists"
        )

    _, manifest, authority, source_paths = _verify_composition_sources(spec)
    if output_path in source_paths:
        raise F6RestartReplayAuthoringError(
            "target input output must be exclusive from transitive source artifacts"
        )
    secret_pins: list[tuple[F6SecretFileRef, int]] = []
    pinned_fds: list[int] = []
    try:
        for handle_id, source in sorted(spec.secret_files.items()):
            secret_ref, descriptor = _capture_secret_ref(source)
            secret_pins.append((secret_ref, descriptor))
        request, identity, pinned_fds = _resolve_immutable_identity(
            spec,
            manifest,
            authority,
            source_paths,
        )
        production = F6ProductionBinding(
            composition_ref_path=spec.composition_descriptor.path,
            composition_descriptor_ref=_immutable_ref(
                "composition-descriptor",
                spec.composition_descriptor.sha256,
            ),
            composition_manifest_ref=_immutable_ref(
                "composition-manifest",
                spec.composition_manifest.sha256,
            ),
            authority_bundle_ref=_immutable_ref(
                "authority-bundle",
                spec.authority_bundle.sha256,
            ),
            secret_files={
                handle_id: secret_ref
                for handle_id, (secret_ref, _) in zip(
                    sorted(spec.secret_files),
                    secret_pins,
                    strict=True,
                )
            },
        )
        fresh_payload = request.model_dump(mode="json")
        fresh_payload["episode_id"] = spec.fresh_episode_id
        fresh_request = c.ResolveEpisodeRequest.model_validate_json(
            canonical_json_bytes(fresh_payload),
            strict=True,
        )
        target_input = F6RestartReplayInput(
            schema_version="bb.rl.phase5-f6-restart-replay-input.v1",
            production=production,
            target=spec.target,
            immutable_identity=identity,
            original_request=request,
            fresh_live_request=fresh_request,
            task_input=spec.task_input,
            run_context=spec.run_context,
            report_path=spec.report_path,
        )
        payload = canonical_json_bytes(
            target_input.model_dump(mode="json")
        )
        for secret_ref, descriptor in secret_pins:
            _revalidate_secret_ref(secret_ref, descriptor)
        output_identity = _write_exclusive(output_path, payload)
        return F6RestartReplayInputBuildDescriptor(
            schema_version="bb.rl.phase5-f6-restart-replay-input-build.v1",
            target_input_path=output_path,
            target_input_sha256=_sha256(payload),
            target_input_identity=output_identity,
            composition_descriptor_sha256=spec.composition_descriptor.sha256,
            composition_manifest_sha256=spec.composition_manifest.sha256,
            authority_bundle_sha256=spec.authority_bundle.sha256,
            original_request_sha256=spec.original_request.sha256,
            immutable_identity_sha256=_sha256(
                canonical_json_bytes(identity.model_dump(mode="json"))
            ),
            payload_ready=True,
        )
    finally:
        for descriptor in reversed(pinned_fds):
            os.close(descriptor)
        for _, descriptor in reversed(secret_pins):
            os.close(descriptor)


def read_f6_restart_replay_authoring_input(
    path: str,
) -> F6RestartReplayAuthoringInput:
    normalized = _absolute(path)
    try:
        raw, descriptor = _secure_read(normalized)
    except (OSError, ValueError) as exc:
        raise F6RestartReplayAuthoringError(
            "authoring input is unavailable or unsafe"
        ) from exc
    try:
        try:
            value = canonical_json_loads(raw)
        except Exception as exc:
            raise F6RestartReplayAuthoringError(
                "authoring input is not exact JSON"
            ) from exc
        if canonical_json_bytes(value) != raw:
            raise F6RestartReplayAuthoringError(
                "authoring input is not canonical JSON"
            )
    finally:
        os.close(descriptor)
    return F6RestartReplayAuthoringInput.model_validate_json(raw, strict=True)


__all__ = [
    "F6ImmutableFileSource",
    "F6RestartReplayAuthoringError",
    "F6RestartReplayAuthoringInput",
    "F6RestartReplayInputBuildDescriptor",
    "build_f6_restart_replay_input",
    "read_f6_restart_replay_authoring_input",
]
