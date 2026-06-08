import { promises as fs } from "node:fs"
import path from "node:path"
import { ensureDir, writeJson } from "./reports/artifactBundle"

interface Variant {
  readonly family: string
  readonly name: string
  readonly prompt: string
  readonly response: string
  readonly resize?: boolean
  readonly tool?: boolean
  readonly modal?: "slash" | "model-picker" | "file-picker" | "transcript" | "shortcuts"
  readonly markdown?: "long-list" | "table-rowwise" | "code-fence" | "mixed-mdx"
  readonly width?: number
  readonly rows?: number
  readonly seedTurns?: number
  readonly lifecycleShape?: boolean
}

const families: Record<string, Variant[]> = {
  scrollback: Array.from({ length: 15 }, (_, index) => ({
    family: "scrollback",
    name: `scrollback_${String(index + 1).padStart(2, "0")}`,
    prompt: `V6 scrollback prompt ${index + 1}`,
    response: `V6 scrollback response ${index + 1} remained ordered and singular.`,
    resize: index % 2 === 0,
    width: 72 + (index % 5) * 12,
    rows: 24 + (index % 4) * 3,
    seedTurns: index >= 8 ? 10 + index : undefined,
  })),
  markdown: Array.from({ length: 15 }, (_, index) => ({
    family: "markdown",
    name: `markdown_${String(index + 1).padStart(2, "0")}`,
    prompt: `V6 markdown prompt ${index + 1}`,
    response: `V6 markdown response ${index + 1} completed.`,
    resize: index % 3 === 0,
    markdown: (["long-list", "table-rowwise", "code-fence", "mixed-mdx"] as const)[index % 4],
    width: 80 + (index % 4) * 10,
    rows: 26 + (index % 3) * 4,
  })),
  tool: Array.from({ length: 12 }, (_, index) => ({
    family: "tool",
    name: `tool_${String(index + 1).padStart(2, "0")}`,
    prompt: `V6 tool prompt ${index + 1}`,
    response: `V6 tool response ${index + 1} completed after tool output.`,
    resize: index % 2 === 1,
    tool: true,
    width: 86 + (index % 4) * 10,
    rows: 28 + (index % 3) * 3,
  })),
  modal: Array.from({ length: 12 }, (_, index) => ({
    family: "modal",
    name: `modal_${String(index + 1).padStart(2, "0")}`,
    prompt: `V6 modal prompt ${index + 1}`,
    response: `V6 modal response ${index + 1} completed after focus recovery.`,
    resize: true,
    modal: (["slash", "model-picker", "transcript", "shortcuts"] as const)[index % 4],
    width: 82 + (index % 4) * 10,
    rows: 26 + (index % 3) * 3,
  })),
  composer: Array.from({ length: 8 }, (_, index) => ({
    family: "composer",
    name: `composer_${String(index + 1).padStart(2, "0")}`,
    prompt: `V6 composer prompt ${index + 1} with pasted-ish text line A line B`,
    response: `V6 composer response ${index + 1} preserved the input surface.`,
    resize: index % 2 === 0,
    width: 76 + (index % 4) * 12,
    rows: 24 + (index % 3) * 4,
  })),
  lifecycle: Array.from({ length: 10 }, (_, index) => ({
    family: "lifecycle",
    name: `lifecycle_${String(index + 1).padStart(2, "0")}`,
    prompt: `V6 lifecycle-shaped prompt ${index + 1}`,
    response: `V6 lifecycle-shaped response ${index + 1} completed without transcript pollution.`,
    resize: index % 2 === 0,
    lifecycleShape: true,
    width: 88 + (index % 3) * 10,
    rows: 26 + (index % 3) * 4,
  })),
  resize: Array.from({ length: 12 }, (_, index) => ({
    family: "resize",
    name: `resize_${String(index + 1).padStart(2, "0")}`,
    prompt: `V6 resize prompt ${index + 1}`,
    response: `V6 resize response ${index + 1} stayed stable after shrink and grow.`,
    resize: true,
    width: 64 + (index % 6) * 14,
    rows: 20 + (index % 5) * 4,
    seedTurns: index >= 6 ? 20 : undefined,
  })),
}

