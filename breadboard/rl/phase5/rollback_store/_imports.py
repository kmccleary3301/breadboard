from __future__ import annotations

import base64
import binascii
import ctypes
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import threading
import sys
from typing import Any, Iterator, Mapping, Protocol, Sequence
import uuid


def _package_callable(name: str, local: Any) -> Any:
    package = sys.modules.get(__package__)
    candidate = getattr(package, name, local) if package is not None else local
    return local if candidate is local else candidate


def _package_limit(name: str, local: int) -> int:
    package = sys.modules.get(__package__)
    candidate = getattr(package, name, local) if package is not None else local
    return candidate

def _package_value(name: str, local: Any) -> Any:
    package = sys.modules.get(__package__)
    return getattr(package, name, local) if package is not None else local



__all__ = [
    "Any", "Enum", "Iterator", "Mapping", "Path", "Protocol", "Sequence",
    "base64", "binascii", "contextmanager", "ctypes", "dataclass", "fcntl",
    "hashlib", "hmac", "json", "os", "re", "stat", "sys", "threading",
    "uuid", "_package_callable", "_package_limit", "_package_value",
]
