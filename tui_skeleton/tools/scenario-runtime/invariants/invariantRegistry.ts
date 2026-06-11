import { promises as fs } from "node:fs"
import path from "node:path"

export interface ScenarioArtifacts {
  readonly artifactDir: string
  readonly scenarioId: string
  readonly lane: string
  readonly manifest?: Record<string, unknown>
  readonly plainText?: string
  readonly rawText?: string
  readonly viewportText?: string
  readonly scrollbackText?: string
  readonly stateDumpText?: string
  readonly transcriptCellsText?: string
  readonly terminalGridText?: string
  readonly actionConfirmations?: Record<string, boolean>
  readonly performanceMetrics?: Record<string, unknown>
}

export interface InvariantRequest {
  readonly id: string
  readonly severity: "blocker" | "warning" | "metric" | "human-aid"
}

export interface InvariantResult {
  readonly id: string
  readonly severity: InvariantRequest["severity"]
  readonly status: "pass" | "fail" | "skip" | "metric"
  readonly message: string
  readonly evidence?: Record<string, unknown>
}

export interface InvariantReport {
  readonly ok: boolean
  readonly scenarioId: string
  readonly lane: string
  readonly generatedAt: string
  readonly results: InvariantResult[]
}

const readIfExists = async (filePath: string): Promise<string | undefined> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return undefined
  }
}

const parseJsonIfExists = async (filePath: string): Promise<Record<string, unknown> | undefined> => {
  const raw = await readIfExists(filePath)
  if (!raw) return undefined
  try {
    return JSON.parse(raw) as Record<string, unknown>
  } catch {
    return undefined
  }
}

export const loadScenarioArtifacts = async (artifactDir: string, scenarioId: string, lane: string): Promise<ScenarioArtifacts> => {
  return {
    artifactDir,
    scenarioId,
    lane,
    manifest: await parseJsonIfExists(path.join(artifactDir, "manifest.json")),
    plainText: await readIfExists(path.join(artifactDir, "pty_plain.txt")),
    rawText: await readIfExists(path.join(artifactDir, "pty_raw.ansi")),
    viewportText: await readIfExists(path.join(artifactDir, "viewport_final.txt")),
    scrollbackText: await readIfExists(path.join(artifactDir, "scrollback_final.txt")),
    stateDumpText: await readIfExists(path.join(artifactDir, "state_dumps.ndjson")),
    transcriptCellsText: await readIfExists(path.join(artifactDir, "transcript_cells.ndjson")),
    terminalGridText: await readIfExists(path.join(artifactDir, "terminal_grid.ndjson")),
    actionConfirmations: (await parseJsonIfExists(path.join(artifactDir, "action_confirmations.json"))) as Record<string, boolean> | undefined,
    performanceMetrics: await parseJsonIfExists(path.join(artifactDir, "performance_metrics.json")),
  }
}

const textBlob = (artifacts: ScenarioArtifacts): string =>
  [artifacts.plainText, artifacts.viewportText, artifacts.scrollbackText, artifacts.terminalGridText]
    .filter((value): value is string => typeof value === "string")
    .join("\n")

const countOccurrences = (text: string, needle: string): number => {
  if (!needle) return 0
  let count = 0
  let idx = 0
  while ((idx = text.indexOf(needle, idx)) >= 0) {
    count += 1
    idx += needle.length
  }
  return count
}

const regexCount = (text: string, pattern: RegExp): number => (text.match(pattern) ?? []).length

const parseNdjson = (text: string | undefined): any[] => {
  if (!text) return []
  const records: any[] = []
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try {
      records.push(JSON.parse(trimmed))
    } catch {
      // State dump files may contain non-json diagnostics in failure cases. Ignore for structural invariants.
    }
  }
  return records
}

const latestState = (artifacts: ScenarioArtifacts): any | null => {
  const records = parseNdjson(artifacts.stateDumpText)
  const latest = records.at(-1)
  return latest?.state ?? latest ?? null
}

const latestStateRecord = (artifacts: ScenarioArtifacts): any | null => {
  const records = parseNdjson(artifacts.stateDumpText)
  return records.at(-1) ?? null
}

const latestTranscriptCells = (artifacts: ScenarioArtifacts): any[] => {
  const record = latestStateRecord(artifacts)
  const cells = record?.transcriptCells ?? record?.state?.transcriptCells
  if (Array.isArray(cells)) return cells
  const records = parseNdjson(artifacts.transcriptCellsText)
  if (records.length === 1 && Array.isArray(records[0]?.transcriptCells)) return records[0].transcriptCells
  if (records.length === 1 && Array.isArray(records[0]?.cells)) return records[0].cells
  return records
}

const latestConversationEntries = (artifacts: ScenarioArtifacts): any[] => {
  const state = latestState(artifacts)
  return Array.isArray(state?.conversation) ? state.conversation : []
}

const latestLiveSlots = (artifacts: ScenarioArtifacts): any[] => {
  const state = latestState(artifacts)
  return Array.isArray(state?.liveSlots) ? state.liveSlots : []
}

const latestPendingResponse = (artifacts: ScenarioArtifacts): boolean => latestState(artifacts)?.pendingResponse === true

const activeLiveSlotStatuses = new Set(["active", "pending", "running", "streaming", "recovering", "reconnecting"])

const isActiveLiveSlot = (slot: any): boolean => {
  const status = String(slot?.status ?? "").trim().toLowerCase()
  if (status && activeLiveSlotStatuses.has(status)) return true
  const text = String(slot?.text ?? slot?.label ?? "").trim()
  return /\b(?:running|streaming|thinking|recovering|reconnecting|pending|attempting|retrying)\b/i.test(text)
}

const cellIdentity = (cell: any): string => String(cell?.id ?? "").trim()

const cellDedupeKey = (cell: any): string => String(cell?.dedupeKey ?? "").trim()

