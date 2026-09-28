from __future__ import annotations

import asyncio
import sys

import pytest

from breadboard.rl.harness.native_session import NativeSession, NativeSessionError


def test_failed_owned_retirement_is_bounded_on_repeated_close() -> None:
    async def scenario() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            "import time; print('ready', flush=True); time.sleep(30)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        session = NativeSession(process, retire_callback=lambda: False)
        try:
            assert process.stdout is not None
            assert await asyncio.wait_for(process.stdout.readline(), 2) == b"ready\n"
            for _ in range(2):
                with pytest.raises(NativeSessionError) as raised:
                    await asyncio.wait_for(session.close(), 2)
                assert raised.value.code == "native_retire_failed"
        finally:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), 2)

    asyncio.run(scenario())
