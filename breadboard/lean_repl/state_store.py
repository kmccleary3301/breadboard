"""State Store for Lean REPL state blobs.

This module provides Content-Addressable Storage (CAS) for Lean environment
and proof state pickles. State blobs are stored with content-based addressing
for deduplication and integrity verification.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import zstandard as zstd
from pathlib import Path
from typing import Optional

from .schema import StateBlob

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# State Store
# -----------------------------------------------------------------------------


class StateStore:
    """File-based content-addressable storage for Lean state blobs.

    State blobs are stored in a directory structure:
        {root}/
            {hash[:2]}/
                {hash[2:4]}/
                    {hash}.json

    This provides O(1) lookup and prevents directory from becoming too large.

    Attributes:
        root: Root directory for state storage
        compression_level: zstd compression level (1-22, higher = better compression)
    """

    def __init__(self, root: str, compression_level: int = 3):
        self.root = Path(root)
        self.compression_level = compression_level
        self.root.mkdir(parents=True, exist_ok=True)

        logger.info(f"StateStore initialized: root={root}, compression_level={compression_level}")

    def put(self, pickle_bytes: bytes, state_blob: StateBlob) -> str:
        """Store a state blob and return its CAS reference.

        Args:
            pickle_bytes: Raw pickle bytes from Lean
            state_blob: StateBlob metadata

        Returns:
            CAS reference (hash of compressed blob)
        """
        # Compress pickle bytes
        compressor = zstd.ZstdCompressor(level=self.compression_level)
        compressed = compressor.compress(pickle_bytes)

        # Encode to base64
        pickle_b64 = base64.b64encode(compressed).decode('ascii')

        # Create state blob with encoded pickle
        blob_with_pickle = StateBlob(
            format_version=state_blob.format_version,
            lean_toolchain=state_blob.lean_toolchain,
            mathlib_rev=state_blob.mathlib_rev,
            repl_rev=state_blob.repl_rev,
            header_hash=state_blob.header_hash,
            kind=state_blob.kind,
            pickle_bytes_b64=pickle_b64,
            created_ts=state_blob.created_ts,
        )

        # Compute content hash
        blob_json = blob_with_pickle.to_json()
        content_hash = hashlib.sha256(blob_json.encode()).hexdigest()

        # Store at hash-based path
        blob_path = self._get_blob_path(content_hash)
        blob_path.parent.mkdir(parents=True, exist_ok=True)

        with open(blob_path, 'w') as f:
            f.write(blob_json)

        logger.debug(f"Stored state blob: hash={content_hash[:16]}, kind={state_blob.kind}")

        return f"cas:sha256:{content_hash}"

    def get(self, ref: str) -> Optional[tuple[bytes, StateBlob]]:
        """Retrieve a state blob by CAS reference.

        Args:
            ref: CAS reference (e.g., "cas:sha256:abcd1234...")

        Returns:
            Tuple of (pickle_bytes, StateBlob) or None if not found
        """
        # Parse reference
        if not ref.startswith("cas:sha256:"):
            logger.warning(f"Invalid CAS reference format: {ref}")
            return None

        content_hash = ref.split(":")[-1]

        # Load blob
        blob_path = self._get_blob_path(content_hash)
        if not blob_path.exists():
            logger.debug(f"State blob not found: hash={content_hash[:16]}")
            return None

        try:
            with open(blob_path, 'r') as f:
                blob_json = f.read()

            # Deserialize
            blob = StateBlob.from_json(blob_json)

            # Decode and decompress pickle
            compressed = base64.b64decode(blob.pickle_bytes_b64)
            decompressor = zstd.ZstdDecompressor()
            pickle_bytes = decompressor.decompress(compressed)

            logger.debug(f"Retrieved state blob: hash={content_hash[:16]}, kind={blob.kind}")

            return (pickle_bytes, blob)

        except Exception as e:
            logger.exception(f"Error retrieving state blob {content_hash[:16]}: {e}")
            return None

    def delete(self, ref: str) -> bool:
        """Delete a state blob by CAS reference.

        Args:
            ref: CAS reference

        Returns:
            True if deleted, False if not found
        """
        if not ref.startswith("cas:sha256:"):
            return False

        content_hash = ref.split(":")[-1]
        blob_path = self._get_blob_path(content_hash)

        if blob_path.exists():
            blob_path.unlink()
            logger.debug(f"Deleted state blob: hash={content_hash[:16]}")
            return True

        return False

    def exists(self, ref: str) -> bool:
        """Check if a state blob exists.

        Args:
            ref: CAS reference

        Returns:
            True if exists, False otherwise
        """
        if not ref.startswith("cas:sha256:"):
            return False

        content_hash = ref.split(":")[-1]
        blob_path = self._get_blob_path(content_hash)
        return blob_path.exists()

    def _get_blob_path(self, content_hash: str) -> Path:
        """Get the filesystem path for a content hash.

        Uses sharded directory structure for performance:
            {hash[:2]}/{hash[2:4]}/{hash}.json

        Args:
            content_hash: SHA256 hash

        Returns:
            Path to blob file
        """
        shard1 = content_hash[:2]
        shard2 = content_hash[2:4]
        return self.root / shard1 / shard2 / f"{content_hash}.json"

    def get_stats(self) -> dict:
        """Get storage statistics.

        Returns:
            Dictionary with storage stats
        """
        total_blobs = 0
        total_size = 0

        for blob_file in self.root.rglob("*.json"):
            total_blobs += 1
            total_size += blob_file.stat().st_size

        return {
            "total_blobs": total_blobs,
            "total_size_bytes": total_size,
            "total_size_mb": total_size / (1024 * 1024),
            "root_path": str(self.root),
        }


# -----------------------------------------------------------------------------
# Global State Store Instance
# -----------------------------------------------------------------------------

_global_state_store: Optional[StateStore] = None


def get_state_store(root: Optional[str] = None, **kwargs) -> StateStore:
    """Get or create the global StateStore instance.

    Args:
        root: Root directory for state storage (defaults to /tmp/lean_state_store)
        **kwargs: Additional arguments for StateStore

    Returns:
        StateStore instance
    """
    global _global_state_store

    if _global_state_store is None:
        if root is None:
            root = "/tmp/lean_state_store"
        _global_state_store = StateStore(root, **kwargs)

    return _global_state_store


def reset_state_store() -> None:
    """Reset the global StateStore instance (for testing)."""
    global _global_state_store
    _global_state_store = None
