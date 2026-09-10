from __future__ import annotations

import base64
import json
import subprocess
import sys

from breadboard_engine.execution.author_worker import (
    AuthorWorker,
    AuthorWorkerResourceReceipt,
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
