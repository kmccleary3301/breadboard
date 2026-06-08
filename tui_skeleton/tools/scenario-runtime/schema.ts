export const SCENARIO_SCHEMA_VERSION = 1 as const

export type ClaimScope = "semantic" | "pty" | "real-terminal" | "lifecycle" | "support-lane" | "fuzz"
export type ScenarioLane = "pty" | "ghostty" | "wezterm" | "vscode" | "tmux"
export type EngineMode = "local-owned" | "test-owned" | "external" | "remote"
export type InvariantSeverity = "blocker" | "warning" | "metric" | "human-aid"
export type ClaimTier = "S0" | "S1" | "S2" | "S3" | "S4"

export interface Scenario {
  readonly schemaVersion: 1
  readonly id: string
  readonly title?: string
  readonly description?: string
  readonly tags?: string[]
  readonly family?: string
  readonly campaign?: string
  readonly expectedOutcome?: ExpectedOutcomeSpec
  readonly knownFailureClass?: string
  readonly claimScope: ClaimScope
  readonly productionEquivalence: ProductionEquivalenceSpec
  readonly environment: ScenarioEnvironment
  readonly launch: LaunchSpec
  readonly terminal: TerminalSpec
  readonly fixtures?: FixtureSpec
  readonly timeline: ScenarioStep[]
  readonly invariants: InvariantSpec[]
  readonly artifacts: ArtifactSpec
  readonly actionRequirements?: ActionRequirementSpec
  readonly terminalMatrix?: Record<string, TerminalLaneExpectation>
  readonly performanceBudget?: PerformanceBudgetSpec
  readonly visualEvidence?: VisualEvidenceSpec
  readonly transcriptExpectation?: TranscriptExpectationSpec
  readonly hotRegionExpectation?: HotRegionExpectationSpec
  readonly mutationProfile?: MutationProfileSpec
  readonly repeat?: RepeatSpec
  readonly minimization?: MinimizationSpec
  readonly sourceCapture?: SourceCaptureSpec
}

export interface ExpectedOutcomeSpec {
  readonly verdict: "pass" | "fail"
  readonly failureClass?: string
  readonly reason?: string
}

export interface ProductionEquivalenceSpec {
  readonly required: boolean
  readonly claimTier?: ClaimTier
  readonly allowedSyntheticLayers: Array<"model" | "tool" | "fault" | "time" | "workspace">
  readonly forbiddenBypass: string[]
}

export interface ScenarioEnvironment {
  readonly cwd?: string
  readonly env?: Record<string, string>
  readonly shell?: string
  readonly dummyWorkspace?: {
    readonly files: Array<{ readonly path: string; readonly content: string }>
    readonly gitInit?: boolean
  }
  readonly prelaunchShellHistory?: Array<{
    readonly id: string
    readonly text: string
    readonly kind?: "short" | "long-wrap" | "unicode" | "ansi-probe"
  }>
}

export interface LaunchSpec {
  readonly command?: string
  readonly args?: string[]
  readonly mode?: "classic" | "scrollback" | "owned-live" | "test-owned"
  readonly engineMode?: EngineMode
  readonly configPath?: string
  readonly startupWait?: WaitSpec
  readonly timeoutMs?: number
}

export interface TerminalSpec {
  readonly lanes: ScenarioLane[]
  readonly initial: { readonly cols: number; readonly rows: number }
  readonly resizePolicy?: "none" | "scripted" | "fuzzed"
  readonly captureScreenshots?: boolean
  readonly captureGrid?: boolean
  readonly captureScrollback?: boolean
}

export interface TerminalLaneExpectation {
  readonly expectedOutcome?: "pass" | "fail" | "classified-deferral"
  readonly requiredEvidence?: string[]
  readonly notes?: string
}

export interface PerformanceBudgetSpec {
  readonly maxDurationMs?: number
  readonly maxRawBytes?: number
  readonly maxFrames?: number
  readonly maxStateDumps?: number
  readonly maxResizeSettleMs?: number
}

