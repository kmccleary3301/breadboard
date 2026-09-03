"""Session-owned attachment staging, content addressing, and projection."""

from __future__ import annotations

import json
import os
import shutil
import stat
import uuid
from pathlib import Path
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence

from fastapi import HTTPException, UploadFile, status

from breadboard.product.runtime import AnchoredStorage, ArtifactStore
from breadboard.product.runtime.artifacts import (
    ArtifactRef,
    _validate_artifact_name,
    workspace_artifact_ref,
)
from breadboard.product.runtime.session_store import (
    _MAX_ARTIFACT_MANIFEST_AGGREGATE_BYTES,
    _MAX_ARTIFACT_MANIFEST_BYTES,
    _MAX_ARTIFACT_MANIFESTS,
    authorize_session_artifact_manifest,
)

from .models import AttachmentHandle, AttachmentUploadResponse

MAX_ATTACHMENT_BYTES = 16 * 1024

def _open_workspace_breadboard(
    workspace_dir: Path,
) -> tuple[Path, Path, int | None, list[int]]:
    workspace_root = workspace_dir.resolve()
    logical = workspace_root / ".breadboard"
    if os.name == "nt":
        handles: list[int] = []
        try:
            handles.append(
                AnchoredStorage.windows_handle(
                    workspace_root, directory=True, create=False
                )
            )
            handles.append(AnchoredStorage.windows_handle(logical, directory=True))
            return logical, workspace_root, None, handles
        except OSError as exc:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid workspace metadata path",
            ) from exc
    try:
        expected = workspace_root.stat(follow_symlinks=False)
        root_fd = os.open(
            workspace_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid workspace root"
        ) from exc
    actual = os.fstat(root_fd)
    if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
        os.close(root_fd)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace root changed",
        )
    metadata_fd = None
    try:
        try:
            os.mkdir(".breadboard", dir_fd=root_fd)
        except FileExistsError:
            pass
        metadata_fd = os.open(
            ".breadboard",
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        os.fsync(root_fd)
        return logical, workspace_root, metadata_fd, []
    except OSError as exc:
        if metadata_fd is not None:
            os.close(metadata_fd)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid workspace metadata path",
        ) from exc
    finally:
        os.close(root_fd)


