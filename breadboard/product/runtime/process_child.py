"""Private, standard-library-only launcher for durable process children."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path


def entry_command(executable: str = sys.executable) -> tuple[str, ...]:
    if getattr(sys, "frozen", False):
        return executable, "--process-child"
    return executable, str(Path(__file__).resolve())


def main(arguments: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if arguments is None else arguments)
    if len(args) == 3 and args[0] == "--guardian":
        while not os.path.exists(args[1]):
            time.sleep(0.01)
        return 0
    if len(args) < 9:
        raise ValueError(
            "process child requires its retained control paths and command"
        )
    (
        _target_ref,
        group_token,
        release,
        task_path,
        status,
        guard,
        result_path,
        raw_limit,
        *command,
    ) = args
    result_limit = int(raw_limit)
    while not os.path.exists(release):
        time.sleep(0.01)
    with open(task_path, "rb") as stream:
        task = stream.read()
    guardian = subprocess.Popen((*entry_command(), "--guardian", guard, group_token))
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    def reset_term() -> None:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    if result_path:
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            preexec_fn=reset_term,
        )
        assert child.stdin is not None and child.stdout is not None
        child.stdin.write(task)
        child.stdin.close()
        output = child.stdout.read(result_limit + 1)
        if len(output) > result_limit:
            child.kill()
            child.wait()
            result = 1
            output = b'{"status":"failed","exit_code":null,"stdout":"","stderr":"","problem":{"code":"worker_result_too_large","message":"worker result exceeded bounded output"},"execution_evidence":[]}'
        else:
            result = child.wait()
        temporary = f"{result_path}.{os.getpid()}.tmp"
        with open(temporary, "wb") as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, result_path)
    else:
        result = subprocess.run(command, input=task, preexec_fn=reset_term).returncode
    group = os.getpgrp()
    while True:
        try:
            rows = subprocess.check_output(
                ["ps", "-axo", "pid=,pgid=,stat="], text=True, start_new_session=True
            ).splitlines()
        except (OSError, subprocess.CalledProcessError):
            time.sleep(0.01)
            continue
        live = False
        for row in rows:
            fields = row.strip().split()
            if (
                len(fields) >= 3
                and int(fields[0]) not in {os.getpid(), guardian.pid}
                and int(fields[1]) == group
                and not fields[2].startswith("Z")
            ):
                live = True
                break
        if not live:
            break
        time.sleep(0.01)
    temporary = f"{status}.{os.getpid()}.tmp"
    with open(temporary, "wb") as stream:
        stream.write(str(result).encode("ascii"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, status)
    descriptor = os.open(guard, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fsync(descriptor)
    os.close(descriptor)
    directory = os.open(os.path.dirname(status), os.O_RDONLY)
    os.fsync(directory)
    os.close(directory)
    guardian.wait()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
