export interface StateWaitCriteria {
  readonly timeoutMs?: number
  readonly fresh?: boolean
  readonly pendingResponse?: boolean
  readonly disconnected?: boolean
  readonly mainFollowTail?: boolean
  readonly statusIncludes?: string
  readonly conversationCountAtLeast?: number
  readonly eventCountAtLeast?: number
  readonly lastConversationSpeaker?: string
  readonly lastConversationPhase?: string
  readonly lastConversationPreviewIncludes?: string
  readonly lastToolEventKind?: string
  readonly lastToolEventStatus?: string
  readonly lastToolEventTextIncludes?: string
  readonly lastLiveSlotStatus?: string
  readonly lastLiveSlotTextIncludes?: string
  readonly lifecycleMode?: string
  readonly lifecycleOwned?: boolean
  readonly lifecyclePidPresent?: boolean
}

export interface StateDumpRecord {
  readonly timestamp?: number
  readonly reason?: string
  readonly state?: {
    readonly pendingResponse?: boolean
    readonly disconnected?: boolean
    readonly mainFollowTail?: boolean
    readonly status?: string
    readonly lifecycle?: {
      readonly mode?: string
      readonly owned?: boolean
      readonly pid?: number
      readonly restartPolicy?: string
      readonly modeSource?: string
    } | null
    readonly lifecycleRestartCount?: number
    readonly stats?: { readonly eventCount?: number }
    readonly counts?: { readonly conversation?: number }
    readonly conversation?: ReadonlyArray<{
      readonly speaker?: string
      readonly phase?: string
      readonly text?: string
    }>
    readonly toolEvents?: ReadonlyArray<{
      readonly kind?: string
      readonly status?: string
      readonly text?: string
    }>
    readonly liveSlots?: ReadonlyArray<{
      readonly status?: string
      readonly text?: string
    }>
    readonly lastConversation?: {
      readonly speaker?: string
      readonly phase?: string
      readonly preview?: string
    } | null
    readonly lastToolEvent?: {
      readonly kind?: string
      readonly status?: string
      readonly text?: string
    } | null
  }
}

export const matchesStateWaitCriteria = (
  record: StateDumpRecord | null,
  criteria: StateWaitCriteria,
  baselineTimestamp = Number.NEGATIVE_INFINITY,
): boolean => {
  if (!record?.state) return false
  const conversation = Array.isArray(record.state.conversation) ? record.state.conversation : []
  const toolEvents = Array.isArray(record.state.toolEvents) ? record.state.toolEvents : []
  const liveSlots = Array.isArray(record.state.liveSlots) ? record.state.liveSlots : []
  const derivedLastConversation =
    record.state.lastConversation ??
    (conversation.length > 0
      ? {
          speaker: conversation[conversation.length - 1]?.speaker,
          phase: conversation[conversation.length - 1]?.phase,
          preview: conversation[conversation.length - 1]?.text,
        }
      : null)
  const derivedLastToolEvent =
    record.state.lastToolEvent ??
    (toolEvents.length > 0
      ? {
          kind: toolEvents[toolEvents.length - 1]?.kind,
          status: toolEvents[toolEvents.length - 1]?.status,
          text: toolEvents[toolEvents.length - 1]?.text,
        }
      : null)
  const derivedLastLiveSlot =
    liveSlots.length > 0
      ? {
          status: liveSlots[liveSlots.length - 1]?.status,
          text: liveSlots[liveSlots.length - 1]?.text,
        }
      : null
  const conversationCount = record.state.counts?.conversation ?? conversation.length
  if (criteria.fresh && (record.timestamp ?? 0) <= baselineTimestamp) return false
  if (criteria.pendingResponse != null && record.state.pendingResponse !== criteria.pendingResponse) return false
  if (criteria.disconnected != null && record.state.disconnected !== criteria.disconnected) return false
  if (criteria.mainFollowTail != null && record.state.mainFollowTail !== criteria.mainFollowTail) return false
  if (criteria.statusIncludes && !(record.state.status ?? "").includes(criteria.statusIncludes)) return false
  if (criteria.conversationCountAtLeast != null && conversationCount < criteria.conversationCountAtLeast) {
    return false
  }
  if (criteria.eventCountAtLeast != null && (record.state.stats?.eventCount ?? 0) < criteria.eventCountAtLeast) return false
  if (criteria.lastConversationSpeaker && (derivedLastConversation?.speaker ?? "") !== criteria.lastConversationSpeaker) {
    return false
  }
  if (criteria.lastConversationPhase && (derivedLastConversation?.phase ?? "") !== criteria.lastConversationPhase) return false
  if (
    criteria.lastConversationPreviewIncludes &&
    !(derivedLastConversation?.preview ?? "").includes(criteria.lastConversationPreviewIncludes)
  ) {
    return false
  }
  if (criteria.lastToolEventKind && (derivedLastToolEvent?.kind ?? "") !== criteria.lastToolEventKind) return false
  if (criteria.lastToolEventStatus && (derivedLastToolEvent?.status ?? "") !== criteria.lastToolEventStatus) return false
  if (criteria.lastToolEventTextIncludes && !(derivedLastToolEvent?.text ?? "").includes(criteria.lastToolEventTextIncludes)) {
    return false
  }
  if (criteria.lastLiveSlotStatus && (derivedLastLiveSlot?.status ?? "") !== criteria.lastLiveSlotStatus) return false
  if (criteria.lastLiveSlotTextIncludes && !(derivedLastLiveSlot?.text ?? "").includes(criteria.lastLiveSlotTextIncludes)) {
    return false
  }
  if (criteria.lifecycleMode && (record.state.lifecycle?.mode ?? "") !== criteria.lifecycleMode) return false
  if (criteria.lifecycleOwned != null && record.state.lifecycle?.owned !== criteria.lifecycleOwned) return false
  if (criteria.lifecyclePidPresent != null) {
    const hasPid = typeof record.state.lifecycle?.pid === "number" && Number.isFinite(record.state.lifecycle.pid)
    if (hasPid !== criteria.lifecyclePidPresent) return false
  }
  return true
}

