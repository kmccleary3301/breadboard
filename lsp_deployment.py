"""Compatibility shim for tests and legacy imports.

Expose `breadboard.lsp_deployment` as a top-level module name so test patches like
`@patch("lsp_deployment.LSPManagerV2")` target the real implementation.
"""

from __future__ import annotations

import sys

from breadboard import lsp_deployment as _impl

# Ensure `import lsp_deployment` returns the same module object as
# `import breadboard.lsp_deployment`.
sys.modules[__name__] = _impl

