import { promises as fs } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

type MaskOptions = {
  timestamps: boolean
  tokens: boolean
  paths: boolean
  status: boolean
  spinner: boolean
}

type FrameGrid = {
  rows: number
  cols: number
  grid: string[]
  activeBuffer: "normal" | "alternate"
  normalGrid: string[]
  alternateGrid: string[]
  timestamp: number
}

const applyNormalize = (value: string, options: MaskOptions): string => {
  let out = value
  if (options.paths) {
    out = out
      .replace(/\/shared_folders\/\S+/g, "<path>")
      .replace(/\/home\/\S+/g, "<path>")
      .replace(/\/Users\/\S+/g, "<path>")
      .replace(/[A-Za-z]:\\\\\S+/g, "<path>")
      .replace(/~\/\S+/g, "<path>")
  }
  if (options.tokens) {
    out = out.replace(/(tokens?:\s*)(\d[\d,]*)/gi, "$1<tokens>")
    out = out.replace(/\b(\d[\d,]*)\s+tokens?\b/gi, "<tokens> tokens")
  }
  if (options.timestamps) {
    out = out.replace(/Cooked for [0-9hms\s.]+/gi, "Cooked for <elapsed>")
    out = out.replace(/Cooking [0-9hms\s.]+/gi, "Cooking <elapsed>")
  }
  if (options.status) {
    if (/(Deciphering|Spelunking|Reconnecting|Disconnected)/i.test(out)) {
      out = "<status>"
    }
  }
  if (options.spinner) {
    out = out.replace(/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/g, " ")
  }
  return out
}

export const maskGridLines = (lines: ReadonlyArray<string>, options: MaskOptions): string[] =>
  lines.map((line) => applyNormalize(line, options))

const parseArgs = () => {
  const args = process.argv.slice(2)
  let inputPath = ""
  let outputPath = ""
  const options: MaskOptions = {
    timestamps: true,
    tokens: true,
    paths: true,
    status: true,
    spinner: true,
  }
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--in":
        inputPath = args[++i] ?? ""
        break
      case "--out":
        outputPath = args[++i] ?? ""
        break
      case "--no-timestamps":
        options.timestamps = false
        break
      case "--no-tokens":
        options.tokens = false
        break
      case "--no-paths":
        options.paths = false
        break
      case "--no-status":
        options.status = false
        break
      case "--no-spinner":
        options.spinner = false
        break
      default:
        break
    }
  }
  if (!inputPath) {
    throw new Error("Usage: tsx scripts/mask_grid.ts --in <frame.grid.json> [--out <file>]")
  }
  if (!outputPath) {
    const base = inputPath.endsWith(".json") ? inputPath.replace(/\.json$/i, ".masked.json") : `${inputPath}.masked.json`
    outputPath = base
  }
  return { inputPath, outputPath, options }
}

const main = async () => {
  const { inputPath, outputPath, options } = parseArgs()
  const raw = await fs.readFile(inputPath, "utf8")
  const parsed = JSON.parse(raw) as FrameGrid
  const masked: FrameGrid = {
    ...parsed,
    grid: maskGridLines(parsed.grid ?? [], options),
    normalGrid: maskGridLines(parsed.normalGrid ?? [], options),
    alternateGrid: maskGridLines(parsed.alternateGrid ?? [], options),
  }
  await fs.writeFile(outputPath, `${JSON.stringify(masked, null, 2)}\n`, "utf8")
  console.log(`[mask_grid] wrote ${outputPath}`)
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error("[mask_grid] failed:", error)
    process.exit(1)
  })
}
