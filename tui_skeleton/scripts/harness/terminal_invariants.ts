type InvariantCheck = { readonly ok: boolean; readonly message: string; readonly detail?: string }

const findLastIndex = (haystack: string, needle: string): number => haystack.lastIndexOf(needle)

const requireDisableAfterEnable = (raw: string, code: string): InvariantCheck => {
  const enable = `\u001b[?${code}h`
  const disable = `\u001b[?${code}l`
  const lastEnable = findLastIndex(raw, enable)
  if (lastEnable === -1) return { ok: true, message: `mode ?${code} not enabled` }
  const lastDisable = findLastIndex(raw, disable)
  const ok = lastDisable > lastEnable
  return ok
    ? { ok: true, message: `mode ?${code} disabled after enable` }
    : {
        ok: false,
        message: `mode ?${code} left enabled`,
        detail: `lastEnable=${lastEnable} lastDisable=${lastDisable}`,
      }
}

const requireCursorShownAtEnd = (raw: string): InvariantCheck => {
  const hide = "\u001b[?25l"
  const show = "\u001b[?25h"
  const lastHide = findLastIndex(raw, hide)
  const lastShow = findLastIndex(raw, show)
  if (lastHide === -1 && lastShow === -1) {
    return { ok: true, message: "cursor mode not observed" }
  }
  const ok = lastShow > lastHide
  return ok
    ? { ok: true, message: "cursor shown at end" }
    : { ok: false, message: "cursor left hidden", detail: `lastHide=${lastHide} lastShow=${lastShow}` }
}

export const checkTerminalInvariants = (
  raw: string,
): { readonly pass: boolean; readonly checks: ReadonlyArray<InvariantCheck> } => {
  const checks: InvariantCheck[] = []

  checks.push(requireCursorShownAtEnd(raw))
  checks.push(requireDisableAfterEnable(raw, "2004")) // bracketed paste

  // mouse tracking modes (any of these may be enabled depending on runtime)
  checks.push(requireDisableAfterEnable(raw, "1000")) // X10 mouse
  checks.push(requireDisableAfterEnable(raw, "1002")) // button events
  checks.push(requireDisableAfterEnable(raw, "1003")) // any-motion events
  checks.push(requireDisableAfterEnable(raw, "1006")) // SGR mouse mode
  checks.push(requireDisableAfterEnable(raw, "1015")) // urxvt mouse mode

  const pass = checks.every((c) => c.ok)
  return { pass, checks }
}