const scenarioFor = (variant: Variant) => {
  const id = `v6_${variant.family}_${variant.name}`
  const timeline: any[] = []
  if (variant.modal) {
    timeline.push({ kind: "open", surface: variant.modal })
    timeline.push({ kind: "wait", ms: 250 })
    timeline.push({ kind: "key", key: "escape" })
    timeline.push({ kind: "wait", ms: 400 })
  }
  timeline.push({ kind: "submit", text: variant.prompt })
  if (variant.tool) {
    timeline.push({ kind: "tool", event: { kind: "start", toolId: `${id}_tool`, name: "shell_command", args: { command: "printf v6" } } })
    timeline.push({ kind: "tool", event: { kind: "stdout", toolId: `${id}_tool`, text: `v6 tool output for ${id}\n` } })
    timeline.push({ kind: "tool", event: { kind: "result", toolId: `${id}_tool`, status: "ok", summary: `tool completed for ${id}`, output: "v6" } })
  }
  timeline.push({ kind: "assistant", event: { kind: "start", streamId: "s1" } })
  if (variant.markdown) timeline.push({ kind: "assistant", event: { kind: "markdown-fixture", streamId: "s1", fixture: variant.markdown, chunking: { mode: variant.name.endsWith("01") ? "char" : "syntax" } } })
  timeline.push({ kind: "assistant", event: { kind: "delta", streamId: "s1", text: variant.response } })
  timeline.push({ kind: "assistant", event: { kind: "complete", streamId: "s1" } })
  if (variant.modal) timeline.push({ kind: "wait", ms: 500 })
  timeline.push({ kind: "waitFor", target: { text: variant.response }, timeoutMs: 60000 })
  if (variant.resize) {
    timeline.push({ kind: "resize", cols: Math.max(52, (variant.width ?? 100) - 18), rows: Math.max(12, (variant.rows ?? 30) - 3), settleMs: 200 })
    timeline.push({ kind: "resize", cols: variant.width ?? 100, rows: variant.rows ?? 30, settleMs: 200 })
  }
  timeline.push({ kind: "checkpoint", id: "final" })

  const invariants = [
    { id: "GLOBAL-HOST-HISTORY-PRESERVED", severity: "blocker" },
    { id: "SCROLL-PROMPT-CARDINALITY", severity: "blocker" },
    { id: "SCROLL-ASSISTANT-CARDINALITY", severity: "blocker" },
    { id: "GLOBAL-COMPOSER-VISIBLE", severity: "blocker" },
    { id: "GLOBAL-NO-RAW-ANSI", severity: "blocker" },
  ]
  if (variant.resize) invariants.push({ id: "RESIZE-ACTION-OBSERVED", severity: "blocker" }, { id: "RESIZE-COMPOSER-ALWAYS-VISIBLE", severity: "blocker" })
  if (variant.modal) invariants.push({ id: "MODAL-OPEN-CONFIRMED", severity: "blocker" }, { id: "MODAL-FOCUS-RETURNS-TO-COMPOSER", severity: "blocker" })
  if (variant.tool) invariants.push({ id: "TOOL-START-VISIBLE", severity: "blocker" }, { id: "TOOL-RESULT-SINGULAR", severity: "blocker" })
  if (variant.markdown) invariants.push({ id: "MD-FINAL-TEXT-COMPLETE", severity: "blocker" })
  if (variant.lifecycleShape) invariants.push({ id: "SCROLL-NO-CONTROL-CHROME-BODY", severity: "blocker" })

  return {
    schemaVersion: 1,
    id,
    title: `Generated ${variant.family} scenario ${variant.name}`,
    family: variant.family,
    campaign: "p14-v6-scenario-family-explosion",
    claimScope: "pty",
    productionEquivalence: {
      required: true,
      claimTier: "S2",
      allowedSyntheticLayers: ["model", "tool", "time", "workspace"],
      forbiddenBypass: ["renderer", "transcript-store", "composer", "scrollback-feed"],
    },
    environment: {
      dummyWorkspace: { gitInit: true, files: [{ path: "README.md", content: `# ${id}\n` }] },
      prelaunchShellHistory: [{ id: "pre", text: `BB_${id.toUpperCase()}_PRE` }],
    },
    launch: { mode: "classic", engineMode: "test-owned", startupWait: { composerReady: true, timeoutMs: 20000 }, timeoutMs: 180000 },
    terminal: { lanes: ["pty"], initial: { cols: variant.width ?? 100, rows: variant.rows ?? 30 }, resizePolicy: variant.resize ? "scripted" : "none", captureGrid: true, captureScrollback: true },
    fixtures: variant.seedTurns ? { seedTurns: { count: variant.seedTurns, throughProductionIngestion: true, contentMix: ["plain", "markdown"] } } : undefined,
    timeline,
    invariants,
    actionRequirements: variant.modal ? { modalOpened: "required", focusReturned: "required" } : undefined,
    performanceBudget: { maxDurationMs: 180000, maxRawBytes: 7000000, maxFrames: 1500, maxStateDumps: 2500 },
    transcriptExpectation: { promptCardinality: "exactly-once", assistantCardinality: "at-least-once", toolCardinality: variant.tool ? "at-least-once" : "not-required" },
    artifacts: { required: ["manifest", "raw", "grid", "state", "invariant-report"] },
  }
}

const main = async () => {
  const root = path.join(process.cwd(), "scenarios/v6/generated")
  await ensureDir(root)
  const all: string[] = []
  for (const [family, variants] of Object.entries(families)) {
    const familyRoot = path.join(root, family)
    await ensureDir(familyRoot)
    for (const variant of variants) {
      const scenario = scenarioFor(variant)
      const target = path.join(familyRoot, `${scenario.id}.json`)
      await writeJson(target, scenario)
      all.push(path.relative(process.cwd(), target))
    }
  }
  await ensureDir(path.join(process.cwd(), "scenarios/v6/batches"))
  await writeJson(path.join(process.cwd(), "scenarios/v6/batches/generated_all.json"), { id: "v6_generated_all", campaign: "p14-v6-scenario-family-explosion", scenarios: all })
  await writeJson(path.join(process.cwd(), "scenarios/v6/batches/generated_sample.json"), { id: "v6_generated_sample", campaign: "p14-v6-scenario-family-explosion", scenarios: all.filter((_, index) => index % 10 === 0).slice(0, 10) })
  console.log(`[scenario:generate-v6] wrote ${all.length} scenarios`)
}

await main()
