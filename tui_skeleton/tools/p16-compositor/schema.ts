import { promises as fs } from "node:fs"

export const P16_COMPOSITOR_SCHEMA_VERSION = 1 as const

export type P16CompositorSurface =
  | "landing"
  | "transcript"
  | "markdown"
  | "tool"
  | "model-picker"
  | "file-picker"
  | "transcript-viewer"
  | "diff-approval"
  | "permission"
  | "taskboard"
  | "multiagent"
  | "recovery"

export type P16KnownBadKind =
  | "duplicate-prompt"
  | "stale-overlay"
  | "landing-overflow"
  | "task-raw-log-spam"
  | "diff-header-clipping"
  | "invisible-active-row"
  | "footer-mismatch"
  | "ready-lie-recovery"
  | "hidden-composer"
  | "host-history-sentinel-loss"

export type P16CompositorInvariant =
  | "COMPOSITOR-PRODUCTION-RENDERER"
  | "COMPOSITOR-NO-FAKE-RENDERER"
  | "COMPOSITOR-ARTIFACT-PACKET-COMPLETE"
  | "COMPOSITOR-MARKERS-PRESENT"
  | "COMPOSITOR-FORBIDDEN-MARKERS-ABSENT"
  | "COMPOSITOR-WIDTH-SAFE"
  | "COMPOSITOR-COMPOSER-VISIBLE"
  | "COMPOSITOR-PROMPT-EXACTLY-ONCE"
  | "COMPOSITOR-NO-READY-LIE"
  | "COMPOSITOR-ACTIVE-ROW-VISIBLE"
  | "COMPOSITOR-RECOVERY-ACTIONABLE"
  | "COMPOSITOR-HOST-HISTORY-PRESERVED"

export interface P16SceneEvent {
  readonly kind:
    | "user"
    | "assistant"
    | "assistant-delta"
    | "tool"
    | "diff"
    | "permission"
    | "task"
    | "disconnect"
    | "reconnect"
    | "open-surface"
    | "resize"
    | "wait"
    | "workspace-file"
  readonly text?: string
  readonly id?: string
  readonly status?: string
  readonly surface?: P16CompositorSurface
  readonly cols?: number
  readonly rows?: number
  readonly filePath?: string
  readonly role?: string
  readonly content?: string
  readonly ms?: number
}

export interface P16CompositorScene {
  readonly schemaVersion: 1
  readonly id: string
  readonly title: string
  readonly description?: string
  readonly expectedOutcome: "pass" | "fail"
  readonly knownBadKind?: P16KnownBadKind
  readonly claimScope: "production-renderer-compositor"
  readonly productionEquivalence: {
    readonly required: true
    readonly renderer: "ReplView"
    readonly controllerPathsRequired?: readonly string[]
    readonly allowedSyntheticLayers: readonly ("model" | "tool" | "fault" | "time" | "workspace" | "task")[]
    readonly forbiddenBypass: readonly string[]
  }
  readonly surfaces: readonly P16CompositorSurface[]
  readonly sizes: readonly { readonly cols: number; readonly rows: number }[]
  readonly timeline: readonly P16SceneEvent[]
  readonly expectedMarkers: readonly string[]
  readonly forbiddenMarkers?: readonly string[]
  readonly invariants: readonly P16CompositorInvariant[]
  readonly artifacts: {
    readonly required: readonly string[]
  }
  readonly claimBoundary: string
}

export interface ValidationResult {
  readonly ok: boolean
  readonly errors: readonly string[]
}

const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value)
const nonEmpty = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0
const array = (value: unknown): value is unknown[] => Array.isArray(value)

