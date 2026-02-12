import type { FilePickerResource } from "../../../../filePicker.js"
import type { FileMentionMode } from "../../../../fileMentions.js"
import type { SessionFileInfo } from "../../../../../api/types.js"

export interface FilePickerState {
  readonly status: "hidden" | "loading" | "ready" | "error"
  readonly cwd: string
  readonly items: ReadonlyArray<SessionFileInfo>
  readonly index: number
  readonly message?: string
}

export interface FileIndexMeta {
  readonly status: "idle" | "scanning" | "ready" | "error"
  readonly fileCount: number
  readonly dirCount: number
  readonly scannedDirs: number
  readonly queuedDirs: number
  readonly truncated: boolean
  readonly message?: string
  readonly version: number
}

export interface FileIndexStore {
  generation: number
  running: boolean
  visited: Set<string>
  queue: string[]
  files: Map<string, SessionFileInfo>
  dirs: Map<string, SessionFileInfo>
  lastMetaUpdateMs: number
}

export type FileMenuRow =
  | { readonly kind: "resource"; readonly resource: FilePickerResource }
  | { readonly kind: "file"; readonly item: SessionFileInfo }

export interface ActiveAtMention {
  readonly start: number
  readonly end: number
  readonly query: string
  readonly quoted: boolean
}

export interface QueuedFileMention {
  readonly id: string
  readonly path: string
  readonly size?: number | null
  readonly requestedMode: FileMentionMode
  readonly addedAt: number
}
