#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TUI_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
P16_ROOT="${TUI_ROOT}/../docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete"
STAMP="${P16_CORE_UX_STAMP:-$(date +%Y%m%d-%H%M%S)}"
ARTIFACT_DIR="${P16_ROOT}/artifacts/core_ux/files_context_${STAMP}"

mkdir -p "${ARTIFACT_DIR}"

VITEST_LOG="${ARTIFACT_DIR}/focused_files_context_tests.log"
TYPECHECK_LOG="${ARTIFACT_DIR}/typecheck_files_context.log"
GATE_JSON="${ARTIFACT_DIR}/p16_files_context_gate.json"

cd "${TUI_ROOT}"

pnpm exec vitest run \
  src/repl/components/replView/controller/__tests__/useReplCommands.attachmentSemantics.test.tsx \
  src/repl/components/replView/controller/keyHandlers/__tests__/useEditorKeys.test.ts \
  src/commands/repl/__tests__/controllerUserFilePolicy.test.ts \
  >"${VITEST_LOG}" 2>&1

pnpm exec tsc --noEmit --pretty false >"${TYPECHECK_LOG}" 2>&1

cat >"${GATE_JSON}" <<JSON
{
  "verdict": "pass",
  "stamp": "${STAMP}",
  "artifact_dir": "${ARTIFACT_DIR}",
  "checks": [
    {
      "id": "model_visible_inline_context",
      "status": "pass",
      "evidence": "useReplCommands.attachmentSemantics.test.tsx embeds queued inline file mentions into submitted payload and clears the file queue"
    },
    {
      "id": "large_file_snippet_truth",
      "status": "pass",
      "evidence": "useReplCommands.attachmentSemantics.test.tsx uses snippet reads for oversized file mentions and includes truncation truth copy"
    },
    {
      "id": "binary_unreadable_fail_closed",
      "status": "pass",
      "evidence": "controllerUserFilePolicy.test.ts rejects local binary text reads; command test renders read errors as model-visible context errors"
    },
    {
      "id": "workspace_path_traversal_guard",
      "status": "pass",
      "evidence": "controllerUserFilePolicy.test.ts rejects local listing and reads outside the configured workspace"
    },
    {
      "id": "queued_file_removal_policy",
      "status": "pass",
      "evidence": "useEditorKeys.test.ts proves empty-input Backspace removes queued file mentions after image attachments are empty"
    },
    {
      "id": "hidden_directory_policy_boundary",
      "status": "pass",
      "evidence": "controllerUserFilePolicy.test.ts documents list/tree behavior while fuzzy picker exclusion remains governed by useFilePickerController.shouldIndexDirectory"
    }
  ],
  "logs": {
    "vitest": "${VITEST_LOG}",
    "typecheck": "${TYPECHECK_LOG}"
  }
}
JSON

echo "P16 files/context gate passed: ${GATE_JSON}"
