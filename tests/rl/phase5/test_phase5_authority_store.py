from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

import pytest

from scripts.rl_phase5 import phase5_authority_store as store_module

_IDENTITY_FIELDS = (
    "artifacts_identity",
    "events_identity",
    "key_identity",
    "root_identity",
    "state_identity",
)


def _open_store(root: Path, key: bytes) -> store_module.FileTrustStore:
    return store_module._open_deployment_store(
        root=root,
        deployment_key=key,
        expected_public_key_digest=store_module.FileTrustStore.public_key_digest_for(
            key
        ),
    )


@pytest.mark.parametrize("legacy", ["v2-numeric", "v3-numeric"])
def test_deployment_anchor_identities_are_decimal_and_numeric_anchor_fails_closed(
    tmp_path: Path, legacy: str
) -> None:
    key = secrets.token_bytes(32)
    root = tmp_path / "authority"
    _open_store(root, key)
    anchor_path = root / "anchor.json"
    anchor = json.loads(anchor_path.read_bytes())["anchor"]
    assert anchor["schema"] == "bb.rl.phase5.deployment-anchor.v3"
    root_metadata = root.stat(follow_symlinks=False)
    assert anchor["root_identity"] == [
        str(root_metadata.st_dev),
        str(root_metadata.st_ino),
    ]
    for field in _IDENTITY_FIELDS:
        assert [type(part) for part in anchor[field]] == [str, str]
    _open_store(root, key)

    forged = dict(anchor)
    if legacy == "v2-numeric":
        forged["schema"] = "bb.rl.phase5.deployment-anchor.v2"
    for field in _IDENTITY_FIELDS:
        forged[field] = [int(part) for part in anchor[field]]
    unsigned = store_module.FileTrustStore._json_bytes(forged)
    anchor_path.write_bytes(
        store_module.FileTrustStore._json_bytes(
            {
                "anchor": forged,
                "hmac": "hmac-sha256:"
                + hmac.new(key, unsigned, hashlib.sha256).hexdigest(),
            }
        )
    )
    with pytest.raises(ValueError, match="trust anchor mismatch"):
        _open_store(root, key)
