# Credential Security Contract

## Authority and scope

This contract governs provider credentials handled by the provider broker, provider-auth API routes, runtimes, Ray actors, sandboxes, model-controlled subprocesses, logs, snapshots, and artifact evidence. It describes the current SQLite branch. It does not authorize reading or migrating real credentials, changing the storage format, or introducing Keychain storage; those actions require the product playbook's human gate.

Secret assets include API keys, OAuth access and refresh tokens, authorization headers, PKCE verifiers, device-flow values, and broker bearer URLs or tokens. Credential paths and SQLite sidecar paths are sensitive locators. Provider IDs, account IDs, labels, status, expiry, selection origin, binding, and fixed audit fields are non-secret only when returned through typed broker views.

## Trust boundaries

1. **Broker process:** trusted to open the store, lease material, refresh credentials, and redact failures.
2. **Local API and canonical TUI:** trusted control-plane clients. `BREADBOARD_API_TOKEN` protects the whole API when configured. Without it, credential and login-state reads and mutations plus model-role resolution require a loopback Host and client; cross-site browser requests are rejected. Model-controlled network paths independently deny loopback and private destinations.
3. **Model-controlled execution:** untrusted. This includes shell, LSP/compiler, MCP stdio server, Codex app-server, evaluator, receipt, and artifact operations.
4. **Ray serialization and actor startup:** never a secret transport. Trusted parents capture protected path locators before environment sanitation and pass them explicitly.
5. **Workspace and evidence filesystems:** attacker-controlled wherever a model can create, rename, link, or retain a background process.
6. **Owning OS account and root:** outside the SQLite confidentiality guarantee. They can read plaintext files or broker memory.
7. **Remote credential transport:** not implemented. Configuring it fails closed instead of falling back locally.

## Required invariants

### Storage

- The default database is `~/.breadboard/credentials.sqlite3`. `BREADBOARD_CREDENTIAL_STORE_PATH` or `BREADBOARD_CREDENTIAL_DB` selects an explicit database; `BREADBOARD_STATE_DIR` selects the state directory when no explicit database is set.
- The final state directory is owned by the current user and mode `0700`. The database and existing `-wal`, `-shm`, and `-journal` files are mode `0600`.
- Path components are opened descriptor-relative with no-follow semantics. Unsafe ownership or writable ancestors, symlinks, hardlinked databases, non-regular files, and platforms missing required no-follow primitives are refused with typed, secret-free errors.
- SQLite owns transactions and journal recovery. BreadBoard does not raw-copy or restore database bytes during migration.
- Secret rows and inspectable metadata are separate tables in one plaintext database. This is not encryption.

### Secret lifetime and state transitions

- Material leaves the broker only through a bounded execution lease carrying account identity and secret version. It is released after use.
- Rotation inserts the new version and deletes superseded secret rows transactionally.
- Logout disables selection, releases leases and refresh ownership, and retains the secret row so the disabled account can be re-enabled. Its audit event records `secret_disposition=retained`; disabled material remains plaintext residual until rotation or revoke.
- Revoke deletes secret rows, clears refresh ownership, and retains a tombstone that cannot be reactivated. Its audit event records `secret_disposition=revoked`; re-login creates a new account identity.
- OAuth refresh uses durable single-flight ownership, expected-version comparison, bounded leases, stale-owner recovery, and compare-and-swap commit.
- Transient and definitive refresh failures remain distinct and expose neither provider response bodies nor tokens.
- OAuth login sessions expire within ten minutes. Terminal, cancelled, failed, expired, and migrated stale sessions retain no flow payload.
- Row deletion is not secure erasure. Values can remain in free pages, snapshots, storage media, or backups.

### Selection and origin

- Source precedence is explicit and tested; it never depends on row order.
- Account, credential, and label selectors resolve deterministically.
- Session bindings persist only non-secret identity and reason. Restart preserves a binding; rotation preserves account identity.
- Expired, disabled, revoked, rate-limited, missing-secret, or refresh-blocked accounts are not usable material.
- Authentication or security failure never masquerades as model fallback.
- Every resolution exposes a non-secret origin record without material.

