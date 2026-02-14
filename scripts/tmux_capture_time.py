from __future__ import annotations

from datetime import datetime, timezone


def run_timestamp() -> str:
    """
    Stable timestamp used in capture run directory names.

    We use UTC to avoid CI/local timezone surprises. Format matches existing
    runbook examples: YYYYMMDD-HHMMSS.
    """

    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

