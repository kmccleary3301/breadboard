#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
OUT_ROOT="${P15_CURSOR_OUT_ROOT:-$REPO_ROOT/docs_tmp/cli_phase_6/CODESIGN_p15/implementation_validation_p15_peer_parity_host_universality/artifacts/cursor_manual}"
OUT_ROOT="$(readlink -m "$OUT_ROOT")"
STAMP="$(date +%Y%m%d-%H%M%S)-$$-${RANDOM:-0}"
RUN_DIR="$OUT_ROOT/cursor_manual_$STAMP"
WORKSPACE="${BREADBOARD_CURSOR_WORKSPACE:-/tmp/bb-cursor-manual-$STAMP}"
CURSOR_BIN="${CURSOR_BIN:-cursor}"

mkdir -p "$RUN_DIR/screenshots" "$RUN_DIR/notes" "$WORKSPACE/src" "$WORKSPACE/docs"

cat > "$WORKSPACE/README.md" <<'WORKSPACE_EOF'
# BreadBoard Cursor Manual QC Workspace

This is an isolated dummy workspace for BreadBoard TUI manual QC in Cursor's integrated terminal.
Do not run the QC flow from the BreadBoard repository root.
WORKSPACE_EOF

cat > "$WORKSPACE/src/greeter.ts" <<'WORKSPACE_EOF'
export function greet(name: string): string {
  return `hello ${name}`
}
WORKSPACE_EOF

cat > "$WORKSPACE/docs/markdown_fixture.md" <<'WORKSPACE_EOF'
# Markdown Fixture

| Item | Status |
| --- | --- |
| Alpha | ready |
| Beta | pending |

- short bullet
- another bullet with `inline code`

```ts
export const value = 42
```
WORKSPACE_EOF

cat > "$RUN_DIR/env.json" <<EOF_JSON
{
  "createdAt": "$(date -Iseconds)",
  "workspace": "$WORKSPACE",
  "runDir": "$RUN_DIR",
  "cursorBin": "$(command -v "$CURSOR_BIN" 2>/dev/null || true)",
  "bb": "$(command -v bb 2>/dev/null || true)",
  "breadboard": "$(command -v breadboard 2>/dev/null || true)"
}
EOF_JSON

{
  echo "# Cursor Manual QC Versions"
  echo
  echo "## cursor --version"
  if command -v "$CURSOR_BIN" >/dev/null 2>&1; then "$CURSOR_BIN" --version || true; else echo "cursor not found"; fi
  echo
  echo "## bb --version"
  bb --version || true
  echo
  echo "## breadboard --version"
  breadboard --version || true
  echo
  echo "## uname"
  uname -a
} > "$RUN_DIR/version_probe.txt"

cat > "$RUN_DIR/QC_STEPS.md" <<EOF_STEPS
# Cursor Manual QC Steps

Run directory: \`$RUN_DIR\`
Workspace: \`$WORKSPACE\`

## Setup

1. Open Cursor on the generated workspace:

   \`\`\`bash
   $CURSOR_BIN "$WORKSPACE"
   \`\`\`

2. In Cursor's integrated terminal, start with a clean shell prompt and run:

   \`\`\`bash
   echo BB_CURSOR_PRE_ALPHA
   echo BB_CURSOR_PRE_BETA
   pwd
   bb
   \`\`\`

3. Capture screenshots or notes into:

   \`\`\`text
   $RUN_DIR/screenshots/
   $RUN_DIR/notes/
   \`\`\`

## Required Checks

- Startup shows the BreadBoard landing/composer/status surfaces without clearing \`BB_CURSOR_PRE_ALPHA\` or \`BB_CURSOR_PRE_BETA\` from reachable terminal history.
- Empty composer still shows the input bar and ready/status footer.
- Submit: \`Reply with exactly BB_CURSOR_SHORT_OK and no other words.\`
- Confirm the submitted prompt is visible exactly once and the assistant answer is visible after completion.
- Run \`/status\`, \`/model\`, \`/permissions\`, \`/diff\`, \`/transcript\`, \`/raw\`, \`/copy\`, \`/agents\`, \`/tasks\`, and \`?\`; each must either work or show a truthful disabled/bounded reason.
- Open \`@\` file picker, select \`docs/markdown_fixture.md\`, remove/re-add if supported, and confirm attachment state is readable.
- Ask for markdown rendering using the fixture, then shrink width to approximately 80 columns and grow back to 120+ columns.
- Confirm no duplicated landing pane, duplicated composer/footer, stale \`Log link available\`, \`file://logging/\`, or \`You are Codex\` leak appears.
- Force height shrink/grow while a long response or command overlay is visible; composer/footer must remain reachable.
- Use Ctrl+O transcript if available, Esc back to composer, then continue a new turn.
- If a tool call is triggered, confirm compact tool display is readable, raw details are inspectable, and no raw output floods the main transcript.
- Exit with Ctrl+D or \`/exit\`; terminal mode must recover and shell input must work.

## Result

Write the final result into \`$RUN_DIR/notes/result.md\` with:

- Cursor version/build.
- Terminal dimensions tested.
- Monitor/window movement, if any.
- Pass/fail for each required check.
- Screenshots or videos referenced by filename.
- Any S0/S1/S2/S3 issues using P15 severity.
EOF_STEPS

cat > "$RUN_DIR/summary.md" <<EOF_SUMMARY
# Cursor Manual QC Packet

Status: prepared

Workspace: \`$WORKSPACE\`

Run directory: \`$RUN_DIR\`

Open Cursor with:

\`\`\`bash
$CURSOR_BIN "$WORKSPACE"
\`\`\`

Then follow \`QC_STEPS.md\`.
EOF_SUMMARY

echo "$RUN_DIR"
