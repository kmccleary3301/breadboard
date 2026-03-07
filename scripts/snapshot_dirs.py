#!/usr/bin/env python3
from __future__ import annotations

import os
from typing import List


def split_snapshot_dirs(raw: str) -> List[str]:
    value = (raw or "").strip()
    if not value:
        return []
    if "," in value:
        parts = value.split(",")
    elif os.pathsep in value:
        parts = value.split(os.pathsep)
    else:
        parts = [value]

    result: List[str] = []
    seen = set()
    for part in parts:
        item = part.strip()
        if not item or item in seen:
            continue
        result.append(item)
        seen.add(item)
    return result
