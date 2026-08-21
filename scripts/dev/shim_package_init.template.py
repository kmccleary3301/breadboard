"""Compatibility shim: the engine package is now ``breadboard_engine``.

This file is the exact ``agentic_coder_prototype/__init__.py`` installed at
R-1 (the rename freeze event). Importing the legacy name:

1. applies the legacy-import policy to the root import itself (the physical
   package bypasses the alias finder, so the policy must fire here),
2. installs the prefix-wide alias finder (every ``agentic_coder_prototype.*``
   import resolves to the one canonical module object), and
3. self-replaces this package's ``sys.modules`` entry with the canonical
   package - the wrapper pattern proven by the R-0C matrix.

Policy: set ``BREADBOARD_LEGACY_IMPORTS=warn`` to deprecation-warn once per
process, or ``error`` to reject legacy imports outright.
"""

import sys

from breadboard_engine.compat.alias_import import announce_root_import, install

announce_root_import("agentic_coder_prototype")
install("agentic_coder_prototype", "breadboard_engine")

import breadboard_engine as _canonical  # noqa: E402

sys.modules[__name__] = _canonical
