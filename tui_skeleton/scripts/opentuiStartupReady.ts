import { COMPOSER_READY_MARKERS, includesComposerReady } from "./harness/composerReady"

export const waitForPlainComposerReady = async (
  getPlain: () => string,
  options?: { timeoutMs?: number; pollMs?: number; label?: string },
): Promise<void> => {
  const timeoutMs = options?.timeoutMs ?? 25_000
  const pollMs = options?.pollMs ?? 50
  const label = options?.label ?? "OpenTUI startup readiness"
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    if (includesComposerReady(getPlain())) return
    await new Promise((resolve) => setTimeout(resolve, pollMs))
  }
  throw new Error(`[${label}] timed out waiting for startup readiness via markers: ${COMPOSER_READY_MARKERS.join(", ")}`)
}