const getAcceptedPrompts = (artifacts: ScenarioArtifacts): string[] => {
  const manifestPrompt = artifacts.manifest?.expectedPrompt
  if (typeof manifestPrompt === "string" && manifestPrompt.trim()) return [manifestPrompt.trim()]
  const state = latestState(artifacts)
  const conversation = Array.isArray(state?.conversation) ? state.conversation : []
  return conversation
    .filter((entry: any) => entry?.speaker === "user" || entry?.role === "user" || entry?.role === "user-request")
    .map((entry: any) => String(entry?.text ?? entry?.textPreview ?? entry?.content ?? "").trim())
    .filter(Boolean)
}

const expectedAssistantTerms = (artifacts: ScenarioArtifacts): string[] => {
  const expected = typeof artifacts.manifest?.expectedAssistantText === "string" ? artifacts.manifest.expectedAssistantText : ""
  return Array.from(
    new Set(
      expected
        .replace(/[`*_#[\]()>|:-]/g, " ")
        .split(/\s+/)
        .map((term) => term.trim())
        .filter((term) => term.length >= 5),
    ),
  )
}

const finalVisibleText = (artifacts: ScenarioArtifacts): string =>
  [artifacts.viewportText, artifacts.scrollbackText, artifacts.terminalGridText, artifacts.plainText]
    .find((value) => typeof value === "string" && value.trim().length > 0) ?? ""

const manifestStringArray = (artifacts: ScenarioArtifacts, key: string): string[] => {
  const value = artifacts.manifest?.[key]
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0) : []
}

const manifestStringArrayRecord = (artifacts: ScenarioArtifacts, key: string): Record<string, string[]> => {
  const value = artifacts.manifest?.[key]
  if (!value || typeof value !== "object" || Array.isArray(value)) return {}
  const entries = Object.entries(value).flatMap(([label, raw]) => {
    if (!Array.isArray(raw)) return []
    const expected = raw.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    return expected.length > 0 ? [[label, expected] as const] : []
  })
  return Object.fromEntries(entries)
}

const snapshotVisibleText = (artifacts: ScenarioArtifacts): Record<string, string> | null => {
  const raw = artifacts.terminalGridText
  if (!raw?.trim()) return null
  try {
    const parsed = JSON.parse(raw) as unknown
    if (parsed && typeof parsed === "object" && "snapshots" in parsed && Array.isArray((parsed as { snapshots?: unknown }).snapshots)) {
      return Object.fromEntries(
        (parsed as { snapshots: unknown[] }).snapshots.flatMap((snapshot) => {
          if (!snapshot || typeof snapshot !== "object") return []
          const record = snapshot as Record<string, unknown>
          const label = typeof record.label === "string" ? record.label.trim() : ""
          const text =
            typeof record.cleaned === "string"
              ? record.cleaned
              : typeof record.text === "string"
                ? record.text
                : typeof record.viewport === "string"
                  ? record.viewport
                  : ""
          return label && text ? [[label, text] as const] : []
        }),
      )
    }
  } catch {
    const snapshots: Record<string, string> = {}
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim()
      if (!trimmed) continue
      try {
        const record = JSON.parse(trimmed) as Record<string, unknown>
        const label = typeof record.label === "string" ? record.label.trim() : ""
        const text =
          typeof record.cleaned === "string"
            ? record.cleaned
            : typeof record.text === "string"
              ? record.text
              : typeof record.viewport === "string"
                ? record.viewport
                : ""
        if (label && text) snapshots[label] = text
      } catch {
        // Keep parsing later lines; malformed diagnostics should not hide valid snapshots.
      }
    }
    return snapshots
  }
  return {}
}

const countLiteralOccurrences = (text: string, needle: string): number => {
  if (!needle) return 0
  let count = 0
  let index = 0
  while (index <= text.length) {
    const next = text.indexOf(needle, index)
    if (next < 0) break
    count += 1
    index = next + needle.length
  }
  return count
}

const pass = (request: InvariantRequest, message: string, evidence?: Record<string, unknown>): InvariantResult => ({
  id: request.id,
  severity: request.severity,
  status: request.severity === "metric" ? "metric" : "pass",
  message,
  evidence,
})

const fail = (request: InvariantRequest, message: string, evidence?: Record<string, unknown>): InvariantResult => ({
  id: request.id,
  severity: request.severity,
  status: request.severity === "warning" || request.severity === "metric" || request.severity === "human-aid" ? "metric" : "fail",
  message,
  evidence,
})

const skip = (request: InvariantRequest, message: string): InvariantResult => ({
  id: request.id,
  severity: request.severity,
  status: "skip",
  message,
})

const invariantFns: Record<string, (request: InvariantRequest, artifacts: ScenarioArtifacts) => InvariantResult> = {
  "GLOBAL-HOST-HISTORY-PRESERVED": (request, artifacts) => {
    const expected = artifacts.manifest?.hostHistorySentinels
    if (!Array.isArray(expected) || expected.length === 0) return skip(request, "no host history sentinels declared")
    const text = textBlob(artifacts)
    const missing = expected.filter((item) => typeof item === "string" && !text.includes(item))
    return missing.length === 0
      ? pass(request, "host history sentinels are visible", { sentinels: expected.length })
      : fail(request, "host history sentinels missing", { missing })
  },
  "GLOBAL-NO-RAW-ANSI": (request, artifacts) => {
    const text = finalVisibleText(artifacts)
    const leaked = /\x1b\[[0-9;?]*[A-Za-z]/.test(text) || /\[200~|\[201~/.test(text)
    return leaked ? fail(request, "raw ANSI/bracketed paste marker visible in plain text") : pass(request, "no raw ANSI leak detected")
  },
  "GLOBAL-NO-RAW-PROVIDER-DIAGNOSTIC": (request, artifacts) => {
    const text = textBlob(artifacts)
    const patterns = [
      /\{'error':/i,
      /"error"\s*:\s*\{/i,
      /'type':\s*'(?:invalid_request_error|insufficient_quota|rate_limit_error|authentication_error)'/i,
      /context_length_exceeded/i,
      /Error code:\s*(?:400|401|429|5\d\d)\s*-/i,
    ]
    const matched = patterns.find((pattern) => pattern.test(text))?.source
    return matched
      ? fail(request, "raw provider diagnostic payload visible in default artifacts", { pattern: matched })
      : pass(request, "no raw provider diagnostic payload visible")
  },
  "DIAG-PROVIDER-DIAGNOSTIC-DEDUPED": (request, artifacts) => {
    const state = latestState(artifacts)
    const toolText = Array.isArray(state?.toolEvents)
      ? state.toolEvents.map((entry: any) => String(entry?.text ?? "")).join("\n")
      : ""
    const conversationText = Array.isArray(state?.conversation)
      ? state.conversation.map((entry: any) => String(entry?.text ?? "")).join("\n")
      : ""
    const expected = [
      { label: "quota", phrase: /Provider quota exceeded/g, maxTool: 2, maxConversation: 1 },
      { label: "context", phrase: /Provider context limit exceeded/g, maxTool: 2, maxConversation: 1 },
      { label: "rate", phrase: /Provider rate limit hit/g, maxTool: 2, maxConversation: 1 },
    ]
    const counts = expected.map((item) => ({
      label: item.label,
      tool: regexCount(toolText, item.phrase),
      conversation: regexCount(conversationText, item.phrase),
      maxTool: item.maxTool,
      maxConversation: item.maxConversation,
    }))
    const bad = counts.filter((item) => item.tool > item.maxTool || item.conversation > item.maxConversation)
    return bad.length === 0
      ? pass(request, "provider diagnostics are within dedupe cardinality", { counts })
      : fail(request, "provider diagnostic row cardinality exceeded", { bad, counts })
  },
  "GLOBAL-NO-SYSTEM-PROMPT-LEAK": (request, artifacts) => {
    const text = textBlob(artifacts)
    const patterns = [/You are Codex, based on GPT-5/i, /## Editing constraints/i, /## Plan tool/i, /system prompt/i]
    const matched = patterns.find((pattern) => pattern.test(text))?.source
    return matched ? fail(request, "system/developer prompt leakage detected", { pattern: matched }) : pass(request, "no system prompt leakage detected")
  },
  "GLOBAL-NO-DUPLICATE-PROMPT": (request, artifacts) => {
    const text = finalVisibleText(artifacts)
    const prompts = getAcceptedPrompts(artifacts)
    if (prompts.length === 0) return skip(request, "no accepted prompt known")
    const findings = prompts.map((prompt) => ({ prompt, count: countOccurrences(text, prompt) }))
    const bad = findings.filter((finding) => finding.count > 2)
    return bad.length === 0 ? pass(request, "accepted prompts are not visibly duplicated", { findings }) : fail(request, "accepted prompt duplicated", { bad })
  },
  "GLOBAL-TRANSCRIPT-ORDER": (request, artifacts) => {
    const prompt = getAcceptedPrompts(artifacts)[0]
    const expected = typeof artifacts.manifest?.expectedAssistantText === "string" ? artifacts.manifest.expectedAssistantText : undefined
    const text = textBlob(artifacts)
    if (!prompt || !expected) return skip(request, "prompt/assistant order expectation missing")
    const promptIdx = text.indexOf(prompt)
    const exactAssistantIdx = text.indexOf(expected)
    const semanticAssistantIndexes = expectedAssistantTerms(artifacts)
      .map((term) => text.indexOf(term))
      .filter((index) => index >= 0)
    const assistantIdx =
      exactAssistantIdx >= 0
        ? exactAssistantIdx
        : semanticAssistantIndexes.length > 0
          ? Math.min(...semanticAssistantIndexes)
          : -1
    return promptIdx >= 0 && assistantIdx >= 0 && promptIdx < assistantIdx
      ? pass(request, "prompt appears before assistant text")
      : fail(request, "prompt/assistant order not proven", { promptIdx, assistantIdx })
  },
  "GLOBAL-NO-READY-LIE": (request, artifacts) => {
    const states = parseNdjson(artifacts.stateDumpText).map((record) => record?.state ?? record)
    const bad = states.find((state) => {
      const status = String(state?.status ?? "")
      const disconnected = state?.disconnected === true
      const pending = state?.pendingResponse === true
      return /ready/i.test(status) && (disconnected || /recover|reconnect|disconnect/i.test(status) || pending)
    })
    return bad ? fail(request, "ready status while disconnected/recovering/pending", { status: bad.status }) : pass(request, "no ready-lie state observed")
  },
  "GLOBAL-COMPOSER-VISIBLE": (request, artifacts) => {
    const text = textBlob(artifacts)
    const visible = /Type your request|enter send|❯|›/.test(text)
    return visible ? pass(request, "composer/input affordance visible") : fail(request, "composer/input affordance not visible")
  },
  "GLOBAL-FOOTER-TRUTHFUL": (request, artifacts) => {
    const text = textBlob(artifacts)
    const hasFooter = /enter send|resume \/sessions|ctrl\+|shortcuts|mdl /.test(text)
    return hasFooter ? pass(request, "footer/status affordance visible") : fail(request, "footer/status affordance missing")
  },
  "VISIBLE-TEXT-SENTINELS-PRESENT": (request, artifacts) => {
    const expected = manifestStringArray(artifacts, "expectedVisibleText")
    if (expected.length === 0) return skip(request, "no expected visible text sentinels declared")
    const text = textBlob(artifacts)
    const missing = expected.filter((item) => !text.includes(item))
    return missing.length === 0
      ? pass(request, "expected visible text sentinels are present", { count: expected.length })
      : fail(request, "expected visible text sentinels missing", { missing })
  },
  "FINAL-VISIBLE-TEXT-SENTINELS-PRESENT": (request, artifacts) => {
    const expected = manifestStringArray(artifacts, "expectedFinalVisibleText")
    if (expected.length === 0) return skip(request, "no expected final visible text sentinels declared")
    const text = finalVisibleText(artifacts)
    const missing = expected.filter((item) => !text.includes(item))
    return missing.length === 0
      ? pass(request, "expected final visible text sentinels are present", { count: expected.length })
      : fail(request, "expected final visible text sentinels missing", { missing })
  },
  "FINAL-VISIBLE-TEXT-SENTINELS-UNIQUE": (request, artifacts) => {
    const expected = manifestStringArray(artifacts, "expectedFinalUniqueText")
    if (expected.length === 0) return skip(request, "no expected final unique text sentinels declared")
    const text = finalVisibleText(artifacts)
    const duplicates = expected
      .map((item) => ({ item, count: countLiteralOccurrences(text, item) }))
      .filter((entry) => entry.count !== 1)
    return duplicates.length === 0
      ? pass(request, "expected final visible text sentinels are unique", { count: expected.length })
      : fail(request, "expected final visible text sentinels are missing or duplicated", { duplicates })
  },
  "SNAPSHOT-VISIBLE-TEXT-SENTINELS-PRESENT": (request, artifacts) => {
    const expectedByLabel = manifestStringArrayRecord(artifacts, "expectedSnapshotVisibleText")
    const labels = Object.keys(expectedByLabel)
    if (labels.length === 0) return skip(request, "no expected snapshot visible text sentinels declared")
    const snapshots = snapshotVisibleText(artifacts)
    if (!snapshots) return fail(request, "terminal grid snapshot artifact is missing or empty")
    const missingSnapshots = labels.filter((label) => snapshots[label] === undefined)
    const missingSentinels = labels.flatMap((label) => {
      const text = snapshots[label] ?? ""
      return expectedByLabel[label]
        .filter((sentinel) => !text.includes(sentinel))
        .map((sentinel) => ({ label, sentinel }))
    })
    if (missingSnapshots.length === 0 && missingSentinels.length === 0) {
      return pass(request, "expected snapshot visible text sentinels are present", { snapshotCount: labels.length })
    }
    return fail(request, "expected snapshot visible text sentinels missing", { missingSnapshots, missingSentinels })
  },
  "SNAPSHOT-VISIBLE-TEXT-SENTINELS-UNIQUE": (request, artifacts) => {
    const expectedByLabel = manifestStringArrayRecord(artifacts, "expectedSnapshotUniqueText")
    const labels = Object.keys(expectedByLabel)
    if (labels.length === 0) return skip(request, "no expected snapshot unique text sentinels declared")
    const snapshots = snapshotVisibleText(artifacts)
    if (!snapshots) return fail(request, "terminal grid snapshot artifact is missing or empty")
    const missingSnapshots = labels.filter((label) => snapshots[label] === undefined)
    const nonUnique = labels.flatMap((label) => {
      const text = snapshots[label] ?? ""
      return expectedByLabel[label]
        .map((sentinel) => ({ label, sentinel, count: countLiteralOccurrences(text, sentinel) }))
        .filter((entry) => entry.count !== 1)
    })
    if (missingSnapshots.length === 0 && nonUnique.length === 0) {
      return pass(request, "expected snapshot visible text sentinels are unique", { snapshotCount: labels.length })
    }
    return fail(request, "expected snapshot visible text sentinels are missing or duplicated", { missingSnapshots, nonUnique })
  },
  "COMPOSER-NO-BRACKETED-PASTE-MARKERS": (request, artifacts) => {
    const text = finalVisibleText(artifacts)
    const leaked = /\[200~|\[201~|\x1b\[200~|\x1b\[201~/.test(text)
    return leaked ? fail(request, "bracketed paste marker leaked into visible composer output") : pass(request, "no bracketed paste marker visible")
  },
  "FOOTER-NO-DUPLICATE-STATUS": (request, artifacts) => {
    const text = finalVisibleText(artifacts)
    const footerLines = text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => /enter send|resume \/sessions|ctrl\+|shortcuts|mdl /.test(line))
    const unique = new Set(footerLines)
    const excessive = footerLines.length > 4 || unique.size < footerLines.length - 1
    return excessive
      ? fail(request, "footer/status lines appear duplicated in final visible output", { footerLines })
      : pass(request, "footer/status line cardinality is bounded", { footerLineCount: footerLines.length })
  },
  "LIVE-NO-STALE-STREAMING-WHEN-IDLE": (request, artifacts) => {
    if (latestPendingResponse(artifacts)) return pass(request, "pending response still active; streaming rows are allowed")
    const conversationStreaming = latestConversationEntries(artifacts).filter((entry) => entry?.phase === "streaming")
    const cellStreaming = latestTranscriptCells(artifacts).filter((cell) => cell?.streaming === true || cell?.phase === "streaming" || cell?.lifecycle === "live")
    const stale = [...conversationStreaming, ...cellStreaming]
    return stale.length === 0
      ? pass(request, "no stale streaming conversation or transcript cells after idle")
      : fail(request, "stale streaming rows remain after idle", { staleCount: stale.length, stale: stale.slice(0, 6) })
  },
  "LIVE-NO-PENDING-SLOTS-WHEN-IDLE": (request, artifacts) => {
    if (latestPendingResponse(artifacts)) return pass(request, "pending response still active; live slots are allowed")
    const slots = latestLiveSlots(artifacts)
    const active = slots.filter(isActiveLiveSlot)
    return active.length === 0
      ? pass(request, "no active live slots remain after idle", { liveSlotCount: slots.length })
      : fail(request, "active live slots remain after idle", { activeCount: active.length, active: active.slice(0, 6) })
  },
  "LIVE-TRANSCRIPT-CELL-IDS-UNIQUE": (request, artifacts) => {
    const cells = latestTranscriptCells(artifacts)
    if (cells.length === 0) return skip(request, "no transcript cells available")
    const seen = new Set<string>()
    const duplicates: string[] = []
    for (const cell of cells) {
      const id = cellIdentity(cell)
      if (!id) continue
      if (seen.has(id)) duplicates.push(id)
      else seen.add(id)
    }
    return duplicates.length === 0
      ? pass(request, "transcript cell IDs are unique", { cellCount: cells.length, identifiedCells: seen.size })
      : fail(request, "duplicate transcript cell IDs detected", { duplicates: Array.from(new Set(duplicates)) })
  },
  "LIVE-DURABLE-DEDUPE-KEYS-UNIQUE": (request, artifacts) => {
    const cells = latestTranscriptCells(artifacts).filter((cell) => cell?.streaming !== true && cell?.phase !== "streaming" && cell?.lifecycle !== "live")
    if (cells.length === 0) return skip(request, "no durable transcript cells available")
    const seen = new Set<string>()
    const duplicates: string[] = []
    for (const cell of cells) {
      const key = cellDedupeKey(cell)
      if (!key) continue
      if (seen.has(key)) duplicates.push(key)
      else seen.add(key)
    }
    return duplicates.length === 0
      ? pass(request, "durable transcript dedupe keys are unique", { durableCells: cells.length, keyedCells: seen.size })
      : fail(request, "duplicate durable transcript dedupe keys detected", { duplicates: Array.from(new Set(duplicates)).slice(0, 12) })
  },
  "LIVE-HANDOFF-CLEAN-WHEN-IDLE": (request, artifacts) => {
    if (latestPendingResponse(artifacts)) return pass(request, "pending response still active; handoff is not expected yet")
    const staleStreaming = invariantFns["LIVE-NO-STALE-STREAMING-WHEN-IDLE"](request, artifacts)
    if (staleStreaming.status === "fail") return staleStreaming
    const staleSlots = invariantFns["LIVE-NO-PENDING-SLOTS-WHEN-IDLE"](request, artifacts)
    if (staleSlots.status === "fail") return staleSlots
    return pass(request, "live regions have cleanly handed off or cleared after idle", {
      liveSlotCount: latestLiveSlots(artifacts).length,
      transcriptCellCount: latestTranscriptCells(artifacts).length,
    })
  },
  "RESIZE-ACTION-OBSERVED": (request, artifacts) => {
    const confirmations = artifacts.actionConfirmations ?? {}
    return confirmations.resizeRequested === true && confirmations.resizeObserved === true
      ? pass(request, "resize requested and observed")
      : fail(request, "resize action was not confirmed", confirmations)
  },
  "RESIZE-NO-DUP-LANDING": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.plainText ?? ""
    const count = (text.match(/BreadBoard v|Using Config|░█/g) ?? []).length
    return count <= 12 ? pass(request, "landing/header duplication not detected", { markerCount: count }) : fail(request, "landing/header marker count suggests duplication", { markerCount: count })
  },
  "RESIZE-NO-DUP-STATUS": (request, artifacts) => {
    const text = textBlob(artifacts)
    const logCount = (text.match(/Log link available|file:\/\/logging/g) ?? []).length
    return logCount <= 1 ? pass(request, "status/log duplication not detected", { logCount }) : fail(request, "status/log line duplicated", { logCount })
  },
  "RESIZE-NO-STALE-BORDER": (request, artifacts) => {
    const text = textBlob(artifacts)
    const stale = /(?:Disconnected|Reconnecting):[\s\S]{0,200}\n\s*[╭│╰]/.test(text)
    return stale ? fail(request, "stale lifecycle/modal border fragment detected") : pass(request, "no stale border fragment detected")
  },
  "LIFE-RECOVERY-NO-BODY-POLLUTION": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.viewportText ?? artifacts.plainText ?? ""
    const polluted = /\b(?:Reconnecting|Disconnected):\s/.test(text)
    return polluted ? fail(request, "lifecycle chrome appears in readable body") : pass(request, "no lifecycle body pollution detected")
  },
  "SCROLL-HOST-HISTORY-PREFIX-PRESERVED": (request, artifacts) => {
    const expected = artifacts.manifest?.hostHistorySentinels
    if (!Array.isArray(expected) || expected.length === 0) return skip(request, "no host history sentinels declared")
    const text = artifacts.scrollbackText ?? artifacts.plainText ?? ""
    const launchIndex = Math.max(text.indexOf("BreadBoard"), text.indexOf("Using Config"))
    const missing = expected.filter((item) => typeof item === "string" && !text.includes(item))
    const afterLaunch = expected.filter((item) => typeof item === "string" && launchIndex >= 0 && text.indexOf(item) > launchIndex)
    if (missing.length > 0) return fail(request, "host history prefix sentinels missing", { missing })
    return afterLaunch.length === 0 ? pass(request, "host history prefix appears before launch", { sentinels: expected.length }) : fail(request, "host history sentinel appears after launch marker", { afterLaunch })
  },
  "SCROLL-NO-ALTERNATE-SCREEN-HIJACK": (request, artifacts) => {
    const raw = artifacts.rawText ?? ""
    const entersAltScreen = /\x1b\[\?1049h|\x1b\[\?47h|\x1b\[\?1047h/.test(raw)
    return entersAltScreen ? fail(request, "alternate-screen enter sequence detected") : pass(request, "no alternate-screen enter sequence detected")
  },
  "SCROLL-NO-DESTRUCTIVE-CLEAR": (request, artifacts) => {
    const raw = artifacts.rawText ?? ""
    const clearsScrollback = /\x1b\[3J|\x1bc/.test(raw)
    return clearsScrollback ? fail(request, "destructive terminal clear sequence detected") : pass(request, "no destructive terminal clear sequence detected")
  },
  "SCROLL-LANDING-CARDINALITY": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.plainText ?? ""
    const breadboardCount = regexCount(text, /BreadBoard v/g)
    const configCount = regexCount(text, /Using Config/g)
    const bad = breadboardCount > 1 || configCount > 1
    return bad ? fail(request, "landing/header appears more than once", { breadboardCount, configCount }) : pass(request, "landing/header cardinality acceptable", { breadboardCount, configCount })
  },
  "SCROLL-PROMPT-CARDINALITY": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.plainText ?? ""
    const prompts = getAcceptedPrompts(artifacts)
    if (prompts.length === 0) return skip(request, "no accepted prompt known")
    const findings = prompts.map((prompt) => ({ prompt, count: countOccurrences(text, prompt) }))
    const bad = findings.filter((finding) => finding.count !== 1)
    return bad.length === 0 ? pass(request, "each known prompt appears exactly once", { findings }) : fail(request, "prompt cardinality mismatch", { bad, findings })
  },
  "SCROLL-ASSISTANT-CARDINALITY": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.plainText ?? ""
    const terms = expectedAssistantTerms(artifacts)
    if (terms.length === 0) return skip(request, "no expected assistant terms declared")
    const missing = terms.filter((term) => !text.includes(term))
    return missing.length === 0 ? pass(request, "expected assistant terms are present once or more", { terms }) : fail(request, "expected assistant terms missing", { missing })
  },
  "SCROLL-NO-DUPLICATE-ASSISTANT-TAIL": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.plainText ?? ""
    const expected = typeof artifacts.manifest?.expectedAssistantText === "string" ? artifacts.manifest.expectedAssistantText : ""
    const candidate = expected
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length >= 12 && !/^(?:[#>*`|:-]|\d+\.|- )/.test(line))
      .at(-1)
    if (!candidate) return skip(request, "no distinctive assistant tail line declared")
    const count = countOccurrences(text, candidate)
    return count <= 1
      ? pass(request, "assistant tail line is not duplicated", { candidate, count })
      : fail(request, "assistant tail line duplicated", { candidate, count })
  },
  "SCROLL-TOOL-CARDINALITY": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.plainText ?? ""
    const state = latestState(artifacts)
    const toolEvents = Array.isArray(state?.toolEvents) ? state.toolEvents : []
    const expectedToolBlocks = Math.max(1, toolEvents.filter((entry: any) => entry?.kind === "result" || entry?.status === "success").length)
    const toolBlocks = regexCount(text, /^\s*● Tool\b/gm)
    return toolBlocks <= expectedToolBlocks
      ? pass(request, "tool block cardinality within expected completed-tool count", { toolBlocks, expectedToolBlocks })
      : fail(request, "tool block cardinality suggests scrollback replay", { toolBlocks, expectedToolBlocks })
  },
  "SCROLL-ORDER-PRESERVED": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.plainText ?? ""
    const sentinels = Array.isArray(artifacts.manifest?.hostHistorySentinels) ? artifacts.manifest.hostHistorySentinels.filter((item): item is string => typeof item === "string") : []
    const prompt = getAcceptedPrompts(artifacts)[0]
    const launchIdx = Math.max(text.indexOf("BreadBoard"), text.indexOf("Using Config"))
    const sentinelIdx = sentinels.length ? text.indexOf(sentinels[0]) : -1
    const promptIdx = prompt ? text.indexOf(prompt) : -1
    const ok = (sentinels.length === 0 || (sentinelIdx >= 0 && (launchIdx < 0 || sentinelIdx < launchIdx))) && (!prompt || promptIdx >= 0)
    return ok ? pass(request, "basic scrollback order preserved", { sentinelIdx, launchIdx, promptIdx }) : fail(request, "basic scrollback order violated", { sentinelIdx, launchIdx, promptIdx })
  },
  "SCROLL-NO-CONTROL-CHROME-BODY": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.viewportText ?? artifacts.plainText ?? ""
    const patterns = [
      /^\s*(?:Disconnected|Reconnecting|Stream interruption|Log · file:\/\/)/m,
      /✗ Guardrail:/,
    ]
    const matched = patterns.find((pattern) => pattern.test(text))?.source
    return matched ? fail(request, "control chrome appears as body line", { pattern: matched }) : pass(request, "control chrome body pollution not detected")
  },
  "SCROLL-NO-INPUT-SEPARATOR-REPLAY": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.plainText ?? ""
    const inputSeparators = regexCount(text, /^\s*— input\s*$/gm)
    return inputSeparators <= 1
      ? pass(request, "input separator cardinality is bounded", { inputSeparators })
      : fail(request, "input separator replay detected", { inputSeparators })
  },
  "SCROLL-NO-COMPOSER-PROMPT-REPLAY": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.viewportText ?? artifacts.plainText ?? ""
    const composerPrompts = regexCount(text, /^\s*(?:[❯›]\s+)?Type your request/gm)
    return composerPrompts <= 1
      ? pass(request, "composer prompt cardinality is bounded", { composerPrompts })
      : fail(request, "composer prompt replay detected", { composerPrompts })
  },
  "MD-NO-STREAMING-PARTIAL-REPLAY": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.plainText ?? ""
    const expected = typeof artifacts.manifest?.expectedAssistantText === "string" ? artifacts.manifest.expectedAssistantText : ""
    const expectedHeadings = expected
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => /^#{1,6}\s+/.test(line))
      .map((line) => line.replace(/^#{1,6}\s+/, "").trim())
      .filter(Boolean)
    const headingFindings = expectedHeadings.map((heading) => ({
      heading,
      count: regexCount(text, new RegExp(`^\\s*#{0,6}\\s*${heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`, "gm")),
    }))
    const tableTopBorderCount = /\|\s*[-:]{3,}/.test(expected) ? regexCount(text, /^\s*┌.*┬/gm) : 0
    const badHeading = headingFindings.find((finding) => finding.count > 2)
    if (badHeading || tableTopBorderCount > 1) {
      return fail(request, "streaming markdown partial replay detected", { headingFindings, tableTopBorderCount })
    }
    return pass(request, "streaming markdown partial replay not detected", { headingFindings, tableTopBorderCount })
  },
  "SCROLL-COLLAPSE-AFFORDANCE-TRUTHFUL": (request, artifacts) => {
    const text = textBlob(artifacts)
    const matches = [...text.matchAll(/Scroll up for (\d+) earlier outputs/g)].map((match) => Number(match[1]))
    const invalid = matches.filter((value) => !Number.isFinite(value) || value <= 0)
    return invalid.length === 0 ? pass(request, "collapse affordance counts are positive when present", { counts: matches }) : fail(request, "collapse affordance count invalid", { invalid })
  },
  "RESIZE-COMPOSER-ALWAYS-VISIBLE": (request, artifacts) => invariantFns["GLOBAL-COMPOSER-VISIBLE"](request, artifacts),
  "RESIZE-FOOTER-TRUTHFUL": (request, artifacts) => invariantFns["GLOBAL-FOOTER-TRUTHFUL"](request, artifacts),
  "RESIZE-NO-DUPLICATE-STATIC-BLOCK": (request, artifacts) => {
    const landing = invariantFns["SCROLL-LANDING-CARDINALITY"](request, artifacts)
    if (landing.status === "fail") return landing
    return invariantFns["SCROLL-PROMPT-CARDINALITY"](request, artifacts)
  },
  "RESIZE-NO-FOOTER-GAP-EXPLOSION": (request, artifacts) => {
    const text = artifacts.viewportText ?? artifacts.plainText ?? ""
    const maxBlankRun = Math.max(0, ...text.split(/[^\n]*(?:\n|$)/).map((chunk) => chunk.length))
    const excessive = /\n\s*\n\s*\n\s*\n\s*\n\s*\n\s*\n/.test(text)
    return excessive ? fail(request, "large blank vertical gap detected", { maxBlankRun }) : pass(request, "no excessive footer/composer vertical gap detected")
  },
  "RESIZE-TINY-GRACEFUL-DEGRADATION": (request, artifacts) => {
    const cols = typeof artifacts.manifest?.cols === "number" ? artifacts.manifest.cols : 999
    const text = textBlob(artifacts)
    const corrupted = cols < 60 && /undefined|NaN|\[object Object\]/.test(text)
    return corrupted ? fail(request, "tiny-width corruption marker detected", { cols }) : pass(request, "tiny-width corruption marker not detected", { cols })
  },
  "MD-FINAL-TEXT-COMPLETE": (request, artifacts) => {
    const text = artifacts.scrollbackText ?? artifacts.plainText ?? ""
    const terms = expectedAssistantTerms(artifacts)
    if (terms.length === 0) return skip(request, "no markdown/assistant expectation declared")
    const missing = terms.filter((term) => !text.includes(term))
    return missing.length === 0 ? pass(request, "final rendered text includes expected semantic terms", { terms }) : fail(request, "final rendered text missing semantic terms", { missing })
  },
  "MD-LONG-URL-NO-COLLISION": (request, artifacts) => {
    const text = textBlob(artifacts)
    const hasLongUrl = /https?:\/\/\S{60,}/.test(text)
    const collision = hasLongUrl && /https?:\/\/\S{0,20}.*Type your request/.test(text)
    return collision ? fail(request, "long URL appears to collide with composer") : pass(request, "long URL composer collision not detected", { hasLongUrl })
  },
  "MD-DIFF-BLOCK-READABLE": (request, artifacts) => {
    const text = textBlob(artifacts)
    const hasDiff = /\*\*\* Begin Patch|^@@|^\+|\-/m.test(text)
    const unreadable = hasDiff && /undefined|NaN|\[object Object\]/.test(text)
    return unreadable ? fail(request, "diff block contains corruption marker") : pass(request, "diff block corruption marker not detected", { hasDiff })
  },
  "TOOL-START-VISIBLE": (request, artifacts) => {
    const confirmations = artifacts.actionConfirmations ?? {}
    if (confirmations.toolStarted === undefined) return skip(request, "tool start confirmation unavailable")
    return confirmations.toolStarted ? pass(request, "tool start confirmed visible or injected") : fail(request, "tool start not confirmed visible")
  },
  "TOOL-STDOUT-BOUNDED": (request, artifacts) => {
    const text = textBlob(artifacts)
    const stdoutLines = regexCount(text, /stdout \d+ line|stdout|│/gi)
    return stdoutLines <= 120 ? pass(request, "stdout marker count within broad bound", { stdoutLines }) : fail(request, "stdout marker count too high", { stdoutLines })
  },
  "TOOL-STDERR-VISIBLE": (request, artifacts) => {
    const text = textBlob(artifacts)
    const hasStderr = /stderr/i.test(text)
    return pass(request, "stderr visibility not required unless stderr is present", { hasStderr })
  },
  "TOOL-RESULT-SINGULAR": (request, artifacts) => {
    const confirmations = artifacts.actionConfirmations ?? {}
    if (confirmations.toolCompleted === undefined) return skip(request, "tool completion confirmation unavailable")
    if (!confirmations.toolCompleted) return fail(request, "tool completion not confirmed")
    const scrollbackToolCardinality = invariantFns["SCROLL-TOOL-CARDINALITY"](request, artifacts)
    if (scrollbackToolCardinality.status === "fail") return scrollbackToolCardinality
    return pass(request, "tool completion confirmed and singular", scrollbackToolCardinality.evidence)
  },
  "TOOL-COLLAPSE-ACCESSIBLE": (request, artifacts) => {
    const text = textBlob(artifacts)
    const collapsed = /Scroll up for \d+ earlier outputs/.test(text)
    const accessible = !collapsed || /Ctrl\+O Transcript|transcript/i.test(text)
    return accessible ? pass(request, "collapsed tool output is accessible or not present", { collapsed }) : fail(request, "collapsed tool output lacks access affordance")
  },
  "DIFF-SUMMARY-READABLE": (request, artifacts) => invariantFns["MD-DIFF-BLOCK-READABLE"](request, artifacts),
  "MODAL-OPEN-CONFIRMED": (request, artifacts) => {
    const value = artifacts.actionConfirmations?.modalOpened
    if (value === undefined) return skip(request, "modal open confirmation unavailable")
    return value ? pass(request, "modal open confirmed") : fail(request, "modal open not confirmed")
  },
  "MODAL-CLOSE-CONFIRMED": (request, artifacts) => {
    const value = artifacts.actionConfirmations?.modalClosed
    if (value === undefined) return skip(request, "modal close confirmation unavailable")
    return value ? pass(request, "modal close confirmed") : fail(request, "modal close not confirmed")
  },
  "MODAL-FOCUS-RETURNS-TO-COMPOSER": (request, artifacts) => {
    const value = artifacts.actionConfirmations?.focusReturned
    if (value === undefined) return skip(request, "focus return confirmation unavailable")
    return value ? pass(request, "focus returned to composer") : fail(request, "focus did not return to composer")
  },
  "OVERLAY-NO-DURABLE-MUTATION": (request, artifacts) => {
    const expectedPrompt = artifacts.manifest?.expectedPrompt
    if (typeof expectedPrompt === "string" && expectedPrompt.trim()) {
      return skip(request, "scenario contains a submitted prompt; overlay-only mutation contract not applicable")
    }
    const state = latestState(artifacts)
    const conversation = Array.isArray(state?.conversation) ? state.conversation : []
    const toolEvents = Array.isArray(state?.toolEvents) ? state.toolEvents : []
    const transcriptCells = latestTranscriptCells(artifacts)
    const durableTranscriptCells = transcriptCells.filter((cell) => {
      const source = String(cell?.source ?? "").trim().toLowerCase()
      const kind = String(cell?.kind ?? "").trim().toLowerCase()
      return source === "conversation" || source === "tool" || kind === "message" || kind === "tool"
    })
    const mutated = conversation.length > 0 || toolEvents.length > 0 || durableTranscriptCells.length > 0
    return mutated
      ? fail(request, "overlay-only scenario mutated durable transcript state", {
          conversationCount: conversation.length,
          toolEventCount: toolEvents.length,
          durableTranscriptCellCount: durableTranscriptCells.length,
          durableTranscriptCells: durableTranscriptCells.slice(0, 6),
        })
      : pass(request, "overlay-only scenario did not mutate durable transcript state", {
          conversationCount: conversation.length,
          toolEventCount: toolEvents.length,
          transcriptCellCount: transcriptCells.length,
        })
  },
  "COMPOSER-EMPTY-VISIBLE": (request, artifacts) => invariantFns["GLOBAL-COMPOSER-VISIBLE"](request, artifacts),
  "LIFE-DISCONNECT-STATUS-TRUTHFUL": (request, artifacts) => invariantFns["GLOBAL-NO-READY-LIE"](request, artifacts),
  "LIFE-RECOVERY-DOES-NOT-DUPLICATE-PROMPT": (request, artifacts) => invariantFns["GLOBAL-NO-DUPLICATE-PROMPT"](request, artifacts),
  "PERF-RAW-BYTE-BUDGET": (request, artifacts) => {
    const rawBytes = Number(artifacts.performanceMetrics?.rawBytes ?? 0)
    const budget = Number((artifacts.performanceMetrics?.budgets as any)?.maxRawBytes ?? 10_000_000)
    return rawBytes <= budget ? pass(request, "raw byte budget satisfied", { rawBytes, budget }) : fail(request, "raw byte budget exceeded", { rawBytes, budget })
  },
  "PERF-FRAME-COUNT-BUDGET": (request, artifacts) => {
    const frameCount = Number(artifacts.performanceMetrics?.frameCount ?? 0)
    const budget = Number((artifacts.performanceMetrics?.budgets as any)?.maxFrames ?? 5000)
    return frameCount <= budget ? pass(request, "frame count budget satisfied", { frameCount, budget }) : fail(request, "frame count budget exceeded", { frameCount, budget })
  },
  "PERF-STATE-DUMP-BUDGET": (request, artifacts) => {
    const stateDumpCount = Number(artifacts.performanceMetrics?.stateDumpCount ?? 0)
    const budget = Number((artifacts.performanceMetrics?.budgets as any)?.maxStateDumps ?? 5000)
    return stateDumpCount <= budget ? pass(request, "state dump budget satisfied", { stateDumpCount, budget }) : fail(request, "state dump budget exceeded", { stateDumpCount, budget })
  },
}

export const evaluateInvariants = async (
  artifactDir: string,
  scenarioId: string,
  lane: string,
  requests: InvariantRequest[],
): Promise<InvariantReport> => {
  const artifacts = await loadScenarioArtifacts(artifactDir, scenarioId, lane)
  const results = requests.map((request) => {
    const fn = invariantFns[request.id]
    return fn ? fn(request, artifacts) : skip(request, `invariant ${request.id} is not implemented yet`)
  })
  const ok = results.every((result) => result.status !== "fail")
  return { ok, scenarioId, lane, generatedAt: new Date().toISOString(), results }
}