export interface VisualEvidenceSpec {
  readonly screenshots?: "required" | "optional" | "not-supported"
  readonly textExport?: "required" | "optional"
  readonly scrollbackExport?: "required" | "optional"
}

export interface TranscriptExpectationSpec {
  readonly promptCardinality?: "exactly-once" | "at-least-once" | "not-required"
  readonly assistantCardinality?: "exactly-once" | "at-least-once" | "not-required"
  readonly toolCardinality?: "exactly-once" | "at-least-once" | "not-required"
}

export interface HotRegionExpectationSpec {
  readonly maxRows?: number
  readonly freezeAfterCheckpoint?: string
}

export interface MutationProfileSpec {
  readonly seed?: number
  readonly dimensions?: string[]
}

export interface RepeatSpec {
  readonly count: number
  readonly steps?: ScenarioStep[]
}

export type ActionRequirementName =
  | "launchObserved"
  | "sentinelObservedBeforeLaunch"
  | "inputSubmitted"
  | "assistantStreamStarted"
  | "assistantStreamCompleted"
  | "assistantInjected"
  | "toolStarted"
  | "toolCompleted"
  | "resizeRequested"
  | "resizeObserved"
  | "modalOpened"
  | "modalClosed"
  | "focusReturned"
  | "commandAccepted"
  | "fileInserted"
  | "modelSelected"
  | "transcriptOpened"
  | "transcriptReturned"
  | "lifecycleFaultInjected"
  | "recoveryCompleted"
  | "waitTargetsObserved"
  | "stateDumpObservedAfterSubmit"
  | "stateDumpObservedAfterResize"
  | "artifactCaptureComplete"

export type ActionRequirementValue = "required" | "optional" | "observation-only"
export type ActionRequirementSpec = Partial<Record<ActionRequirementName, ActionRequirementValue>>

export interface FixtureSpec {
  readonly seedTurns?: {
    readonly count: number
    readonly throughProductionIngestion?: boolean
    readonly contentMix?: string[]
  }
  readonly mockSseScript?: string
}

export interface WaitSpec {
  readonly text?: string
  readonly composerReady?: boolean
  readonly state?: Record<string, unknown>
  readonly timeoutMs?: number
  readonly stableMs?: number
}

export type KeyName =
  | "enter"
  | "escape"
  | "tab"
  | "space"
  | "backspace"
  | "ctrl+backspace"
  | "ctrl+c"
  | "ctrl+d"
  | "ctrl+b"
  | "ctrl+g"
  | "ctrl+z"
  | "ctrl+y"
  | "ctrl+k"
  | "ctrl+l"
  | "ctrl+p"
  | "ctrl+o"
  | "ctrl+t"
  | "ctrl+v"
  | "ctrl+left"
  | "ctrl+right"
  | "up"
  | "down"
  | "left"
  | "right"
  | "pageup"
  | "pagedown"
  | "home"
  | "end"

export type ScenarioStep =
  | { readonly kind: "wait"; readonly ms: number }
  | { readonly kind: "type"; readonly text: string; readonly delayMs?: number }
  | { readonly kind: "paste"; readonly text: string; readonly bracketed?: boolean }
  | { readonly kind: "key"; readonly key: KeyName; readonly repeat?: number; readonly delayMs?: number }
  | { readonly kind: "submit"; readonly text?: string }
  | { readonly kind: "resize"; readonly cols: number; readonly rows: number; readonly settleMs?: number }
  | { readonly kind: "open"; readonly surface: "slash" | "model-picker" | "file-picker" | "transcript" | "shortcuts" | "tasks" }
  | { readonly kind: "navigate"; readonly keys: KeyName[] }
  | { readonly kind: "engine"; readonly event: EngineEventStep }
  | { readonly kind: "assistant"; readonly event: AssistantEventStep }
  | { readonly kind: "tool"; readonly event: ToolEventStep }
  | { readonly kind: "subagent"; readonly event: SubagentEventStep }
  | { readonly kind: "fault"; readonly event: FaultEventStep }
  | { readonly kind: "waitFor"; readonly target: WaitTarget; readonly timeoutMs?: number; readonly stableMs?: number }
  | { readonly kind: "checkpoint"; readonly id: string; readonly capture?: CaptureSpec }
  | { readonly kind: "assert"; readonly invariantIds: string[] }

