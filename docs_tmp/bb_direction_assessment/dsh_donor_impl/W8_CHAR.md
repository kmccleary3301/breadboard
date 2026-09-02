# W8.1 / CP-CHAR — Session compaction characterization

## Verdict

**Current main has no Session compaction owner.** `ContextWindowGuard.maybe_warn`
records a `context_window_warning` transcript and guardrail fact; it does not
compact, replace, spill, or delete context. A provider-free fixture crossed the
threshold three times and emitted zero compaction transitions. Public
`Session.restore` reconstructs lifecycle facts, not owner effective-context
bytes or C-Tree raw-fact identities. The first replay gap is trigger 1.

## Reproduction

Checkout: `/Users/kylemccleary/projects/breadboard-dsh-impl-w8`, campaign
baseline `b3cacc73`. Deterministic fixture: `/tmp/w8_compaction_characterize.py`.

```console
PYTHONPATH=. /opt/homebrew/bin/python3.11 /tmp/w8_compaction_characterize.py
```

Result: completed; three context warnings, zero compaction events, six owner
provider messages, and 15 owner C-Tree facts. The fixture snapshots canonical
owner bytes before each `maybe_warn` and compares them with public
`Session.restore`/`public_session_event`. It uses
`ContextWindowGuard(max_tokens=64, warn_ratio=0.5)`; canonical UTF-8 JSON bytes
and ordered C-Tree IDs are compared.

| attempt | warning | owner pre-context | owner raw IDs | differential |
|---:|---|---:|---|---|
| 1 | 167 tokens, ratio 2.61 | 2 messages / 733 bytes | `ctn_000001..ctn_000005` | 0 compactions; context/raw **FAIL**, first gap |
| 2 | 334 tokens, ratio 5.22 | 4 messages / 1,465 bytes | `ctn_000001..ctn_000010` | 0 compactions; equality not established |
| 3 | 501 tokens, ratio 7.83 | 6 messages / 2,197 bytes | `ctn_000001..ctn_000015` | 0 compactions; equality not established |

First exact gap:

```text
public Session.restore/public_session_event has no effective_context_bytes or raw_fact_identities
```

The public reconstruction is otherwise coherent: status `running`; seven
events; `session.started`, then three `input.accepted`/`assistant_message`
pairs. No public field carries effective context or C-Tree identity.

## Ownership card

- **Callers/owners:** `conductor/modes.py:get_model_response` calls
  `ContextWindowGuard.maybe_warn`; `SessionState.add_transcript_entry` and
  `_record_ctree` retain warnings and facts. Product `Session` owns logical
  replay; `SessionRuntime.read_session_event_batch` and `public_session_event`
  own public reconstruction. The TUI only projects incoming compaction names.
- **Inputs/outputs:** provider messages, token limits, transcript/C-Tree
  payloads, and `KernelEvent`s produce warnings, retained facts, `SessionView`,
  and public envelopes. No summary, continuation patch, replacement context,
  or compaction event exists.
- **Hidden work/dependencies:** token approximation, prompt insertion,
  transcript normalization, C-Tree identity generation, event folding,
  live/durable selection, SDK decoders, and TUI consumers.
- **Deleted:** none. Characterization adds no owner, schema, API, or seam.
- **Test surface:** existing kernel replay and guardrail-record tests do not
  cover repeated thresholds or compaction equality. W8.2 must add one narrow
  red behavioral test before implementation.

## Laws

- **E2.9:** retention remains domain-owned: engine `SessionState` retained six
  messages, three warnings, and 15 C-Tree facts; Product Session retained its
  typed logical facts.
- **E3.11:** lossless compaction is unproven. The first exact gap is absent
  public effective-context/raw-fact reconstruction at trigger 1.
