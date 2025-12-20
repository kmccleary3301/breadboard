import type { SessionEvent } from "../api/types.js"
import { CliProviders } from "../providers/cliProviders.js"

export interface AskOptions {
  readonly prompt: string
  readonly configPath: string
  readonly workspace?: string | null
  readonly overrides?: Record<string, unknown>
  readonly metadata?: Record<string, unknown>
  readonly remoteStream?: boolean
}

export interface AskResult {
  readonly sessionId: string
  readonly completion?: unknown
  readonly events: SessionEvent[]
}

export const runAsk = async (
  options: AskOptions,
  onEvent?: (event: SessionEvent) => Promise<void> | void,
): Promise<AskResult> => {
  const appConfig = CliProviders.args.config
  const sdk = CliProviders.sdk
  const api = sdk.api()
  const overrides = { ...(options.overrides ?? {}) }
  const metadata = { ...(options.metadata ?? {}) }
  const useRemoteStream = options.remoteStream ?? appConfig.remoteStreamDefault
  if (useRemoteStream) {
    metadata.enable_remote_stream = true
  }
  const payload = {
    config_path: options.configPath,
    task: options.prompt,
    workspace: options.workspace ?? undefined,
    overrides: Object.keys(overrides).length ? overrides : undefined,
    metadata: Object.keys(metadata).length ? metadata : undefined,
    stream: true,
  }

  const response = await api.createSession(payload)
  const events: SessionEvent[] = []
  let completion: unknown = undefined
  for await (const event of sdk.stream(response.session_id, { signal: undefined })) {
    events.push(event)
    await onEvent?.(event)
    if (event.type === "completion") {
      completion = event.payload?.summary ?? event.payload
      break
    }
  }
  return { sessionId: response.session_id, completion, events }
}
