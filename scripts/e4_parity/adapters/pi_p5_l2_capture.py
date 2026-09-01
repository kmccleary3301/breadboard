from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


from breadboard.product.evidence.e4.adapters import pi_p5_l2_capture as _owner

if __name__ == "__main__":
    raise SystemExit(_owner.main())


sys.modules[__name__] = _owner
