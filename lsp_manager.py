# Deprecated shim: forward to v2 where feasible
from kylecode.lsp_manager_v2 import *  # type: ignore

# Backwards-compat alias: legacy tests import LSPManager
# Expose LSPManager name bound to v2 manager class
try:
    LSPManager = LSPManagerV2  # type: ignore[name-defined]
except Exception:
    pass