### Process and environment isolation

- Credential values, provider-secret variables, locator variables, and full remote broker URLs are removed from child environments. A non-secret configured sentinel may cross only to force remote-broker failure closed.
- Protected locations include all of `~/.breadboard` and `~/.codex`, configured state/database paths, SQLite sidecars, and programmatically registered paths.
- Trusted parents capture custom paths before Ray sanitation. Sandboxes, LSP managers, MCP managers, evaluators, and provider subprocesses receive that set or the process-wide registered union.
- Model-controlled host subprocesses, including MCP stdio servers, launch only through `build_restricted_process_command`. Missing enforcement produces a secret-free unavailable error with no unisolated retry.
- The Codex app-server runs with read-only sandboxing and `approvalPolicy=never`. Any unexpected command-execution or file-change approval request cancels the turn; it is never accepted implicitly.
- macOS permits workspace writes, ancestor metadata traversal, bounded `sysctl-read` access for `hw.*` and the exact `kern.hostname`, `kern.osrelease`, `kern.ostype`, and `kern.version` names, plus required system/toolchain reads. It denies protected paths, network, unrestricted Mach lookup and POSIX IPC, and broad device access. Because the macOS sandbox cannot mediate every same-user process-metadata interface, model-controlled process launch fails closed when provider credentials or the control-plane token were present in the host startup environment; those credentials must instead enter through the broker store or local TUI after startup.
- Linux requires Landlock ABI v3 or newer so truncate operations are mediated, and uses seccomp to deny process-memory inspection, sockets, BPF/performance inspection, `pidfd_getfd`, and `io_uring_setup`. Its absolute isolated helper consumes read roots captured by the parent.
- Before process launch, the workspace and protected trees are checked for shared regular-file inodes; the sandbox then denies creation of new protected hardlinks. Host workspace I/O holds root descriptors, rejects symlink and hardlink targets, verifies inode identity, and uses atomic descriptor-relative writes.
- Evidence and disposable artifact roots must not overlap the workspace, template, export, or protected paths. Template copying, cleanup, traversal, hashing, and evidence copying remain anchored to trusted descriptors; unowned directories are never overwritten.

### Network and local control

- Model-controlled host subprocesses have no network capability. Docker model sandboxes always use `--network none`; configured non-`none` modes, including host networking, are rejected rather than treated as supported options.
- Web scraping admits only public `http` and `https` destinations. Literal and resolved loopback, private, link-local, multicast, unspecified, and reserved addresses are refused. Each connection uses only its vetted DNS result and revalidates the connected peer. Redirects are validated before connection. Browser DNS and direct networking are disabled; GET and HEAD requests are fulfilled through the same pinned path, while service workers, WebSocket, EventSource, WebRTC, WebTransport, and QUIC are disabled.
- Without a bearer token, credential and login-state reads and mutations plus model-role resolution require loopback Host and client values and reject cross-site fetch metadata or non-loopback Origin values. This blocks DNS rebinding, remote Host spoofing, and browser CSRF at the credential boundary.
- The built-in Uvicorn entry point refuses every direct non-loopback bind unless both `BREADBOARD_API_TOKEN` and `BREADBOARD_ALLOW_INSECURE_REMOTE=1` are set. That pair is an explicitly unsupported insecure override because the built-in server provides no TLS. Supported exposure requires a local bind behind a TLS-terminating protected channel; no direct remote-bind credential-security or release claim exists.
- OAuth token endpoints are fixed HTTPS catalog entries using normal TLS verification.
- A future remote broker requires authenticated protected transport with TLS or an equivalent local channel. No remote/TLS support claim exists today.

### Redaction, audit, and errors

- Public views never contain material, authorization-header values, refresh-owner IDs, or raw provider responses.
- Material enters the redaction scope before operations that can log, serialize, or raise it.
- Commands, environments, subprocess output, runtime failures, audit payloads, and evidence are scrubbed before persistence or return.
- Durable audit events use fixed non-secret fields. Each credential or login-state mutation and its audit append commit in one SQLite transaction; an audit persistence failure rolls back the mutation. Arbitrary exception text and secret-bearing input are forbidden.
- Errors describe operation and class, not secret value, raw response, file content, or command output.
- Redaction is defense in depth. It never authorizes sending a secret to an untrusted process.

