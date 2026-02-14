import type {
  ConversationEntry,
  LiveSlotEntry,
  StreamStats,
  ModelMenuState,
  ModelMenuItem,
  SkillsMenuState,
  InspectMenuState,
  SkillSelection,
  CTreeSnapshot,
  GuardrailNotice,
  QueuedAttachment,
  TranscriptPreferences,
  ToolLogEntry,
  TodoItem,
  TodoStoreSnapshot,
  TaskEntry,
  WorkGraphState,
  PermissionRequest,
  PermissionDecision,
  RewindMenuState,
} from "../../types.js"
import type { SessionFileInfo, SessionFileContent, CTreeTreeResponse } from "../../../api/types.js"
import type { ResolvedTuiConfig } from "../../../tui_config/types.js"

export interface ReplViewProps {
  readonly tuiConfig?: ResolvedTuiConfig
  readonly configPath?: string | null
  readonly sessionId: string
  readonly conversation: ConversationEntry[]
  readonly toolEvents: ToolLogEntry[]
  readonly rawEvents: ToolLogEntry[]
  readonly liveSlots: LiveSlotEntry[]
  readonly status: string
  readonly pendingResponse: boolean
  readonly disconnected: boolean
  readonly mode?: string | null
  readonly permissionMode?: string | null
  readonly hints: string[]
  readonly stats: StreamStats
  readonly modelMenu: ModelMenuState
  readonly skillsMenu: SkillsMenuState
  readonly inspectMenu: InspectMenuState
  readonly guardrailNotice?: GuardrailNotice | null
  readonly viewClearAt?: number | null
  readonly viewPrefs: TranscriptPreferences
  readonly todoScopeKey: string
  readonly todoScopeLabel: string
  readonly todoScopeStale: boolean
  readonly todoScopeOrder: ReadonlyArray<string>
  readonly todoStore: TodoStoreSnapshot
  readonly todos: TodoItem[]
  readonly tasks: TaskEntry[]
  readonly workGraph: WorkGraphState
  readonly ctreeSnapshot?: CTreeSnapshot | null
  readonly ctreeTree?: CTreeTreeResponse | null
  readonly ctreeTreeStatus: "idle" | "loading" | "error"
  readonly ctreeTreeError?: string | null
  readonly ctreeStage: string
  readonly ctreeIncludePreviews: boolean
  readonly ctreeSource: string
  readonly ctreeUpdatedAt?: number | null
  readonly permissionRequest?: PermissionRequest | null
  readonly permissionError?: string | null
  readonly permissionQueueDepth?: number
  readonly rewindMenu: RewindMenuState
  readonly onSubmit: (value: string, attachments?: ReadonlyArray<QueuedAttachment>) => Promise<void>
  readonly onModelMenuOpen: () => Promise<void>
  readonly onModelSelect: (item: ModelMenuItem) => Promise<void>
  readonly onModelMenuCancel: () => void
  readonly onSkillsMenuOpen: () => Promise<void>
  readonly onSkillsMenuCancel: () => void
  readonly onSkillsApply: (selection: SkillSelection) => Promise<void>
  readonly onGuardrailToggle: () => void
  readonly onGuardrailDismiss: () => void
  readonly onPermissionDecision: (decision: PermissionDecision) => Promise<void>
  readonly onRewindClose: () => void
  readonly onRewindRestore: (checkpointId: string, mode: "conversation" | "code" | "both") => Promise<void>
  readonly onListFiles: (path?: string) => Promise<SessionFileInfo[]>
  readonly onReadFile: (
    path: string,
    options?: { mode?: "cat" | "snippet"; headLines?: number; tailLines?: number; maxBytes?: number },
  ) => Promise<SessionFileContent>
  readonly onCtreeRequest: (force?: boolean) => Promise<void>
  readonly onCtreeRefresh: (options?: { stage?: string; includePreviews?: boolean; source?: string }) => Promise<void>
}