const allowedSynthetic = new Set(["model", "tool", "fault", "time", "workspace", "task"])
const allowedSurfaces = new Set<P16CompositorSurface>([
  "landing",
  "transcript",
  "markdown",
  "tool",
  "model-picker",
  "file-picker",
  "transcript-viewer",
  "diff-approval",
  "permission",
  "taskboard",
  "multiagent",
  "recovery",
])
const allowedInvariants = new Set<P16CompositorInvariant>([
  "COMPOSITOR-PRODUCTION-RENDERER",
  "COMPOSITOR-NO-FAKE-RENDERER",
  "COMPOSITOR-ARTIFACT-PACKET-COMPLETE",
  "COMPOSITOR-MARKERS-PRESENT",
  "COMPOSITOR-FORBIDDEN-MARKERS-ABSENT",
  "COMPOSITOR-WIDTH-SAFE",
  "COMPOSITOR-COMPOSER-VISIBLE",
  "COMPOSITOR-PROMPT-EXACTLY-ONCE",
  "COMPOSITOR-NO-READY-LIE",
  "COMPOSITOR-ACTIVE-ROW-VISIBLE",
  "COMPOSITOR-RECOVERY-ACTIONABLE",
  "COMPOSITOR-HOST-HISTORY-PRESERVED",
])
const requiredArtifactNames = [
  "manifest.json",
  "command.txt",
  "git_status.txt",
  "environment.json",
  "scenario.json",
  "raw.ansi",
  "plain_final.txt",
  "grid_frames.ndjson",
  "state_snapshots.ndjson",
  "input_events.ndjson",
  "dimension_events.ndjson",
  "anomalies.json",
  "invariant_report.json",
  "screenshots/contact_sheet.png",
  "visual_qc_notes.md",
  "claim_delta.json",
]

export const requiredCompositorArtifacts = (): readonly string[] => requiredArtifactNames

export const validateP16CompositorScene = (input: unknown): ValidationResult => {
  const errors: string[] = []
  if (!object(input)) return { ok: false, errors: ["scene must be an object"] }

  if (input.schemaVersion !== P16_COMPOSITOR_SCHEMA_VERSION) errors.push("schemaVersion must be 1")
  if (!nonEmpty(input.id)) errors.push("id must be a non-empty string")
  if (!nonEmpty(input.title)) errors.push("title must be a non-empty string")
  if (input.expectedOutcome !== "pass" && input.expectedOutcome !== "fail") errors.push("expectedOutcome must be pass or fail")
  if (input.claimScope !== "production-renderer-compositor") errors.push("claimScope must be production-renderer-compositor")
  if (!nonEmpty(input.claimBoundary)) errors.push("claimBoundary must be a non-empty string")

  const productionEquivalence = input.productionEquivalence
  if (!object(productionEquivalence)) {
    errors.push("productionEquivalence must be an object")
  } else {
    if (productionEquivalence.required !== true) errors.push("productionEquivalence.required must be true")
    if (productionEquivalence.renderer !== "ReplView") errors.push("productionEquivalence.renderer must be ReplView")
    if (!array(productionEquivalence.allowedSyntheticLayers)) errors.push("allowedSyntheticLayers must be an array")
    if (array(productionEquivalence.allowedSyntheticLayers)) {
      for (const layer of productionEquivalence.allowedSyntheticLayers) {
        if (!allowedSynthetic.has(String(layer))) errors.push(`unknown synthetic layer ${String(layer)}`)
      }
    }
    if (!array(productionEquivalence.forbiddenBypass) || productionEquivalence.forbiddenBypass.length === 0) {
      errors.push("forbiddenBypass must list forbidden shortcut layers")
    }
    if (array(productionEquivalence.forbiddenBypass)) {
      const forbidden = productionEquivalence.forbiddenBypass.map(String)
      for (const required of ["renderer", "transcript-projection", "composer-footer", "modal-picker", "taskboard-state", "direct-cell-mutation"]) {
        if (!forbidden.includes(required)) errors.push(`forbiddenBypass must include ${required}`)
      }
    }
  }

  if (!array(input.surfaces) || input.surfaces.length === 0) errors.push("surfaces must be a non-empty array")
  if (array(input.surfaces)) {
    for (const surface of input.surfaces) {
      if (!allowedSurfaces.has(surface as P16CompositorSurface)) errors.push(`unknown surface ${String(surface)}`)
    }
  }

  if (!array(input.sizes) || input.sizes.length === 0) errors.push("sizes must be a non-empty array")
  if (array(input.sizes)) {
    for (const size of input.sizes) {
      if (!object(size) || typeof size.cols !== "number" || typeof size.rows !== "number" || size.cols < 20 || size.rows < 4) {
        errors.push("every size must include numeric cols>=20 and rows>=4")
      }
    }
  }

  if (!array(input.timeline) || input.timeline.length === 0) errors.push("timeline must be a non-empty array")
  if (array(input.timeline)) {
    for (const [index, event] of input.timeline.entries()) {
      if (!object(event) || !nonEmpty(event.kind)) errors.push(`timeline[${index}] must include kind`)
    }
  }

  if (!array(input.expectedMarkers) || input.expectedMarkers.length === 0) errors.push("expectedMarkers must be non-empty")
  if (!array(input.invariants) || input.invariants.length === 0) errors.push("invariants must be non-empty")
  if (array(input.invariants)) {
    for (const invariant of input.invariants) {
      if (!allowedInvariants.has(invariant as P16CompositorInvariant)) errors.push(`unknown invariant ${String(invariant)}`)
    }
  }

  const artifacts = input.artifacts
  if (!object(artifacts) || !array(artifacts.required)) {
    errors.push("artifacts.required must be an array")
  } else {
    const required = artifacts.required.map(String)
    for (const artifact of requiredArtifactNames) {
      if (!required.includes(artifact)) errors.push(`artifacts.required missing ${artifact}`)
    }
  }

  if (input.expectedOutcome === "fail" && !nonEmpty(input.knownBadKind)) errors.push("known-bad scenes must include knownBadKind")
  if (input.expectedOutcome === "pass" && nonEmpty(input.knownBadKind)) errors.push("passing scenes must not include knownBadKind")

  return { ok: errors.length === 0, errors }
}

