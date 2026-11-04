# BreadBoard pre-push checks

The hook is not enabled automatically and does not change Git configuration. To
activate it for this checkout, run `git config core.hooksPath .githooks` from the
repository root. Do not set a global `core.hooksPath` for this hook.

`BB_PREPUSH_SKIP=1 git push ...` explicitly skips the optional local fast checks.
It does not claim or perform a security scan. No allowlist, scanner, network
request, tool installation, or provider call is part of this hook.

For pushed refs, the hook checks the checked-out head and requires changed files
to be clean. It selects changed paths from each remote-OID..local-OID diff using
NUL-delimited names; a new branch uses the local
`refs/remotes/<remote>/main` merge base. Deleted paths select no checks. Push a
head from its own checkout and commit or stash edits first.

Changed Python files get `py_compile` syntax checks. The root `pyproject.toml`
does not configure a Python formatter or linter, so this hook adds neither. For
JavaScript/TypeScript files, the hook runs the nearest package's existing
Prettier or ESLint only when that package declares the tool; it does not invent
formatting rules or run package-wide TypeScript builds as changed-file checks.

Focused Python tests come from `.githooks/prepush-test-map.json` plus existing
sibling `test_<module>.py`, mirrored `tests/<module-parent>/test_<module>.py`,
and flat `tests/test_<module>.py` files. Changed test files select themselves.
There is no directory sweep or dynamic import guessing. This mapping is separate
from `scripts/ci/select_tests.py`: that CI selector chooses Python/full-suite
and Node package targets for GitHub Actions, while this hook selects individual
pytest files. It stays independent so this hook can be used without relying on
the separate selector change.

Syntax checks and focused tests share a 28-second deadline, leaving reporting
headroom below 30 seconds. Tests are admitted only with five seconds per file
remaining. If the S2 focused lock at
`/srv/km-migration/bb-s2-scratch/s2lock.sh` is executable, focused tests run as
`s2lock.sh s -- <pytest command>`; if it is absent or not executable, pytest runs
directly. A busy lock (exit 75), an expired test budget, or a focused-test spawn
error reports `FOCUSED-TESTS-NOT-RUN` and defers to CI. Actual test failures,
syntax failures, and fast-check timeouts block the push. The owned process group
is killed on timeout.

Use `python -m unittest discover -s tests -p test_prepush.py` for the focused
hook unit tests.
