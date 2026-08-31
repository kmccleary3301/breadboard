from __future__ import annotations

from pathlib import Path
from typing import Final

import breadboard

SOURCE_ROOT: Final = Path(breadboard.__file__).resolve().parents[1]
