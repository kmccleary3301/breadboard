from __future__ import annotations

from pathlib import Path
import sysconfig

from .model import HarnessDefinition
from .validate import load_harness_definition


_TEMPLATE_RELATIVE_PATH = Path("agent_configs/templates/minimal_harness.v3.yaml")


def minimal_template_path() -> Path:
    """Return the canonical template path in a checkout or installed distribution."""
    candidates = (Path(__file__).resolve().parents[3] / _TEMPLATE_RELATIVE_PATH, Path(sysconfig.get_path("data")) / _TEMPLATE_RELATIVE_PATH)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"minimal Harness Definition template not found; searched: {', '.join(map(str, candidates))}")

def minimal_template_text() -> str:
    """Return the checked-in canonical template without transforming it."""
    return minimal_template_path().read_text(encoding="utf-8")

def load_minimal_harness() -> HarnessDefinition:
    """Load and strictly validate the canonical minimal Harness Definition."""
    return load_harness_definition(minimal_template_path())
