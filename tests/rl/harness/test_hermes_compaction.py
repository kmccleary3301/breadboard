from __future__ import annotations

import sys
from pathlib import Path

# Ensure pinned hermes source is available
_HERMES_SOURCE = Path.home() / ".cache/bb-compaction-e4/hermes-agent-939e45c91d751fadd94dcd1b873ac3cb44846213"
if str(_HERMES_SOURCE) not in sys.path:
    sys.path.insert(0, str(_HERMES_SOURCE))

from agent.context_compressor import ContextCompressor
from breadboard.rl.harness.hermes_worker import HermesActor
from breadboard.rl.harness.native_stream_profiles import HERMES_RESPONSE_CONSUMER_ID, NATIVE_STREAM_PROFILES






def test_hermes_stream_profile_configuration():
    """Verify HERMES_RESPONSE_CONSUMER_ID profile has correct compaction configuration."""
    profile = NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID]
    assert profile.implements_compaction_phases is False
    assert profile.compaction_checkpoints == frozenset()
    assert profile.compaction_in_source is True
    assert profile.compaction_overflow_attempts == 3
    assert profile.compaction_summary_system_prompt is None
    assert profile.phase_mode == "checkpointed"




def test_hermes_compaction_threshold_formula_parity():
    """Verify stock Hermes threshold calculation:
    1. _effective_threshold_percent floors at 0.75 for windows < 512K:
       max(0.50, 0.75) = 0.75
    2. _compute_threshold_tokens with max_tokens=2048:
       effective_window = 131072 - 2048 = 129024
       pct_value = int(129024 * 0.75) = 96768
       floored = max(96768, MINIMUM_CONTEXT_LENGTH=64000) = 96768
    """
    max_tokens = 2048
    cc = ContextCompressor(
        model="test-model",
        threshold_percent=0.5,
        max_tokens=max_tokens,
    )
    cc.context_length = 131072

    assert cc.threshold_percent == 0.75
    assert cc.threshold_tokens == 96768

    # Verify should_compress behavior against threshold
    assert cc.should_compress(prompt_tokens=96767) is False
    assert cc.should_compress(prompt_tokens=96768) is True

def test_compaction_disabled_by_default():
    """Verify HermesActor initializes with compaction disabled and stays disabled."""
    actor = HermesActor(channel=None)
    assert actor._compaction_enabled is False
    assert actor._summary_requests == 0

