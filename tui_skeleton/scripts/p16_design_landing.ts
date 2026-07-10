import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { spawn } from "node:child_process"
import { fileURLToPath } from "node:url"

import {
  buildLandingContext,
  buildScrollbackLandingLines,
  resolveLandingVariantForViewport,
  type LandingVariant,
} from "../src/repl/components/replView/landing/scrollbackLanding.tsx"
import { stripAnsiCodes, visibleWidth } from "../src/repl/components/replView/utils/ansi.ts"

const __filename = fileURLToPath(import.meta.url)
const ROOT_DIR = path.resolve(path.dirname(__filename), "..")
const P16_DIR = path.resolve(ROOT_DIR, "..", "docs_tmp", "cli_phase_6", "CODESIGN_p16", "implementation_validation_p16_final_design_complete")

type Size = { readonly cols: number; readonly rows: number }
type VariantCase = {
  readonly label: string
  readonly viewport: Size
  readonly preferred: "auto" | LandingVariant
  readonly frozenScrollback: boolean
}

type CaseReport = {
  readonly label: string
  readonly cols: number
  readonly rows: number
  readonly preferred: string
  readonly resolved: string
  readonly renderedRows: number
  readonly maxVisibleWidth: number
  readonly widthPass: boolean
  readonly budgetPass: boolean
  readonly composerFooterReserve: number
  readonly lines: readonly string[]
  readonly failures: readonly string[]
}

const escapeXml = (value: string): string =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")

const timestamp = (): string => {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, "0")
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

const parseArgs = (): { outDir: string } => {
  const args = process.argv.slice(2)
  let outDir = path.join(P16_DIR, "artifacts", "design", `landing_${timestamp()}`)
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--out-dir") outDir = path.resolve(args[++index] ?? outDir)
  }
  return { outDir }
}

const runConvert = (svgPath: string, pngPath: string): Promise<boolean> =>
  new Promise((resolve) => {
    const child = spawn("convert", [svgPath, pngPath], { stdio: "ignore" })
    child.on("error", () => resolve(false))
    child.on("exit", (code) => resolve(code === 0))
  })

const stripLines = (lines: readonly string[]): string[] => lines.map((line) => stripAnsiCodes(line))

const renderCase = (input: VariantCase): CaseReport => {
  const contentWidth = input.frozenScrollback ? Math.max(10, Math.min(input.viewport.cols, 88)) : input.viewport.cols
  const composerFooterReserve = 4
  const maxRows = Math.max(0, input.viewport.rows - composerFooterReserve)
  const resolved = input.preferred === "auto"
    ? resolveLandingVariantForViewport({
        contentWidth,
        maxRows,
        preferredVariant: "auto",
        modelLabel: "gpt-5.4-mini",
        chromeLabel: "Codex",
        configLabel: "Codex",
        cwd: "/tmp/bb_p16_visual_dummy",
        borderStyle: "round",
        showAsciiArt: true,
      })
    : input.preferred
  const lines = stripLines(
    buildScrollbackLandingLines(
      buildLandingContext({
        contentWidth,
        modelLabel: "gpt-5.4-mini",
        chromeLabel: "Codex",
        configLabel: "Codex",
        cwd: "/tmp/bb_p16_visual_dummy",
        sessionLabel: "s p16b001",
        statusLabel: "turn 0",
        variant: resolved,
        borderStyle: "round",
        showAsciiArt: true,
      }),
    ),
  )
  const widths = lines.map((line) => visibleWidth(line))
  const maxVisibleWidth = widths.length > 0 ? Math.max(...widths) : 0
  const widthPass = maxVisibleWidth <= contentWidth
  const budgetPass = lines.length <= maxRows
  const failures: string[] = []
  if (!widthPass) failures.push(`max visible width ${maxVisibleWidth} exceeds content width ${contentWidth}`)
  if (!budgetPass) failures.push(`rendered rows ${lines.length} exceeds body budget ${maxRows}`)
  if (resolved !== "suppressed" && lines.length === 0) failures.push("non-suppressed variant rendered no lines")
  return {
    label: input.label,
    cols: input.viewport.cols,
    rows: input.viewport.rows,
    preferred: input.preferred,
    resolved,
    renderedRows: lines.length,
    maxVisibleWidth,
    widthPass,
    budgetPass,
    composerFooterReserve,
    lines,
    failures,
  }
}

const buildMarkdown = (reports: readonly CaseReport[]): string => {
  const parts = [
    "# P16 Landing Variant Contact Sheet",
    "",
    `Generated: ${new Date().toISOString()}`,
    "Source: production `buildScrollbackLandingLines` renderer, ANSI stripped for review.",
    "",
  ]
  for (const report of reports) {
    parts.push(
      `## ${report.label}`,
      "",
      `- size: ${report.cols}x${report.rows}`,
      `- preferred: ${report.preferred}`,
      `- resolved: ${report.resolved}`,
      `- rendered rows: ${report.renderedRows}`,
      `- max visible width: ${report.maxVisibleWidth}`,
      `- width pass: ${report.widthPass}`,
      `- row-budget pass: ${report.budgetPass}`,
      "",
      "```text",
      ...report.lines,
      "```",
      "",
    )
  }
  return `${parts.join("\n")}\n`
}