export const parseP16CompositorScene = (input: unknown): P16CompositorScene => {
  const result = validateP16CompositorScene(input)
  if (!result.ok) throw new Error(result.errors.join("\n"))
  return input as P16CompositorScene
}

export const readP16CompositorScene = async (filePath: string): Promise<P16CompositorScene> => {
  const raw = await fs.readFile(filePath, "utf8")
  return parseP16CompositorScene(JSON.parse(raw))
}

export const p16CompositorJsonSchema = () => ({
  $schema: "https://json-schema.org/draft/2020-12/schema",
  title: "BreadBoard P16 Production-Renderer Compositor Scene",
  type: "object",
  required: [
    "schemaVersion",
    "id",
    "title",
    "expectedOutcome",
    "claimScope",
    "productionEquivalence",
    "surfaces",
    "sizes",
    "timeline",
    "expectedMarkers",
    "invariants",
    "artifacts",
    "claimBoundary",
  ],
  properties: {
    schemaVersion: { const: 1 },
    id: { type: "string", minLength: 1 },
    title: { type: "string", minLength: 1 },
    expectedOutcome: { enum: ["pass", "fail"] },
    knownBadKind: { type: "string" },
    claimScope: { const: "production-renderer-compositor" },
    productionEquivalence: {
      type: "object",
      required: ["required", "renderer", "allowedSyntheticLayers", "forbiddenBypass"],
      properties: {
        required: { const: true },
        renderer: { const: "ReplView" },
        controllerPathsRequired: { type: "array", items: { type: "string" } },
        allowedSyntheticLayers: { type: "array", items: { enum: [...allowedSynthetic] } },
        forbiddenBypass: { type: "array", items: { type: "string" } },
      },
    },
    surfaces: { type: "array", items: { enum: [...allowedSurfaces] }, minItems: 1 },
    sizes: { type: "array", items: { type: "object" }, minItems: 1 },
    timeline: { type: "array", items: { type: "object" }, minItems: 1 },
    expectedMarkers: { type: "array", items: { type: "string" }, minItems: 1 },
    forbiddenMarkers: { type: "array", items: { type: "string" } },
    invariants: { type: "array", items: { enum: [...allowedInvariants] }, minItems: 1 },
    artifacts: { type: "object" },
    claimBoundary: { type: "string", minLength: 1 },
  },
})