export interface EngineEventStep {
  readonly kind: string
  readonly payload?: Record<string, unknown>
}

export type AssistantEventStep =
  | { readonly kind: "start"; readonly streamId: string; readonly messageId?: string; readonly turn?: number }
  | { readonly kind: "delta"; readonly streamId: string; readonly text: string; readonly chunkId?: string; readonly delayMs?: number; readonly turn?: number }
  | { readonly kind: "markdown-fixture"; readonly streamId: string; readonly fixture: "long-list" | "table-rowwise" | "code-fence" | "mixed-mdx"; readonly chunking?: ChunkingSpec; readonly turn?: number }
  | { readonly kind: "complete"; readonly streamId: string; readonly finishReason?: string; readonly turn?: number }
  | { readonly kind: "error"; readonly streamId: string; readonly message: string; readonly turn?: number }

export interface ChunkingSpec {
  readonly mode?: "whole" | "char" | "random" | "syntax"
  readonly seed?: number
}

export type ToolEventStep =
  | { readonly kind: "start"; readonly toolId: string; readonly name: string; readonly args?: unknown; readonly turn?: number }
  | { readonly kind: "stdout"; readonly toolId: string; readonly text: string; readonly stream?: boolean; readonly turn?: number }
  | { readonly kind: "stderr"; readonly toolId: string; readonly text: string; readonly stream?: boolean; readonly turn?: number }
  | { readonly kind: "diff"; readonly toolId: string; readonly patch: string; readonly turn?: number }
  | { readonly kind: "approval"; readonly toolId: string; readonly approvalType: "exec" | "file" | "network" | "patch"; readonly payload: unknown; readonly turn?: number }
  | { readonly kind: "result"; readonly toolId: string; readonly status: "ok" | "error"; readonly summary?: string; readonly output?: string; readonly turn?: number }

export interface SubagentEventStep {
  readonly kind: string
  readonly payload?: Record<string, unknown>
}

export type FaultEventStep =
  | { readonly kind: "engine-death"; readonly phase: "idle" | "after-user-echo" | "assistant-delta" | "tool-stdout" | "tool-result"; readonly signal?: string }
  | { readonly kind: "stream-stall"; readonly streamId: string; readonly durationMs: number }
  | { readonly kind: "disconnect"; readonly reason: string }
  | { readonly kind: "replay-duplicate"; readonly eventIds: string[] }
  | { readonly kind: "event-zero-replay"; readonly from: "start" | "checkpoint" }
  | { readonly kind: "stale-generation"; readonly streamId: string; readonly staleEvents: AssistantEventStep[] }

export interface WaitTarget {
  readonly text?: string
  readonly composerReady?: boolean
  readonly state?: Record<string, unknown>
}

export interface CaptureSpec {
  readonly stateDump?: boolean
  readonly transcriptCells?: boolean
  readonly rawBytes?: boolean
  readonly grid?: boolean
  readonly scrollback?: boolean
  readonly screenshot?: boolean
  readonly surfaceModel?: boolean
  readonly engineLifecycle?: boolean
  readonly markdownPaint?: boolean
}

export interface InvariantSpec {
  readonly id: string
  readonly severity: InvariantSeverity
}

export interface ArtifactSpec {
  readonly required: string[]
}

export interface MinimizationSpec {
  readonly enabled: boolean
  readonly preserveSteps?: string[]
}

export interface SourceCaptureSpec {
  readonly captureId: string
  readonly artifactRoot?: string
  readonly conversionVersion?: number
  readonly droppedEvents?: string[]
  readonly timingMode?: "exact" | "logical-checkpoint" | "jitter"
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value)
const isString = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value)
const isStringArray = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string")

