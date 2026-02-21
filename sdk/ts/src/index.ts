export { ApiError, createBreadboardClient, type BreadboardClientConfig } from "./client.js"
export { streamSessionEvents, type EventStreamOptions, type StreamConfig } from "./stream.js"

export type {
  EventType,
  SessionEvent,
  SessionFileInfo,
  SessionFileContent,
  HealthResponse,
  ModelCatalogEntry,
  ModelCatalogResponse,
  SkillType,
  SkillEntry,
  SkillSelection,
  SkillCatalog,
  SkillCatalogResponse,
  CTreeSnapshotResponse,
  SessionArtifactInfo,
  SessionCreateRequest,
  SessionCreateResponse,
  SessionInputRequest,
  SessionInputResponse,
  SessionCommandRequest,
  SessionCommandResponse,
  SessionSummary,
  ErrorResponse,
} from "./types.js"
