from __future__ import annotations

from pathlib import Path

from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.runtime import build_runtime_signature


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def test_runtime_signature_is_stable_and_hash_addressed() -> None:
    package = load_env_package(PYTHON_TOY)
    sig1 = build_runtime_signature(package)
    sig2 = build_runtime_signature(package)

    assert sig1.digest().startswith("sha256:")
    assert sig1.digest() == sig2.digest()


def test_runtime_signature_changes_with_resource_class() -> None:
    package = load_env_package(PYTHON_TOY)

    assert build_runtime_signature(package, resource_class="cpu_local").digest() != build_runtime_signature(
        package,
        resource_class="cpu_large",
    ).digest()
