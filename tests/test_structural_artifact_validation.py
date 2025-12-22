from __future__ import annotations

from types import SimpleNamespace

from agentic_coder_prototype.conductor_patching import validate_structural_artifacts


class _StubSessionState:
    def __init__(self) -> None:
        self._meta = {"requires_build_guard": True}

    def get_provider_metadata(self, key: str, default=None):
        return self._meta.get(key, default)


def test_validate_structural_artifacts_accepts_fs_convention(tmp_path) -> None:
    (tmp_path / "fs.h").write_text("#ifndef FS_H\n#define FS_H\n#endif\n", encoding="utf-8")
    (tmp_path / "fs.c").write_text('#include "fs.h"\n', encoding="utf-8")
    (tmp_path / "test.c").write_text('#include "fs.h"\nint main(void){return 0;}\n', encoding="utf-8")
    (tmp_path / "filesystem.fs").write_bytes(b"\x00")

    conductor = SimpleNamespace(workspace=str(tmp_path))
    issues = validate_structural_artifacts(conductor, _StubSessionState())
    assert issues == []


def test_validate_structural_artifacts_accepts_protofilesystem_header(tmp_path) -> None:
    (tmp_path / "protofilesystem.h").write_text(
        '#ifndef PROTOFILESYSTEM_H\n#define PROTOFILESYSTEM_H\n#define FS_FILENAME "filesystem.fs"\n#endif\n',
        encoding="utf-8",
    )
    (tmp_path / "fs.c").write_text('#include "protofilesystem.h"\n', encoding="utf-8")
    (tmp_path / "test.c").write_text('#include "protofilesystem.h"\nint main(void){return 0;}\n', encoding="utf-8")

    conductor = SimpleNamespace(workspace=str(tmp_path))
    issues = validate_structural_artifacts(conductor, _StubSessionState())
    assert issues == []

