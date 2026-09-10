from __future__ import annotations

import base64
import json
import subprocess
import sys
from collections.abc import Iterator

import pytest

import breadboard_engine.execution.author_worker as author_worker
from breadboard_engine.execution.author_worker import (
    AuthorWorker,
    AuthorWorkerCleanupResult,
    AuthorWorkerResourceReceipt,
    AuthorWorkerSpec,
    _ManagementNotices,
)


def test_close_allows_helper_to_emit_authenticated_cleanup_receipt() -> None:
    receipt = AuthorWorkerResourceReceipt(
        resource_id="docker:bb-author-test",
        owner_ref="module:session:worker",
        execution_id="execution-test",
        container_id="a" * 64,
        container_name="bb-author-test",
        image_id="sha256:" + "b" * 64,
        image_ref="sha256:" + "b" * 64,
        platform="linux/arm64",
        receiver_identity="receiver-test",
        state="running",
    )
    cleanup = {
        "status": "confirmed_absent",
        "resourceId": receipt.resource_id,
        "containerId": receipt.container_id,
        "ownerRef": receipt.owner_ref,
        "reason": "owner_channel_closed",
        "evidence": ["owned_container_removed", "container_absence_observed"],
    }
    encoded = base64.b64encode(
        json.dumps(cleanup, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    code = (
        "import sys; "
        "sys.stdin.buffer.read(); "
        f"sys.stderr.write('BREADBOARD_AUTHOR_CLEANUP\\t{encoded}\\n'); "
        "sys.stderr.flush()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    notices = _ManagementNotices(process)
    worker = AuthorWorker(process, receipt, notices)

    result = worker.close("server_shutdown")

    assert result.status == "confirmed_absent"
    assert result.resource_id == receipt.resource_id
    assert result.container_id == receipt.container_id
    assert result.owner_ref == receipt.owner_ref
    assert result.evidence == (
        "owned_container_removed",
        "container_absence_observed",
    )


def test_close_uses_authenticated_fallback_when_helper_receipt_is_missing() -> None:
    receipt = AuthorWorkerResourceReceipt(
        resource_id="docker:bb-author-fallback",
        owner_ref="module:session:fallback",
        execution_id="execution-fallback",
        container_id="c" * 64,
        container_name="bb-author-fallback",
        image_id="sha256:" + "d" * 64,
        image_ref="sha256:" + "d" * 64,
        platform="linux/arm64",
        receiver_identity="receiver-fallback",
        state="running",
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    notices = _ManagementNotices(process)
    calls: list[str] = []

    def fallback(reason: str, _deadline: float) -> AuthorWorkerCleanupResult:
        calls.append(reason)
        return AuthorWorkerCleanupResult(
            status="confirmed_absent",
            resource_id=receipt.resource_id,
            container_id=receipt.container_id,
            owner_ref=receipt.owner_ref,
            reason=reason,
            evidence=("fallback_authenticated_owned_container_removed",),
        )

    result = AuthorWorker(process, receipt, notices, fallback).close(
        "server_shutdown"
    )

    assert result.status == "confirmed_absent"
    assert result.evidence == ("fallback_authenticated_owned_container_removed",)
    assert calls == ["server_shutdown"]


def test_cleanup_fallback_authenticates_container_before_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = AuthorWorkerResourceReceipt(
        resource_id="docker:bb-author-owned",
        owner_ref="module:session:owned",
        execution_id="execution-owned",
        container_id="e" * 64,
        container_name="bb-author-owned",
        image_id="sha256:" + "f" * 64,
        image_ref="sha256:" + "f" * 64,
        platform="linux/arm64",
        receiver_identity="receiver-owned",
        state="running",
    )
    spec = AuthorWorkerSpec(
        owner_ref=receipt.owner_ref,
        execution_id=receipt.execution_id,
        execution_token="token-" + "a" * 64,
        image_ref=receipt.image_ref,
        platform=receipt.platform,
        command=("python3", "--stdio"),
        captured_staging_root="/owned/captured",
        staging_owner_ref=receipt.owner_ref,
    )
    inspection = json.dumps(
        [
            {
                "Id": receipt.container_id,
                "Image": receipt.image_id,
                "Config": {
                    "Labels": {
                        "dev.breadboard.author.owner": receipt.owner_ref,
                        "dev.breadboard.author.execution": receipt.execution_id,
                        "dev.breadboard.author.token": spec.execution_token,
                    }
                },
            }
        ]
    ).encode("utf-8")
    responses: Iterator[subprocess.CompletedProcess[bytes]] = iter(
        (
            subprocess.CompletedProcess([], 0, inspection, b""),
            subprocess.CompletedProcess([], 0, receipt.container_id.encode(), b""),
            subprocess.CompletedProcess(
                [],
                1,
                b"",
                b"Error: No such container: " + receipt.container_id.encode(),
            ),
        )
    )
    commands: list[list[str]] = []

    def command(
        _runtime: str,
        arguments: list[str],
        _deadline: float,
    ) -> subprocess.CompletedProcess[bytes]:
        commands.append(arguments)
        return next(responses)

    monkeypatch.setattr(author_worker, "_docker_command", command)

    result = author_worker._authenticated_cleanup_fallback(
        spec,
        receipt,
        "server_shutdown",
        float("inf"),
    )

    assert result.status == "confirmed_absent"
    assert result.evidence == (
        "fallback_authenticated_owned_container_removed",
        "container_absence_observed",
    )
    assert commands == [
        ["container", "inspect", receipt.container_id],
        ["rm", "--force", "--volumes", receipt.container_id],
        ["container", "inspect", receipt.container_id],
    ]
