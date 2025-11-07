export interface ConversationEntry {
  readonly speaker: "assistant" | "user" | "system"
  readonly text: string
}

export interface StreamStats {
  eventCount: number
  toolCount: number
  lastTurn: number | null
  remote: boolean
  model: string
}

export interface ModelMenuItem {
  readonly label: string
  readonly value: string
  readonly provider: string
  readonly detail?: string
  readonly isDefault?: boolean
  readonly isCurrent?: boolean
}

export type ModelMenuState =
  | { readonly status: "hidden" }
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly message: string }
  | { readonly status: "ready"; readonly items: ReadonlyArray<ModelMenuItem> }

