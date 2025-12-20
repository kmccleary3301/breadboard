const BRACKETED_SEQUENCE_ENABLE = "\u001B[?2004h"
const BRACKETED_SEQUENCE_DISABLE = "\u001B[?2004l"

let bracketedPasteRefs = 0
let cleanupRegistered = false

const SIGNALS: Array<NodeJS.Signals> = ["SIGINT", "SIGTERM", "SIGQUIT"]

const writeDisableSequence = () => {
  if (process.stdout.isTTY) {
    process.stdout.write(BRACKETED_SEQUENCE_DISABLE)
  }
}

const handleProcessExit = () => {
  writeDisableSequence()
}

const registerCleanupHandlers = () => {
  if (cleanupRegistered) return
  cleanupRegistered = true
  process.on("exit", handleProcessExit)
  for (const signal of SIGNALS) {
    process.on(signal, handleProcessExit)
  }
}

const unregisterCleanupHandlers = () => {
  if (!cleanupRegistered) return
  cleanupRegistered = false
  process.removeListener("exit", handleProcessExit)
  for (const signal of SIGNALS) {
    process.removeListener(signal, handleProcessExit)
  }
}

export const enableBracketedPaste = (): void => {
  if (!process.stdout.isTTY) return
  if (bracketedPasteRefs === 0) {
    process.stdout.write(BRACKETED_SEQUENCE_ENABLE)
    registerCleanupHandlers()
  }
  bracketedPasteRefs += 1
}

export const disableBracketedPaste = (): void => {
  if (!process.stdout.isTTY) return
  bracketedPasteRefs = Math.max(0, bracketedPasteRefs - 1)
  if (bracketedPasteRefs === 0) {
    writeDisableSequence()
    unregisterCleanupHandlers()
  }
}