const buildSvg = (reports: readonly CaseReport[]): string => {
  const charWidth = 8
  const lineHeight = 18
  const panelGap = 28
  const panelPad = 18
  const panelWidth = 1_240
  const panelHeights = reports.map((report) => panelPad * 2 + lineHeight * (report.lines.length + 4))
  const width = panelWidth + panelPad * 2
  const headerRows = 2
  const headerHeight = headerRows * lineHeight + panelPad
  const height = panelHeights.reduce((sum, value) => sum + value, 0) + panelGap * (reports.length - 1) + panelPad * 2 + headerHeight
  let y = panelPad + headerHeight
  const panels: string[] = []
  for (let index = 0; index < reports.length; index += 1) {
    const report = reports[index]
    const panelHeight = panelHeights[index] ?? 160
    const status = report.failures.length === 0 ? "PASS" : "FAIL"
    panels.push(`<rect x="${panelPad}" y="${y}" width="${panelWidth}" height="${panelHeight}" rx="14" fill="#14120f" stroke="${status === "PASS" ? "#f97316" : "#ff4d6d"}" stroke-width="2"/>`)
    panels.push(`<text font-family="DejaVu Sans Mono" x="${panelPad + 18}" y="${y + 30}" fill="#ffcf99" font-size="16" font-weight="700">${escapeXml(report.label)} · ${report.cols}x${report.rows} · ${report.preferred} -> ${report.resolved} · ${status}</text>`)
    let lineY = y + 58
    for (const line of report.lines.length > 0 ? report.lines : ["<suppressed>"]) {
      panels.push(`<text font-family="DejaVu Sans Mono" x="${panelPad + 18}" y="${lineY}" fill="#f4efe6" font-size="14">${escapeXml(line)}</text>`)
      lineY += lineHeight
    }
    if (report.failures.length > 0) {
      panels.push(`<text font-family="DejaVu Sans Mono" x="${panelPad + 18}" y="${lineY + 8}" fill="#ff4d6d" font-size="13">${escapeXml(report.failures.join("; "))}</text>`)
    }
    y += panelHeight + panelGap
  }
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
    `<rect width="100%" height="100%" fill="#0b0a08"/>`,
    `<text font-family="DejaVu Sans" x="${panelPad}" y="${panelPad + 10}" fill="#f8b36a" font-size="18" font-weight="800">BreadBoard P16 Landing Variant Contact Sheet</text>`,
    `<text font-family="DejaVu Sans" x="${panelPad}" y="${panelPad + 34}" fill="#9ca3af" font-size="13">Production landing renderer output; invariant pass/fail is computed from terminal visible widths, not SVG pixel fit.</text>`,
    ...panels,
    "</svg>",
  ].join("\n")
}

const main = async () => {
  const { outDir } = parseArgs()
  await fs.mkdir(outDir, { recursive: true })
  const cases: VariantCase[] = [
    { label: "auto frozen scrollback 40x18", viewport: { cols: 40, rows: 18 }, preferred: "auto", frozenScrollback: true },
    { label: "auto frozen scrollback 72x22", viewport: { cols: 72, rows: 22 }, preferred: "auto", frozenScrollback: true },
    { label: "auto frozen scrollback 100x30", viewport: { cols: 100, rows: 30 }, preferred: "auto", frozenScrollback: true },
    { label: "auto frozen scrollback 140x40", viewport: { cols: 140, rows: 40 }, preferred: "auto", frozenScrollback: true },
    { label: "warm hero 140x40", viewport: { cols: 140, rows: 40 }, preferred: "hero", frozenScrollback: false },
    { label: "forced compact 72x22", viewport: { cols: 72, rows: 22 }, preferred: "compact", frozenScrollback: false },
    { label: "micro fallback 36x10", viewport: { cols: 36, rows: 10 }, preferred: "auto", frozenScrollback: false },
    { label: "suppressed tiny height 36x4", viewport: { cols: 36, rows: 4 }, preferred: "auto", frozenScrollback: false },
  ]
  const reports = cases.map(renderCase)
  const failures = reports.flatMap((report) => report.failures.map((failure) => `${report.label}: ${failure}`))
  const report = {
    generatedAt: new Date().toISOString(),
    source: "src/repl/components/replView/landing/scrollbackLanding.tsx",
    cases: reports,
    verdict: failures.length === 0 ? "pass" : "fail",
    failures,
  }
  const markdown = buildMarkdown(reports)
  const svg = buildSvg(reports)
  const reportPath = path.join(outDir, "landing_invariant_report.json")
  const mdPath = path.join(outDir, "landing_contact_sheet.md")
  const svgPath = path.join(outDir, "landing_contact_sheet.svg")
  const pngPath = path.join(outDir, "landing_contact_sheet.png")
  await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8")
  await fs.writeFile(mdPath, markdown, "utf8")
  await fs.writeFile(svgPath, svg, "utf8")
  const pngCreated = await runConvert(svgPath, pngPath)
  const index = {
    outDir,
    reportPath,
    mdPath,
    svgPath,
    pngPath: pngCreated ? pngPath : null,
    verdict: report.verdict,
    failures,
  }
  await fs.writeFile(path.join(outDir, "landing_artifact_index.json"), `${JSON.stringify(index, null, 2)}\n`, "utf8")
  process.stdout.write(`${JSON.stringify(index, null, 2)}\n`)
  if (failures.length > 0) process.exit(1)
}

void main().catch((error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error))
  process.exit(1)
})