export interface ValidationResult {
  readonly ok: boolean
  readonly errors: string[]
  readonly warnings: string[]
}

const VALID_CLAIM_SCOPES = new Set<ClaimScope>(["semantic", "pty", "real-terminal", "lifecycle", "support-lane", "fuzz"])
const VALID_LANES = new Set<ScenarioLane>(["pty", "ghostty", "wezterm", "vscode", "tmux"])
const VALID_SYNTHETIC = new Set(["model", "tool", "fault", "time", "workspace"])
const VALID_SEVERITY = new Set<InvariantSeverity>(["blocker", "warning", "metric", "human-aid"])

export const validateScenario = (value: unknown): ValidationResult => {
  const errors: string[] = []
  const warnings: string[] = []
  const fail = (message: string) => errors.push(message)
  const warn = (message: string) => warnings.push(message)

  if (!isRecord(value)) {
    return { ok: false, errors: ["scenario must be an object"], warnings }
  }
  if (value.schemaVersion !== SCENARIO_SCHEMA_VERSION) fail(`schemaVersion must be ${SCENARIO_SCHEMA_VERSION}`)
  if (!isString(value.id) || !/^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(String(value.id))) fail("id must be a stable filesystem-safe string")
  if (!VALID_CLAIM_SCOPES.has(value.claimScope as ClaimScope)) fail("claimScope is invalid")

  const production = value.productionEquivalence
  if (!isRecord(production)) {
    fail("productionEquivalence object is required")
  } else {
    if (typeof production.required !== "boolean") fail("productionEquivalence.required must be boolean")
    if (!Array.isArray(production.allowedSyntheticLayers)) fail("productionEquivalence.allowedSyntheticLayers must be an array")
    else {
      for (const layer of production.allowedSyntheticLayers) {
        if (!VALID_SYNTHETIC.has(String(layer))) fail(`unknown synthetic layer: ${String(layer)}`)
      }
    }
    if (!isStringArray(production.forbiddenBypass)) fail("productionEquivalence.forbiddenBypass must be string[]")
    if (production.required === true && Array.isArray(production.forbiddenBypass) && production.forbiddenBypass.length === 0) {
      fail("production-equivalent scenarios must list forbidden bypasses")
    }
  }

  const terminal = value.terminal
  const lanes = isRecord(terminal) && Array.isArray(terminal.lanes) ? terminal.lanes.map(String) : []
  if (!isRecord(terminal)) {
    fail("terminal object is required")
  } else {
    if (!Array.isArray(terminal.lanes) || terminal.lanes.length === 0) fail("terminal.lanes must be non-empty")
    for (const lane of lanes) if (!VALID_LANES.has(lane as ScenarioLane)) fail(`invalid terminal lane: ${lane}`)
    if (!isRecord(terminal.initial)) fail("terminal.initial is required")
    else {
      if (!isNumber(terminal.initial.cols) || terminal.initial.cols < 20) fail("terminal.initial.cols must be >= 20")
      if (!isNumber(terminal.initial.rows) || terminal.initial.rows < 8) fail("terminal.initial.rows must be >= 8")
    }
  }

  if (value.claimScope === "real-terminal" && lanes.every((lane) => lane === "pty")) {
    fail("real-terminal claim requires at least one non-PTY lane")
  }

  const launch = value.launch
  if (!isRecord(launch)) fail("launch object is required")

  if (value.expectedOutcome !== undefined) {
    if (!isRecord(value.expectedOutcome)) fail("expectedOutcome must be an object")
    else if (value.expectedOutcome.verdict !== "pass" && value.expectedOutcome.verdict !== "fail") fail("expectedOutcome.verdict must be pass or fail")
  }

  if (value.actionRequirements !== undefined) {
    if (!isRecord(value.actionRequirements)) fail("actionRequirements must be an object")
    else {
      for (const [key, raw] of Object.entries(value.actionRequirements)) {
        if (raw !== "required" && raw !== "optional" && raw !== "observation-only") fail(`actionRequirements.${key} is invalid`)
      }
    }
  }

  if (value.repeat !== undefined) {
    if (!isRecord(value.repeat)) fail("repeat must be an object")
    else if (!isNumber(value.repeat.count) || value.repeat.count < 1) fail("repeat.count must be >= 1")
  }

  if (value.performanceBudget !== undefined) {
    if (!isRecord(value.performanceBudget)) fail("performanceBudget must be an object")
    else {
      for (const [key, raw] of Object.entries(value.performanceBudget)) {
        if (!isNumber(raw) || raw < 0) fail(`performanceBudget.${key} must be a non-negative number`)
      }
    }
  }

  if (value.terminalMatrix !== undefined) {
    if (!isRecord(value.terminalMatrix)) fail("terminalMatrix must be an object")
    else {
      for (const [lane, expectation] of Object.entries(value.terminalMatrix)) {
        if (!VALID_LANES.has(lane as ScenarioLane)) fail(`terminalMatrix lane is invalid: ${lane}`)
        if (!isRecord(expectation)) fail(`terminalMatrix.${lane} must be an object`)
      }
    }
  }

  const timeline = Array.isArray(value.timeline) ? value.timeline : null
  if (!timeline) fail("timeline must be an array")
  else if (timeline.length === 0) warn("timeline is empty")

  const invariants = Array.isArray(value.invariants) ? value.invariants : null
  if (!invariants) fail("invariants must be an array")
  else {
    for (const [index, invariant] of invariants.entries()) {
      if (!isRecord(invariant)) fail(`invariant[${index}] must be object`)
      else {
        if (!isString(invariant.id)) fail(`invariant[${index}].id is required`)
        if (!VALID_SEVERITY.has(invariant.severity as InvariantSeverity)) fail(`invariant[${index}].severity is invalid`)
      }
    }
  }

  const artifacts = value.artifacts
  if (!isRecord(artifacts)) fail("artifacts object is required")
  else if (!isStringArray(artifacts.required)) fail("artifacts.required must be string[]")

  const hasResizeStep = timeline?.some((step) => isRecord(step) && step.kind === "resize") ?? false
  const hasFaultStep = timeline?.some((step) => isRecord(step) && step.kind === "fault") ?? false
  const hasBlockerInvariant = invariants?.some((item) => isRecord(item) && item.severity === "blocker") ?? false
  const requiredArtifacts = isRecord(artifacts) && Array.isArray(artifacts.required) ? artifacts.required.map(String) : []

  if (hasBlockerInvariant && production && isRecord(production) && production.required === true) {
    for (const required of ["manifest", "raw", "grid", "state", "invariant-report"]) {
      if (!requiredArtifacts.includes(required)) fail(`production-equivalent blocker scenarios require artifact: ${required}`)
    }
  }
  if ((invariants ?? []).some((item) => isRecord(item) && String(item.id).startsWith("RESIZE-")) && !hasResizeStep) {
    fail("resize invariant requires at least one resize step")
  }
  if ((invariants ?? []).some((item) => isRecord(item) && String(item.id).startsWith("LIFE-")) && !hasFaultStep && value.claimScope === "lifecycle") {
    warn("lifecycle scenario has no fault step; ensure lifecycle is exercised by command or replay")
  }
  if (hasFaultStep && isRecord(launch) && launch.engineMode !== "local-owned" && launch.engineMode !== "test-owned") {
    fail("fault steps require launch.engineMode local-owned or test-owned")
  }

  return { ok: errors.length === 0, errors, warnings }
}

export const parseScenarioJson = (text: string): Scenario => {
  const parsed = JSON.parse(text) as unknown
  const validation = validateScenario(parsed)
  if (!validation.ok) {
    throw new Error(`Invalid scenario:\n${validation.errors.map((error) => `- ${error}`).join("\n")}`)
  }
  return parsed as Scenario
}
