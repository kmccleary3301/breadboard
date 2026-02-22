import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const scriptDir = dirname(fileURLToPath(import.meta.url))
const webappRoot = resolve(scriptDir, "..")
const sessionRunnerPath = resolve(webappRoot, "../agentic_coder_prototype/api/cli_bridge/session_runner.py")
const sessionCommandsPath = resolve(webappRoot, "src/sessionCommands.ts")
const appPath = resolve(webappRoot, "src/App.tsx")

const REQUIRED_CANONICAL_COMMANDS = ["list_checkpoints", "restore_checkpoint", "permission_decision", "stop"]
const FORBIDDEN_ALIASES = ["checkpoint_list", "checkpoints_list", "checkpoint_restore", "checkpoint_restored"]
const ALLOWED_LEGACY_FALLBACKS = ["permission_revoke"]

const extractCommands = (text) =>
  [...text.matchAll(/command:\s*"([^"]+)"/g)].map((match) => String(match[1]))

const backendText = readFileSync(sessionRunnerPath, "utf-8")
const webappCommandText = `${readFileSync(sessionCommandsPath, "utf-8")}\n${readFileSync(appPath, "utf-8")}`

const backendCommands = new Set([...backendText.matchAll(/case\s+"([^"]+)":/g)].map((match) => String(match[1])))
const emittedCommands = [...new Set(extractCommands(webappCommandText))]

const missingFromBackend = REQUIRED_CANONICAL_COMMANDS.filter((command) => !backendCommands.has(command))
const missingFromWebapp = REQUIRED_CANONICAL_COMMANDS.filter((command) => !emittedCommands.includes(command))
const forbiddenPresent = FORBIDDEN_ALIASES.filter((command) => emittedCommands.includes(command))
const unsupportedWebappCommands = emittedCommands.filter(
  (command) => !backendCommands.has(command) && !ALLOWED_LEGACY_FALLBACKS.includes(command),
)

if (missingFromBackend.length > 0 || missingFromWebapp.length > 0 || forbiddenPresent.length > 0 || unsupportedWebappCommands.length > 0) {
  console.error("backend/webapp command inventory verification failed")
  console.error(
    JSON.stringify(
      {
        missingFromBackend,
        missingFromWebapp,
        forbiddenPresent,
        unsupportedWebappCommands,
        emittedCommands,
      },
      null,
      2,
    ),
  )
  process.exit(1)
}

console.log(
  JSON.stringify(
    {
      status: "ok",
      requiredCanonicalCommands: REQUIRED_CANONICAL_COMMANDS,
      emittedCommands,
      backendCommandCount: backendCommands.size,
    },
    null,
    2,
  ),
)