## Threat and control matrix

| Threat | Required control | Residual posture |
|---|---|---|
| Another OS user reads the store | Owner-only directory/files; unsafe ownership refused | Root, ACL authorities, and privileged backup agents remain authoritative |
| Model reads a direct or linked credential | Protected registry, no-follow I/O, macOS sandbox or Linux Landlock/seccomp | Unsupported enforcement fails closed |
| Model inspects sibling memory | Deny ptrace, process-memory, task-port/Mach lookup, and related inspection | Native malware already running as the owner is outside this boundary |
| Secret crosses Ray, argv, env, or stdin | Parent-captured locators, allowlisted environment, value rejection | Non-secret sentinels can reveal broker presence |
| Workspace/evidence path is swapped | Held descriptors, no-follow components, inode/link checks, atomic writes | Kernel or host filesystem compromise is out of scope |
| Crash leaves OAuth or refresh residue | Bounded leases, initialization cleanup, stale-owner recovery | Plaintext remnants may remain in SQLite pages or snapshots |
| Rotation or revoke resurrects material | Transactional deletion, versioned CAS, durable tombstones | Physical erasure is not claimed |
| Log or evidence captures a secret | Narrow outputs, redaction scopes, fixed audit schema, descriptor-safe copying | Bypassing these seams is a defect, not an accepted path |
| Scraper reaches local credential routes | Public-address admission, redirect/browser guards, loopback Host enforcement | General native same-user software remains trusted |
| Remote config falls back locally | Configured-remote sentinel and fail-closed resolution | Remote broker use remains unavailable |
| Backup exposes plaintext | Restrictive live-file modes and honest posture | Backup encryption and retention are operator responsibilities |

## Accepted residual risks

The SQLite branch accepts these limitations, not as encryption claims:

- the owning OS user, root, endpoint security software, and native same-user malware can read the database or broker memory;
- backups, snapshots, crash dumps, swap, deleted SQLite pages, disabled credential rows, and storage media can retain plaintext;
- provider tokens necessarily exist in broker/runtime memory during an authorized request;
- macOS can expose same-user process metadata outside Sandbox mediation, so startup-environment credentials force model-process launch to fail closed;
- Docker and host isolation trust the host kernel and enforcement primitives;
- direct remote credential transport is unavailable rather than partially supported.

This posture is acceptable only for a single-user local workstation where model-controlled execution—not the owning user—is the adversary. A multi-user service, shared daemon, regulated deployment, or environment requiring protection from the owning account does not satisfy this contract.

## Keychain or encryption branch trigger

Stop and open the human-gated migration branch before claiming support that requires:

- confidentiality from another process running as the owning user;
- shared or multi-user service operation;
- managed at-rest encryption, hardware-backed keys, escrow, or regulated retention;
- automatic protection of backups, snapshots, swap, or crash dumps;
- remote credential custody;
- a policy forbidding plaintext credential databases regardless of mode.

That branch must define format versioning, rollback, interrupted migration, old-store disposal, Keychain denial behavior, and backup interaction. Tests use synthetic credentials only; no real credential is copied silently.

## Review and release gates

Exact-head acceptance requires:

1. database, state-directory, sidecar, symlink, hardlink, FIFO, ownership, permission, and migration tests;
2. canaries for environment, argv, stdin, logs, failures, Ray, snapshots, evidence, direct/linked reads, process memory, and loopback network;
3. deterministic precedence, selection, binding, expiry, rate-limit, logout, revoke, rotation, refresh concurrency, crash, restart, and migration tests;
4. actual macOS isolation smoke evidence and real Linux Landlock/seccomp evidence on the release kernel class;
5. independent review of this contract and exact implementation with no unresolved P0, P1, or unapproved P2.

Changes to storage, permissions, redaction, protected-path propagation, process isolation, auth routes, refresh/revoke semantics, or remote transport invalidate affected evidence and require fresh review.
