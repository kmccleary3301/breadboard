#!/usr/bin/env tsx
import { createReadStream, existsSync } from "node:fs"
import { spawnSync } from "node:child_process"

type InputBusCommand = {
  readonly keys?: string
  readonly special?: ReadonlyArray<string>
  readonly enter?: boolean
  readonly newline?: boolean
  readonly send_mode?: "submit" | "multiline" | "literal"
  readonly sendMode?: "submit" | "multiline" | "literal"
  readonly delayMs?: number
  readonly enterDelayMs?: number
}

const argv = process.argv.slice(2)
let session = "breadboard-live"
let fifo = `/tmp/${session}-input.fifo`
let targetWindow = "0"
let verbose = false
let defaultEnterDelayMs = 15
let submitKey = "C-m"
let newlineKey = "C-j"
let newlinePrefix = ""
let submitKeySetByArg = false
let newlineKeySetByArg = false
let newlinePrefixSetByArg = false

for (let i = 0; i < argv.length; i += 1) {
  const arg = argv[i]
  if (arg === "--session") {
    session = argv[++i]
    fifo = `/tmp/${session}-input.fifo`
  } else if (arg === "--fifo") {
    fifo = argv[++i]
  } else if (arg === "--pane") {
    targetWindow = argv[++i]
  } else if (arg === "--enter-delay-ms") {
    defaultEnterDelayMs = Number(argv[++i] ?? "15")
  } else if (arg === "--submit-key") {
    submitKey = argv[++i] ?? "Enter"
    submitKeySetByArg = true
  } else if (arg === "--newline-key") {
    newlineKey = argv[++i] ?? "C-j"
    newlineKeySetByArg = true
  } else if (arg === "--newline-prefix") {
    newlinePrefix = argv[++i] ?? ""
    newlinePrefixSetByArg = true
  } else if (arg === "--verbose") {
    verbose = true
  } else if (arg === "--help") {
    console.log(
      "Usage: agent_input_bus.ts [--session <name>] [--fifo <path>] [--pane <index>] [--enter-delay-ms <n>] [--submit-key <tmux-key>] [--newline-key <tmux-key>] [--newline-prefix <text>] [--verbose]",
    )
    process.exit(0)
  }
}

const sessionLower = session.toLowerCase()
const isClaudeSession = sessionLower.includes("claude")
if (isClaudeSession) {
  if (!submitKeySetByArg) {
    // Claude submit must be sent as a dedicated carriage return; raw Enter can
    // be interpreted as composer newline in some automation paths.
    submitKey = "C-m"
  }
  if (!newlineKeySetByArg) {
    // For multiline composition, Claude behaves more reliably when we send a
    // literal line-feed (Ctrl+J) rather than "Enter". This avoids ambiguity
    // where Enter may submit or may insert a newline depending on UI focus.
    newlineKey = "C-j"
  }
  if (!newlinePrefixSetByArg) {
    newlinePrefix = "\\"
  }
}

const tmuxTarget = `${session}:${targetWindow}`

if (!existsSync(fifo)) {
  console.error(`FIFO ${fifo} not found; run start_tmux_repl.sh first.`)
  process.exit(1)
}

const log = (...values: unknown[]) => {
  if (verbose) {
    console.log("[input-bus]", ...values)
  }
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

const tmuxSendKeys = (keys: ReadonlyArray<string>, options?: { readonly literal?: boolean }) => {
  if (keys.length === 0) {
    return
  }
  const args = ["send-keys", "-t", tmuxTarget]
  if (options?.literal) {
    args.push("-l")
  }
  args.push(...keys)

  log("sending", args.slice(3))
  const result = spawnSync("tmux", args, { stdio: "inherit" })
  if (result.error) {
    console.error("tmux send-keys failed:", result.error)
  }
}

// NOTE: Claude Code appears to apply paste-safety heuristics based on input chunk boundaries.
// If "typed text" and "submit key" arrive in the same chunk, it may refuse to submit.
// Therefore we intentionally send submit as a separate tmux invocation.
const sendToTmux = async (command: InputBusCommand) => {
  const sendMode = (command.send_mode ?? command.sendMode ?? "").toString().trim().toLowerCase()
  if (sendMode) {
    if (sendMode === "submit") {
      command = { ...command, newline: false, enter: true }
    } else if (sendMode === "multiline") {
      command = { ...command, newline: true, enter: false }
    } else if (sendMode === "literal") {
      command = { ...command, newline: false, enter: false }
    } else {
      throw new Error(`Unknown send_mode: ${sendMode}`)
    }
  }

  if (command.keys && command.keys.includes("\n") && sendMode !== "literal") {
    throw new Error('InputBusCommand.keys contains literal newlines; use send_mode="multiline" or send_mode="literal".')
  }

  const hasAny =
    (command.keys && command.keys.length > 0) ||
    (command.special && command.special.length > 0) ||
    command.newline ||
    command.enter

  if (!hasAny) {
    log("no keys to send:", command)
    return
  }

  const sentNonEnter =
    (command.keys && command.keys.length > 0) || (command.special && command.special.length > 0) || command.newline

  if (command.keys && command.keys.length > 0) {
    tmuxSendKeys([command.keys], { literal: true })
  }

  if (command.special) {
    // Claude Code can interpret raw "Enter" differently across automation paths.
    // Normalize "Enter" to the submit key unless the caller explicitly requested a newline.
    const normalizedSpecial =
      isClaudeSession && !command.newline
        ? command.special.map((key) => (key === "Enter" ? submitKey : key))
        : command.special
    for (const key of normalizedSpecial) {
      tmuxSendKeys([key])
    }
  }

  if (command.newline) {
    if (newlinePrefix.length > 0) {
      tmuxSendKeys([newlinePrefix], { literal: true })
    }
    tmuxSendKeys([newlineKey])
  }

  if (command.enter) {
    const enterDelayMs = command.enterDelayMs ?? defaultEnterDelayMs
    if (sentNonEnter && enterDelayMs > 0) {
      await sleep(enterDelayMs)
    }
    tmuxSendKeys([submitKey])
  }
}

const parseCommandLine = (line: string): InputBusCommand => {
  const trimmed = line.trim()
  if (!trimmed) {
    return {}
  }
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
    try {
      const payload = JSON.parse(trimmed) as InputBusCommand
      return payload
    } catch (error) {
      log("invalid json input, falling back to literal:", error)
    }
  }
  return { keys: line }
}

// Keep FIFO reader alive even when writers close:
// open as read+write so we never see EOF solely because a writer disconnected.
const stream = createReadStream(fifo, { encoding: "utf8", flags: "r+" })
let buffer = ""
let pending = Promise.resolve()

stream.on("data", (chunk) => {
  buffer += chunk
  let newlineIndex: number
  while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, newlineIndex)
    buffer = buffer.slice(newlineIndex + 1)
    const command = parseCommandLine(line)
    pending = pending
      .then(async () => {
        if (command.delayMs) {
          await sleep(command.delayMs)
        }
        await sendToTmux(command)
      })
      .catch((error) => {
        console.error("input bus command failed:", error)
      })
  }
})

stream.on("end", () => {
  // With flags:"r+", we should not normally hit EOF.
  console.log("Input bus stream closed (unexpected).")
})

stream.on("error", (error) => {
  console.error("Input bus error:", error)
})