class SessionArtifactStore:
    """Own live attachment references and their durable artifact projection."""

    def __init__(self, *, session_id: str, metadata: Dict[str, Any]) -> None:
        self.session_id = session_id
        self.metadata = metadata
        self._artifact_refs: Dict[str, Any] = {}
        self._attachment_entries: Dict[str, Dict[str, Any]] = {}

    def register_attachments(self, entries: Sequence[Dict[str, Any]]) -> None:
        for entry in entries:
            attachment_id = entry.get("id")
            if attachment_id:
                self._attachment_entries[str(attachment_id)] = dict(entry)

    def artifact_refs(self) -> Mapping[str, Any]:
        return MappingProxyType(self._artifact_refs)

    def selected_artifacts(
        self, attachment_ids: Sequence[str]
    ) -> list[Any]:
        unknown = [
            item
            for item in attachment_ids
            if item not in self._artifact_refs
        ]
        if unknown:
            raise ValueError(f"unknown attachment IDs: {', '.join(unknown)}")
        selected = [self._artifact_refs[item] for item in attachment_ids]
        total_bytes = sum(
            int(getattr(item, "size_bytes", MAX_ATTACHMENT_BYTES + 1))
            for item in selected
        )
        if total_bytes > MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"selected attachments exceed {MAX_ATTACHMENT_BYTES}-byte handoff limit"
            )
        return selected

    def list_rows(self) -> list[dict[str, Any]]:
        return [
            {"name": name, **reference.as_dict()}
            for name, reference in sorted(self._artifact_refs.items())
        ]

    def format_helper(
        self, attachment_ids: Sequence[str]
    ) -> tuple[str, Dict[str, Dict[str, Any]], list[Dict[str, str]]]:
        helper_lines: list[str] = []
        capabilities: Dict[str, Dict[str, Any]] = {}
        media: list[Dict[str, str]] = []
        for index, key in enumerate(
            dict.fromkeys(str(value) for value in attachment_ids), start=1
        ):
            info = self._attachment_entries.get(key)
            if not info:
                continue
            artifact_ref = self._artifact_refs.get(key)
            if artifact_ref is None:
                raise RuntimeError(f"attachment artifact missing: {key}")
            filename = str(info.get("filename") or key)
            _validate_artifact_name(key)
            uri = f"attachment://{artifact_ref.digest}"
            capabilities[uri] = artifact_ref.as_dict()
            if str(artifact_ref.media_type).startswith("image/"):
                media.append(
                    {
                        "type": "media",
                        "kind": "image",
                        "uri": uri,
                        "mime": str(artifact_ref.media_type),
                    }
                )
            helper_lines.append(
                "[Attachment "
                f"{index}: name={json.dumps(filename, ensure_ascii=True)}; "
                f"uri={uri}; size_bytes={artifact_ref.size_bytes}; "
                "read with read_file after normal authorization]"
            )
        return "\n".join(helper_lines), capabilities, media
    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        candidate = filename.strip() or "attachment.bin"
        candidate = candidate.replace("\\", "/")
        return os.path.basename(candidate)

    def _read_manifest(
        self,
        workspace: Path,
        manifest_ref: ArtifactRef,
    ) -> Dict[str, ArtifactRef]:
        anchor, _, descriptor, windows_handles = _open_workspace_breadboard(workspace)
        artifact_fd = None
        try:
            if descriptor is not None:
                artifact_fd = AnchoredStorage.open_directory(
                    descriptor,
                    "artifacts",
                    create=False,
                )
            store = ArtifactStore(anchor / "artifacts", descriptor=artifact_fd)
            document = json.loads(store.read(manifest_ref))
        finally:
            if artifact_fd is not None:
                os.close(artifact_fd)
            if descriptor is not None:
                os.close(descriptor)
            for handle in reversed(windows_handles):
                AnchoredStorage.close_windows_handle(handle)
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != "bb.artifact_manifest.v1"
            or document.get("session_id") != self.session_id
            or not isinstance(document.get("artifacts"), list)
        ):
            raise ValueError("invalid retained attachment manifest")
        restored: Dict[str, ArtifactRef] = {}
        for row in document["artifacts"]:
            if not isinstance(row, dict) or set(row) != {
                "name",
                "digest",
                "size_bytes",
                "media_type",
            }:
                raise ValueError("invalid retained attachment manifest row")
            name = row["name"]
            _validate_artifact_name(name)
            if name in restored:
                raise ValueError("duplicate retained attachment manifest row")
            restored[name] = ArtifactRef(
                digest=row["digest"],
                size_bytes=row["size_bytes"],
                media_type=row["media_type"],
            )
        return restored

    def _manifest_names(self, workspace: Path) -> list[str]:
        anchor, _, descriptor, windows_handles = _open_workspace_breadboard(workspace)
        artifact_fd = manifest_fd = None
        try:
            if descriptor is not None:
                artifact_fd = AnchoredStorage.open_directory(
                    descriptor,
                    "artifacts",
                    create=False,
                )
                manifest_fd = AnchoredStorage.open_directory(
                    artifact_fd,
                    "manifests",
                    create=False,
                )
                return sorted(os.listdir(manifest_fd))
            manifest_root = anchor / "artifacts" / "manifests"
            windows_handles.append(
                AnchoredStorage.windows_handle(
                    manifest_root,
                    directory=True,
                    create=False,
                )
            )
            return sorted(path.name for path in manifest_root.iterdir())
        except FileNotFoundError:
            return []
        finally:
            if manifest_fd is not None:
                os.close(manifest_fd)
            if artifact_fd is not None:
                os.close(artifact_fd)
            if descriptor is not None:
                os.close(descriptor)
            for handle in reversed(windows_handles):
                AnchoredStorage.close_windows_handle(handle)

    def restore_manifest(self, workspace: Path) -> None:
        prefix = f"{self.session_id}."
        retained_ref = self.metadata.get("artifact_manifest_ref")
        retained_digest: str | None = None
        if retained_ref is not None:
            if not isinstance(retained_ref, Mapping):
                raise ValueError("invalid retained attachment manifest reference")
            digest = retained_ref.get("digest")
            if (
                not isinstance(digest, str)
                or not digest.startswith("sha256:")
                or len(digest) != len("sha256:") + 64
                or any(
                    character not in "0123456789abcdef"
                    for character in digest.removeprefix("sha256:")
                )
            ):
                raise ValueError("invalid retained attachment manifest reference")
            retained_digest = digest.removeprefix("sha256:")
        manifest_names = [
            name
            for name in self._manifest_names(workspace)
            if name.startswith(prefix) and name.endswith(".json")
        ]
        if len(manifest_names) > _MAX_ARTIFACT_MANIFESTS:
            raise ValueError("too many retained attachment manifests")
        manifest_refs: list[tuple[str, ArtifactRef]] = []
        aggregate_size = 0
        for name in manifest_names:
            digest = name[len(prefix) : -len(".json")]
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("invalid retained attachment manifest name")
            manifest_ref = workspace_artifact_ref(
                workspace,
                f"sha256:{digest}",
                media_type="application/json",
            )
            if manifest_ref.size_bytes > _MAX_ARTIFACT_MANIFEST_BYTES:
                raise ValueError("retained attachment manifest is oversized")
            aggregate_size += manifest_ref.size_bytes
            if aggregate_size > _MAX_ARTIFACT_MANIFEST_AGGREGATE_BYTES:
                raise ValueError("retained attachment manifests are oversized")
            manifest_refs.append((digest, manifest_ref))
        candidates: list[
            tuple[int, str, ArtifactRef, Dict[str, ArtifactRef]]
        ] = []
        for digest, manifest_ref in manifest_refs:
            restored = self._read_manifest(workspace, manifest_ref)
            candidates.append((len(restored), digest, manifest_ref, restored))
        if not candidates:
            if retained_digest is not None:
                raise ValueError("retained attachment manifest is missing")
            return
        history_head = max(candidates)
        if any(
            any(history_head[3].get(name) != ref for name, ref in candidate.items())
            for *_, candidate in candidates
        ):
            raise ValueError("retained attachment manifests do not form one history")
        if retained_digest is None:
            _, _, selected_ref, selected = history_head
        else:
            selected_ref, selected = next(
                (
                    (manifest_ref, candidate)
                    for _, digest, manifest_ref, candidate in candidates
                    if digest == retained_digest
                ),
                (None, None),
            )
            if selected_ref is None or selected is None:
                raise ValueError("retained attachment manifest is missing")
        workspace_root = workspace.resolve()
        attachment_root = workspace_root / ".breadboard" / "attachments"
        attachment_entries: Dict[str, Dict[str, Any]] = {}
        for attachment_id in selected:
            attachment_dir = attachment_root / attachment_id
            try:
                directory_stat = attachment_dir.stat(follow_symlinks=False)
                children = tuple(attachment_dir.iterdir())
            except FileNotFoundError as exc:
                raise ValueError("retained attachment materialization is missing") from exc
            if not stat.S_ISDIR(directory_stat.st_mode) or len(children) != 1:
                raise ValueError("invalid retained attachment materialization")
            materialized = children[0]
            try:
                materialized_stat = materialized.stat(follow_symlinks=False)
            except FileNotFoundError as exc:
                raise ValueError("retained attachment materialization is missing") from exc
            if not stat.S_ISREG(materialized_stat.st_mode):
                raise ValueError("invalid retained attachment materialization")
            attachment_entries[attachment_id] = {
                "id": attachment_id,
                "filename": materialized.name,
                "absolute_path": str(materialized),
                "relative_path": str(materialized.relative_to(workspace_root)),
                "metadata": {},
            }
        self._attachment_entries = attachment_entries
        self.metadata["artifact_manifest_ref"] = selected_ref.as_dict()
        self._artifact_refs = selected

    def authorize_manifest(
        self,
        workspace: Path,
        *,
        expected_session_directory_identity: Any = None,
    ) -> None:
        manifest_ref = self.metadata.get("artifact_manifest_ref")
        if not isinstance(manifest_ref, Mapping):
            return
        digest = manifest_ref.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise ValueError("invalid attachment manifest reference")
        authorize_session_artifact_manifest(
            workspace,
            self.session_id,
            f"{self.session_id}.{digest.removeprefix('sha256:')}.json",
            expected_session_directory_identity=expected_session_directory_identity,
        )

    async def upload(
        self,
        files: Sequence[UploadFile],
        *,
        workspace_dir: Path,
        metadata: Optional[dict[str, Any]] = None,
        persist: Callable[[], Awaitable[None]] | None = None,
    ) -> AttachmentUploadResponse:
        if not files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="no files provided"
            )
        staged_uploads = []
        staged_bytes = 0
        for index, upload in enumerate(files, start=1):
            data = bytearray()
            try:
                while True:
                    chunk = await upload.read(
                        MAX_ATTACHMENT_BYTES - staged_bytes - len(data) + 1
                    )
                    if not chunk:
                        break
                    data.extend(chunk)
                    if staged_bytes + len(data) > MAX_ATTACHMENT_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=f"attachments exceed {MAX_ATTACHMENT_BYTES}-byte handoff limit",
                        )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"failed to read upload: {exc}",
                ) from exc
            if data:
                staged_uploads.append((index, upload, bytes(data)))
                staged_bytes += len(data)
        if not staged_uploads:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no attachment data found",
            )
        attachment_entries: list[dict[str, Any]] = []
        handles: list[AttachmentHandle] = []
        created_dirs: list[str] = []
        created_refs = set()
        anchor, workspace_root, descriptor, windows_handles = (
            _open_workspace_breadboard(workspace_dir)
        )
        artifact_fd = attachment_fd = None
        artifact_root, attachment_root = anchor / "artifacts", anchor / "attachments"
        artifact_refs = dict(self._artifact_refs)
        artifact_refs_before = dict(self._artifact_refs)
        metadata_before = {
            key: (key in self.metadata, self.metadata.get(key))
            for key in ("artifact_manifest", "artifact_manifest_ref")
        }
        manifest_path: Path | None = None
        manifest_fd = None
        manifest_name = None
        transaction = None
        registered_before = dict(self._attachment_entries)
        try:
            if descriptor is not None:
                artifact_fd = AnchoredStorage.open_directory(descriptor, "artifacts")
                try:
                    attachment_fd = AnchoredStorage.open_directory(
                        descriptor, "attachments"
                    )
                except BaseException:
                    os.close(artifact_fd)
                    artifact_fd = None
                    raise
                os.fsync(descriptor)
            artifact_store = ArtifactStore(artifact_root, descriptor=artifact_fd)
            candidate_transaction = artifact_store.transaction()
            candidate_transaction.__enter__()
            transaction = candidate_transaction
            if attachment_fd is None:
                attachment_root.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                windows_handles.append(
                    AnchoredStorage.windows_handle(artifact_root, directory=True)
                )
                windows_handles.append(
                    AnchoredStorage.windows_handle(attachment_root, directory=True)
                )
            try:
                for index, upload, data in staged_uploads:
                    attachment_id = f"att-{uuid.uuid4().hex[:10]}"
                    filename = self._sanitize_filename(
                        upload.filename or f"attachment-{index}.bin"
                    )
                    created_dirs.append(attachment_id)
                    if attachment_fd is not None:
                        target_fd = AnchoredStorage.open_directory(
                            attachment_fd, attachment_id
                        )
                    else:
                        target_fd = None
                        (attachment_root / attachment_id).mkdir(
                            parents=True, exist_ok=True
                        )
                    try:
                        artifact_ref = artifact_store.put(
                            data,
                            media_type=upload.content_type
                            or "application/octet-stream",
                            created=created_refs,
                        )
                        if target_fd is not None:
                            artifact_store.materialize_at(
                                artifact_ref, target_fd, filename
                            )
                        else:
                            artifact_store.materialize(
                                artifact_ref, attachment_root / attachment_id / filename
                            )
                        artifact_refs[attachment_id] = artifact_ref
                    finally:
                        if target_fd is not None:
                            os.close(target_fd)
                    logical_target = (
                        workspace_root
                        / ".breadboard"
                        / "attachments"
                        / attachment_id
                        / filename
                    )
                    handles.append(
                        AttachmentHandle(
                            id=attachment_id,
                            filename=filename,
                            mime=upload.content_type,
                            size_bytes=len(data),
                        )
                    )
                    attachment_entries.append(
                        {
                            "id": attachment_id,
                            "filename": filename,
                            "absolute_path": str(logical_target),
                            "relative_path": str(
                                logical_target.relative_to(workspace_root)
                            ),
                            "metadata": metadata or {},
                        }
                    )
                manifest = artifact_store.manifest(self.session_id, artifact_refs)
                manifest_ref = artifact_store.put_json(manifest, created=created_refs)
                manifest_name = (
                    f"{self.session_id}.{manifest_ref.digest.removeprefix('sha256:')}.json"
                )
                if artifact_fd is not None:
                    manifest_fd = AnchoredStorage.open_directory(
                        artifact_fd, "manifests"
                    )
                    artifact_store.materialize_at(
                        manifest_ref, manifest_fd, manifest_name
                    )
                    os.fsync(artifact_fd)
                else:
                    manifest_path = artifact_root / "manifests" / manifest_name
                    artifact_store.materialize(manifest_ref, manifest_path)
                if (
                    descriptor is not None
                    and (workspace_root / ".breadboard").resolve()
                    != AnchoredStorage.descriptor_path(descriptor).resolve()
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="workspace metadata path changed",
                    )
                self.register_attachments(attachment_entries)
                self._artifact_refs = artifact_refs
                self.metadata["artifact_manifest"] = manifest
                self.metadata["artifact_manifest_ref"] = manifest_ref.as_dict()
                if persist is not None:
                    await persist()
            except BaseException:
                if attachment_fd is not None:
                    for name in created_dirs:
                        try:
                            target_fd = AnchoredStorage.open_directory(
                                attachment_fd, name, create=False
                            )
                        except FileNotFoundError:
                            continue
                        try:
                            for child in os.listdir(target_fd):
                                os.unlink(child, dir_fd=target_fd)
                        finally:
                            os.close(target_fd)
                        os.rmdir(name, dir_fd=attachment_fd)
                    if manifest_fd is not None and manifest_name is not None:
                        try:
                            os.unlink(manifest_name, dir_fd=manifest_fd)
                        except FileNotFoundError:
                            pass
                        os.fsync(manifest_fd)
                        os.fsync(artifact_fd)
                    os.fsync(attachment_fd)
                else:
                    for name in created_dirs:
                        target = attachment_root / name
                        target_lock = (
                            AnchoredStorage.windows_handle(
                                target, directory=True, create=False
                            )
                            if os.name == "nt"
                            else None
                        )
                        try:
                            if target_lock is None:
                                shutil.rmtree(target, ignore_errors=True)
                            else:
                                for child in target.iterdir():
                                    child.unlink()
                        finally:
                            AnchoredStorage.close_windows_handle(target_lock)
                        if target_lock is not None:
                            target.rmdir()
                    if manifest_path is not None:
                        manifest_lock = (
                            AnchoredStorage.windows_handle(
                                manifest_path.parent, directory=True, create=False
                            )
                            if os.name == "nt"
                            else None
                        )
                        try:
                            manifest_path.unlink(missing_ok=True)
                        finally:
                            AnchoredStorage.close_windows_handle(manifest_lock)
                    for parent in {
                        attachment_root,
                        manifest_path.parent
                        if manifest_path is not None
                        else artifact_root,
                    }:
                        AnchoredStorage.sync_directory(
                            parent
                        ) if parent.is_dir() else None
                for artifact_ref in created_refs:
                    artifact_store.discard(artifact_ref)
                self._artifact_refs = artifact_refs_before
                for key, (present, value) in metadata_before.items():
                    if present:
                        self.metadata[key] = value
                    else:
                        self.metadata.pop(key, None)
                self._attachment_entries = registered_before
                raise
        finally:
            if transaction is not None:
                transaction.__exit__(None, None, None)
            for open_descriptor in (
                manifest_fd,
                artifact_fd,
                attachment_fd,
                descriptor,
            ):
                if open_descriptor is not None:
                    os.close(open_descriptor)
            for handle in reversed(windows_handles):
                AnchoredStorage.close_windows_handle(handle)
        if manifest_name is None:
            raise RuntimeError("attachment manifest was not published")
        try:
            authorize_session_artifact_manifest(
                workspace_root,
                self.session_id,
                manifest_name,
            )
        except FileNotFoundError:
            # Live bridge sessions have no durable product projection yet.
            pass
        return AttachmentUploadResponse(attachments=handles)