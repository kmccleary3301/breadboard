#!/usr/bin/env python3
"""
Shared timestamp utilities for tmux capture tooling.
"""

from __future__ import annotations

import time


def run_timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")
