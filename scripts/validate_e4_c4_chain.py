#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agentic_coder_prototype.conformance.c4_chain import *  # noqa: E402,F403
from agentic_coder_prototype.conformance.c4_chain import (  # noqa: E402
    diff_comparator_reports as _diff_comparator_reports,
    main,
)


if __name__ == "__main__":
    raise SystemExit(main())
