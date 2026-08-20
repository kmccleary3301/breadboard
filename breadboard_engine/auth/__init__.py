"""In-engine provider auth material (in-memory only).

This package is intentionally small and conservative:
- Auth material is accepted from the CLI bridge and stored in-memory only.
- No refresh tokens are persisted or handled here.
"""

from .material import EngineAuthMaterial, EmulationProfileRequirement
from .store import ProviderAuthStore, DEFAULT_PROVIDER_AUTH_STORE

__all__ = [
    "EngineAuthMaterial",
    "EmulationProfileRequirement",
    "ProviderAuthStore",
    "DEFAULT_PROVIDER_AUTH_STORE",
]

