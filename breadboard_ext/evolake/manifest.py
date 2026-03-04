from __future__ import annotations

from breadboard.ext.interfaces import ExtensionManifest

MANIFEST = ExtensionManifest(
    ext_id="evolake",
    version="0.1.0",
    provides=("endpoints", "tools"),
    default_enabled=False,
    surfaces=("evolake.endpoints", "evolake.tools"),
    description="EvoLake extension endpoints",
    config_schema={
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean"},
            "router_prefix": {"type": "string", "default": "/ext/evolake"},
            "enable_endpoints": {"type": "boolean", "default": True},
            "enable_tools": {"type": "boolean", "default": True},
        },
        "additionalProperties": True,
    },
)
