import type { SessionEvent } from "../api/types.js"
import { getCliAppConfig, getCliSdk } from "./commandRuntime.js"
import { collectSessionStream } from "./sessionStream.js"

export interface AskOptions {
  readonly prompt: string
  readonly configPath: string
  readonly workspace?: string | null
  readonly overrides?: Record<string, unknown>
  readonly metadata?: Record<string, unknown>
  readonly remoteStream?: boolean
  readonly permissionMode?: string | null
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
  const appConfig = getCliAppConfig()
  const sdk = getCliSdk()
  const api = sdk.api()
  const overrides = { ...(options.overrides ?? {}) }
  const metadata = { ...(options.metadata ?? {}) }
  metadata.cli_session_kind ??= "oneshot"
  metadata.non_interactive_cli_session ??= true
  const useRemoteStream = options.remoteStream ?? appConfig.remoteStreamDefault
  if (useRemoteStream) {
    metadata.enable_remote_stream = true
  }
  const payload = {
    config_path: options.configPath,
    task: options.prompt,
    workspace: options.workspace ?? undefined,
    permission_mode: options.permissionMode ?? undefined,
    overrides: Object.keys(overrides).length ? overrides : undefined,
    metadata: Object.keys(metadata).length ? metadata : undefined,
    stream: true,
  }

  const response = await api.createSession(payload)
  const { events, completion } = await collectSessionStream(sdk.stream(response.session_id, { signal: undefined }), onEvent)
  return { sessionId: response.session_id, completion, events }
}
