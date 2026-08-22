from __future__ import annotations

import hashlib
import json
from pathlib import Path

FIXTURES = Path(__file__).parents[2] / "fixtures" / "rl" / "harness" / "wp11"


def test_wp11_frozen_migration_fixtures_are_canonical_and_nonexecuting() -> None:
    parity = (FIXTURES / "v1_v2_shadow_vectors.json").read_bytes()
    catalog = (FIXTURES / "config_native_catalog.json").read_bytes()
    assert hashlib.sha256(parity).hexdigest() == "0cd1ebdc6487c8736637c2bd6a5843a61d984c9b3935c8be684795e951921a9d"
    assert hashlib.sha256(catalog).hexdigest() == "9f1942ae319d791ae89f80b5bf3a3246db2aecd49747f878473e5d32faa69899"
    parity_doc = json.loads(parity)
    catalog_doc = json.loads(catalog)
    assert parity_doc["executions"] == 0
    assert {row["name"] for row in catalog_doc["entries"]} >= {"terminal-like", "swe-like", "generated-zeta-unknown"}
