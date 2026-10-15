"""Explicit PTY launch capability keeps the existing sandbox closed by default."""
from pathlib import Path

from breadboard_engine.security import launch_policy


LEGACY_DARWIN_POLICY = '(version 1)\n(deny default)\n(allow process-exec (subpath "/usr"))\n(allow process-fork)\n(allow signal (target self))\n(allow sysctl-read (sysctl-name-regex #"^hw\\."))\n(allow sysctl-read (sysctl-name "kern.hostname"))\n(allow sysctl-read (sysctl-name "kern.osrelease"))\n(allow sysctl-read (sysctl-name "kern.ostype"))\n(allow sysctl-read (sysctl-name "kern.version"))\n(allow file-read* (literal "/") (subpath "/bb-workspace") (subpath "/usr") (literal "/dev/null") (literal "/dev/zero") (literal "/dev/random") (literal "/dev/urandom") (literal "/dev/tty"))\n(allow file-write* (subpath "/bb-workspace") (literal "/dev/null") (literal "/dev/tty"))\n(deny file-read* (literal "/protected-secret") (subpath "/protected-secret"))\n(deny file-write* (literal "/protected-secret") (subpath "/protected-secret"))'


def test_pty_off_preserves_exact_darwin_policy_text():
    args = (Path('/bb-workspace'), (Path('/protected-secret'),), (Path('/usr'),))
    assert launch_policy._darwin_profile(*args) == LEGACY_DARWIN_POLICY
    assert launch_policy._darwin_profile(*args, pty=False) == LEGACY_DARWIN_POLICY


def test_pty_darwin_admits_only_devices_and_keeps_credential_denies():
    args = (Path('/bb-workspace'), (Path('/protected-secret'),), (Path('/usr'),))
    profile = launch_policy._darwin_profile(*args, pty=True)
    assert '(allow pseudo-tty)' in profile
    assert '(literal "/dev/ptmx")' in profile
    assert '(regex #"^/dev/ttys[^/]*$")' in profile
    assert '(allow file-ioctl (literal "/dev/ptmx") (regex #"^/dev/ttys[^/]*$"))' in profile
    assert '(subpath "/dev")' not in profile
    assert '(deny file-read* (literal "/protected-secret") (subpath "/protected-secret"))' in profile
    assert '(deny file-write* (literal "/protected-secret") (subpath "/protected-secret"))' in profile


def test_linux_launch_pty_argument_is_explicit_and_defaults_off(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.setattr(launch_policy.platform, 'system', lambda: 'Linux')
    import os
    import sys
    options = dict(workspace=workspace, shell=False, environment=os.environ)
    default, _ = launch_policy.build_restricted_process_command([sys.executable, '-I', '-c', 'pass'], **options)
    disabled, _ = launch_policy.build_restricted_process_command([sys.executable, '-I', '-c', 'pass'], pty=False, **options)
    enabled, _ = launch_policy.build_restricted_process_command([sys.executable, '-I', '-c', 'pass'], pty=True, **options)
    assert default == disabled
    assert '--pty' not in default
    assert enabled == (*default[:default.index('--')], '--pty', *default[default.index('--'):])


def test_linux_pty_grants_only_fixed_devpts_read_write_rules(monkeypatch):
    from breadboard_engine.security import linux_isolation

    rules = []
    monkeypatch.setattr(linux_isolation, "_syscall", lambda *args: 3)
    monkeypatch.setattr(linux_isolation, "_resolved_existing", lambda path: Path(path))
    monkeypatch.setattr(linux_isolation, "_landlock_add_path_rule",
                        lambda _libc, _fd, path, access: rules.append((path, access)))
    monkeypatch.setattr(linux_isolation.os, "close", lambda _fd: None)
    linux_isolation._apply_linux_landlock(Path("/workspace"), (), pty=False)
    assert all(path not in {Path("/dev/pts"), Path("/dev/ptmx")} for path, _ in rules)
    rules.clear()
    linux_isolation._apply_linux_landlock(Path("/workspace"), (), pty=True)
    pty_rules = [(path, access) for path, access in rules if path in {Path("/dev/pts"), Path("/dev/ptmx")}]
    assert [path for path, _ in pty_rules] == [Path("/dev/pts"), Path("/dev/ptmx")]
    expected_access = (
        linux_isolation._ACCESS_FS_READ_FILE
        | linux_isolation._ACCESS_FS_READ_DIR
        | linux_isolation._ACCESS_FS_WRITE_FILE
    )
    assert all(access == expected_access for _, access in pty_rules)
    assert all(path != Path("/dev") for path, _ in rules)
