export const IPC_PROTOCOL_VERSION = "0.1" as const

export type IpcEnvelope<TType extends string, TPayload> = {
  readonly protocol_version: typeof IPC_PROTOCOL_VERSION
  readonly type: TType
  readonly id?: string
  readonly timestamp_ms: number
  readonly payload: TPayload
}

export type PaletteKind = "commands" | "models" | "files" | "transcript_search"

export type PaletteItem = {
  readonly id: string
  readonly title: string
  readonly detail?: string | null
  readonly group?: string | null
  readonly disabled?: boolean | null
  readonly meta?: Record<string, unknown> | null
}

export type UIReady = IpcEnvelope<"ui.ready", {}>
export type UISubmit = IpcEnvelope<
  "ui.submit",
  {
    readonly text: string
    readonly attachments?: string[] | null
  }
>
export type UIPaletteOpen = IpcEnvelope<
  "ui.palette.open",
  {
    readonly kind: PaletteKind
    readonly query?: string | null
  }
>
export type UIPaletteQuery = IpcEnvelope<
  "ui.palette.query",
  {
    readonly kind: PaletteKind
    readonly query: string
  }
>
export type UIPaletteSelect = IpcEnvelope<
  "ui.palette.select",
  {
    readonly kind: PaletteKind
    readonly item_id: string
  }
>
export type UIPaletteClose = IpcEnvelope<
  "ui.palette.close",
  {
    readonly kind: PaletteKind
  }
>
export type UICommand = IpcEnvelope<
  "ui.command",
  {
    readonly name: string
    readonly args?: Record<string, unknown> | null
  }
>
export type UIPermissionRespond = IpcEnvelope<
  "ui.permission.respond",
  {
    readonly request_id: string
    readonly decision: string
    readonly note?: string | null
    readonly scope?: string | null
    readonly rule?: string | null
    readonly stop?: boolean | null
  }
>
export type UIDraftUpdate = IpcEnvelope<
  "ui.draft.update",
  {
    readonly text: string
  }
>
export type UIShutdown = IpcEnvelope<"ui.shutdown", {}>

export type UIToControllerMessage =
  | UIReady
  | UISubmit
  | UIPaletteOpen
  | UIPaletteQuery
  | UIPaletteSelect
  | UIPaletteClose
  | UICommand
  | UIPermissionRespond
  | UIDraftUpdate
  | UIShutdown

export type CtrlHello = IpcEnvelope<
  "ctrl.hello",
  {
    readonly controller_version: string
    readonly ipc_protocol_version: typeof IPC_PROTOCOL_VERSION
  }
>

export type CtrlState = IpcEnvelope<
  "ctrl.state",
  {
    readonly active_session_id?: string | null
    readonly base_url?: string | null
    readonly config_path?: string | null
    readonly current_model?: string | null
    readonly model_default?: string | null
    readonly model_providers?: string[] | null
    readonly draft_text?: string | null
    readonly status: "idle" | "starting" | "running" | "stopped" | "error"
    readonly pending_permissions?: Array<Record<string, unknown>> | null
  }
>

export type CtrlTranscriptAppend = IpcEnvelope<
  "ctrl.transcript.append",
  {
    readonly text: string
  }
>

export type CtrlEvent = IpcEnvelope<
  "ctrl.event",
  {
    readonly event: Record<string, unknown>
  }
>

export type CtrlPaletteItems = IpcEnvelope<
  "ctrl.palette.items",
  {
    readonly kind: PaletteKind
    readonly query: string
    readonly items: ReadonlyArray<PaletteItem>
  }
>

export type CtrlPaletteStatus = IpcEnvelope<
  "ctrl.palette.status",
  {
    readonly kind: PaletteKind
    readonly status: "idle" | "loading" | "ready" | "error"
    readonly message?: string | null
  }
>

export type CtrlPermissionRequest = IpcEnvelope<
  "ctrl.permission.request",
  {
    readonly request_id: string
    readonly context: Record<string, unknown>
  }
>

export type CtrlError = IpcEnvelope<
  "ctrl.error",
  {
    readonly message: string
    readonly detail?: Record<string, unknown> | null
  }
>

export type CtrlShutdown = IpcEnvelope<
  "ctrl.shutdown",
  {
    readonly reason?: string | null
  }
>

export type ControllerToUIMessage =
  | CtrlHello
  | CtrlState
  | CtrlTranscriptAppend
  | CtrlEvent
  | CtrlPaletteItems
  | CtrlPaletteStatus
  | CtrlPermissionRequest
  | CtrlError
  | CtrlShutdown

export const nowEnvelope = <TType extends string, TPayload>(
  type: TType,
  payload: TPayload,
  options?: { id?: string },
): IpcEnvelope<TType, TPayload> => ({
  protocol_version: IPC_PROTOCOL_VERSION,
  type,
  id: options?.id,
  timestamp_ms: Date.now(),
  payload,
})
