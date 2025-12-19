import { loadAppConfig, type AppConfig } from "../config/appConfig.js"
import { ApiClient } from "../api/client.js"
import { streamSessionEvents } from "../api/stream.js"
import type { AttachmentUploadPayload } from "../api/client.js"
import type { SessionEvent } from "../api/types.js"

export class CliArgsProvider {
  readonly config: AppConfig

  constructor(config?: AppConfig) {
    this.config = config ?? loadAppConfig()
  }
}

export class CliSdkProvider {
  private readonly config: AppConfig

  constructor(config: AppConfig) {
    this.config = config
  }

  api() {
    return ApiClient
  }

  stream(sessionId: string, options: { signal?: AbortSignal }) {
    return streamSessionEvents(sessionId, { signal: options.signal, config: this.config })
  }

  uploadAttachments(sessionId: string, attachments: ReadonlyArray<AttachmentUploadPayload>) {
    return ApiClient.uploadAttachments(sessionId, attachments)
  }
}

export class CliSyncProvider {
  private readonly cache = new Map<string, SessionEvent[]>()

  append(sessionId: string, event: SessionEvent) {
    if (!this.cache.has(sessionId)) {
      this.cache.set(sessionId, [])
    }
    this.cache.get(sessionId)!.push(event)
  }

  events(sessionId: string): readonly SessionEvent[] {
    return this.cache.get(sessionId) ?? []
  }
}

const globalArgs = new CliArgsProvider()
const globalSdk = new CliSdkProvider(globalArgs.config)
const globalSync = new CliSyncProvider()

export const CliProviders = {
  args: globalArgs,
  sdk: globalSdk,
  sync: globalSync,
}

export type CliProviderBundle = typeof CliProviders
