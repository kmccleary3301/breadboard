import { loadAppConfig, type AppConfig } from "../config/appConfig.js"
import { createBreadboardClient, openEventStream, streamSessionEvents, type EventStreamHandlers, type EventStreamHandle, type BreadboardClient } from "@breadboard/sdk"
import type { AttachmentUploadPayload, SessionEvent } from "@breadboard/sdk"
import { resolveAuthToken } from "../config/authTokenProvider.js"

export class CliArgsProvider {
  get config(): AppConfig { return loadAppConfig() }
}

export class CliSdkProvider {
  private instance: BreadboardClient | undefined
  private client(): BreadboardClient {
    if (this.instance) return this.instance
    const config = loadAppConfig()
    this.instance = createBreadboardClient({ baseUrl: config.baseUrl, requestTimeoutMs: config.requestTimeoutMs, streamSchema: config.streamSchema, streamIncludeLegacy: config.streamIncludeLegacy, authToken: () => resolveAuthToken(config.baseUrl) })
    return this.instance
  }
  api(): BreadboardClient { return this.client() }
  stream(sessionId: string, options: { signal?: AbortSignal; lastEventId?: string }) {
    const config = loadAppConfig()
    return streamSessionEvents(sessionId, { signal: options.signal, lastEventId: options.lastEventId, config: { baseUrl: config.baseUrl, requestTimeoutMs: config.requestTimeoutMs, streamSchema: config.streamSchema, streamIncludeLegacy: config.streamIncludeLegacy, authToken: () => resolveAuthToken(config.baseUrl) } })
  }
  openEventStream(sessionId: string, handlers: EventStreamHandlers, options: { signal?: AbortSignal; lastEventId?: string } = {}): EventStreamHandle {
    const config = loadAppConfig()
    return openEventStream(sessionId, handlers, { signal: options.signal, lastEventId: options.lastEventId, config: { baseUrl: config.baseUrl, streamSchema: config.streamSchema, streamIncludeLegacy: config.streamIncludeLegacy } })
  }
  uploadAttachments(sessionId: string, attachments: ReadonlyArray<AttachmentUploadPayload>) { return this.client().uploadAttachments(sessionId, attachments) }
}
export class CliSyncProvider {
  private readonly cache = new Map<string, SessionEvent[]>()
  append(sessionId: string, event: SessionEvent) {
    if (!this.cache.has(sessionId)) this.cache.set(sessionId, [])
    this.cache.get(sessionId)!.push(event)
  }
  events(sessionId: string): readonly SessionEvent[] { return this.cache.get(sessionId) ?? [] }
}

const globalArgs = new CliArgsProvider()
const globalSdk = new CliSdkProvider()
const globalSync = new CliSyncProvider()
export const CliProviders = { args: globalArgs, sdk: globalSdk, sync: globalSync }
export type CliProviderBundle = typeof CliProviders