export const describeTerminalFailedState = (
  record: StateDumpRecord | null,
  criteria: StateWaitCriteria,
  baselineTimestamp = Number.NEGATIVE_INFINITY,
): string | null => {
  if (!record?.state) return null
  if (criteria.fresh && (record.timestamp ?? 0) <= baselineTimestamp) return null
  if (matchesStateWaitCriteria(record, criteria, baselineTimestamp)) return null

  const status = String(record.state.status ?? "")
  const lastConversation = record.state.lastConversation
  const lastSpeaker = String(lastConversation?.speaker ?? "")
  const lastPhase = String(lastConversation?.phase ?? "")
  const lastPreview = String(lastConversation?.preview ?? "")
  const haltedStatus = /^Halted\\b/i.test(status)
  const haltedSystemFinal =
    lastSpeaker === "system" &&
    (lastPhase === "final" || lastPreview.toLowerCase().includes("[halted]"))

  if (!haltedStatus && !haltedSystemFinal) return null

  const wanted = [
    criteria.pendingResponse != null ? `pendingResponse=${criteria.pendingResponse}` : null,
    criteria.lastConversationSpeaker ? `lastConversationSpeaker=${criteria.lastConversationSpeaker}` : null,
    criteria.lastConversationPhase ? `lastConversationPhase=${criteria.lastConversationPhase}` : null,
    criteria.lastConversationPreviewIncludes ? `lastConversationPreviewIncludes=${criteria.lastConversationPreviewIncludes}` : null,
    criteria.statusIncludes ? `statusIncludes=${criteria.statusIncludes}` : null,
  ].filter((part): part is string => part != null)

  const summary = [
    `status=${status || "(empty)"}`,
    `pendingResponse=${String(record.state.pendingResponse)}`,
    `lastConversation=${lastSpeaker || "(none)"}/${lastPhase || "(none)"}`,
    lastPreview ? `preview=${lastPreview.slice(0, 160)}` : null,
  ].filter((part): part is string => part != null)

  return `terminal failed state reached before wait criteria matched; wanted ${wanted.length ? wanted.join(", ") : "(no explicit criteria)"}; latest ${summary.join(", ")}`
}
