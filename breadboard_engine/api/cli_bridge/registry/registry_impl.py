from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import math
import os
import secrets
import time
import tempfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Iterable, Optional, Tuple, TypeVar

from ..engine_identity_config import EngineProcessIdentity, LaunchBootstrapVerifier
from ..events import EventType, SessionEvent, replay_retention_facts
from ..models import (
    BeginControlDrainRequest, BootstrapChallengeRequest, BootstrapChallengeResponse,
    ClientLeaseRequest, ClientRegisterRequest, ClientRegistrationResponse,
    DrainControlRequest, DrainControlResponse, GracefulControlResultRequest,
    HardSignalCommitRequest, HardSignalPreparationResponse, HardSignalPermitResponse,
    HardSignalOutcomeRequest, HardSignalPrepareRequest, OwnerAcquireRequest,
    OwnerLeaseRequest, OwnerLeaseResponse, SessionStatus, SessionSummary,
    TurnAdmission,
)

from .authority_owner import OwnerAuthorityMixin
from .authority_drain import DrainAuthorityMixin
from .persistence import PersistenceMixin
from .records import CONTROL_REQUEST_ID_CAPACITY, SessionRecord


class SessionRegistry(OwnerAuthorityMixin, DrainAuthorityMixin, PersistenceMixin):
    """Registry with optional atomic, secret-safe retained session state."""

    def __init__(
        self,
        state_root: str | Path | None = None,
        *,
        process_identity: EngineProcessIdentity | None = None,
        bootstrap_verifier: LaunchBootstrapVerifier | None = None,
        clock: Callable[[], float] = time.time,
        control_request_capacity: int = CONTROL_REQUEST_ID_CAPACITY,
    ) -> None:
        self._records: Dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()
        self._authority_lock = asyncio.Lock()
        self._process_identity = process_identity
        self._bootstrap_verifier = bootstrap_verifier
        self._clock = clock
        if (
            isinstance(control_request_capacity, bool)
            or not isinstance(control_request_capacity, int)
            or not 1 <= control_request_capacity <= CONTROL_REQUEST_ID_CAPACITY
        ):
            raise ValueError("control request capacity is outside the supported range")
        self._control_request_capacity = control_request_capacity
        self._owner: _OwnerLease | None = None
        self._registrations: Dict[str, _ClientRegistration] = {}
        self._registration_by_client: Dict[str, str] = {}
        self._client_generation: Dict[str, int] = {}
        self._admission_epoch = 0
        self._session_admission_open = True
        self._turn_admission_open = True
        self._registrations_open = True
        self._drain_generation = 0
        self._drain: _DrainState | None = None
        self._control_request_ids: set[str] = set()
        self._state_root = Path(state_root).resolve() if state_root is not None else None
        if self._state_root is not None and self._state_root.exists():
            if not self._state_root.is_dir():
                raise NotADirectoryError(self._state_root)
            self._load_retained_records()

