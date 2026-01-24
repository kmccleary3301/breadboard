import { logStructuredEvent } from "./fetch_logger.js"

/**
 * Compatibility shim for legacy instrumentation calls inside the vendor CLI.
 * Downstream modules can call `logApiCall("request" | "response", payload)` and
 * we persist the same structured schema used by the fetch hook.
 */
export async function logApiCall(phase, payload) {
  if (!payload || typeof payload !== "object") {
    return
  }
  const requestId = payload.requestId || payload.id || payload.ulid
  if (!requestId) {
    return
  }
  await logStructuredEvent({
    logVersion: 1,
    phase,
    requestId,
    ...payload,
  })
}
