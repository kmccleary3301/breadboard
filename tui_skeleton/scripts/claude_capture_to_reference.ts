import { promises as fs } from "node:fs"
import path from "node:path"
import { renderGridFromFrames } from "../tools/tty/vtgrid.js"

const stripAnsi = (value: string): string => value.replace(/\u001b\[[0-9;]*m/g, "")

const normalize = (value: string): string => {
  let out = stripAnsi(value)
  out = out.replace(/\r\n/g, "\n")
  out = out
    .replace(/\/shared_folders\/\S+/g, "<path>")
    .replace(/\/home\/\S+/g, "<path>")
    .replace(/\/Users\/\S+/g, "<path>")
    .replace(/[A-Za-z]:\\\\\S+/g, "<path>")
    .replace(/~\/\S+/g, "<path>")
  out = out.replace(/(tokens?:\s*)(\d[\d,]*)/gi, "$1<tokens>")
  out = out.replace(/\b(\d[\d,]*)\s+tokens?\b/gi, "<tokens> tokens")
  out = out.replace(/Cooked for [0-9hms\s.]+/gi, "Cooked for <elapsed>")
  out = out.replace(/Cooking [0-9hms\s.]+/gi, "Cooking <elapsed>")
  out = out.replace(/\[[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{3})?\]/g, "[<time>]")
  out = out
    .split("\n")
    .map((line) => line.replace(/\s+$/g, ""))
    .join("\n")
  return out
}

const toReference = (value: string): string => {
  let out = value
  // Normalize bullets
  out = out.replace(/^\s*•\s+/gm, "- ")
  // Normalize hidden-lines markers
  out = out.replace(/\.\.\.\s*\(\d+\s+lines?\s+hidden\)/gi, "... (<n> lines hidden)")
  out = out.replace(/\.\.\.\s*\(\d+\s+entries?\s+hidden\)/gi, "... (<n> entries hidden)")
  // Normalize tabs to spaces
  out = out.replace(/\t/g, "  ")
  return out
}

const parseArgs = () => {
  const args = process.argv.slice(2)
  let input = ""
  let out = ""
  let normalizeInput = false
  let writeGrid = false
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--input":
        input = args[++i] ?? ""
        break
      case "--out":
        out = args[++i] ?? ""
        break
      case "--normalize":
        normalizeInput = true
        break
      case "--grid":
        writeGrid = true
        break
      default:
        break
    }
  }
  if (!input) {
    throw new Error(
      "Usage: tsx scripts/claude_capture_to_reference.ts --input <file> [--out <file>] [--normalize] [--grid]",
    )
  }
  return { input, out, normalizeInput, writeGrid }
}

const main = async () => {
  const { input, out, normalizeInput, writeGrid } = parseArgs()
  const inputPath = path.resolve(input)
  const raw = await fs.readFile(inputPath, "utf8")
  const normalized = normalizeInput ? normalize(raw) : raw
  const reference = toReference(normalized)
  const outputPath = out ? path.resolve(out) : path.join(path.dirname(inputPath), "reference.txt")
  await fs.writeFile(outputPath, reference, "utf8")
  console.log(`[claude_capture_to_reference] ${inputPath} -> ${outputPath}`)

  if (writeGrid) {
    const dir = path.dirname(inputPath)
    let rows = Number(process.env.BREADBOARD_TUI_FRAME_HEIGHT ?? "40")
    let cols = Number(process.env.BREADBOARD_TUI_FRAME_WIDTH ?? "120")
    try {
      const metaRaw = await fs.readFile(path.join(dir, "meta.json"), "utf8")
      const meta = JSON.parse(metaRaw) as Record<string, unknown>
      if (typeof meta.terminal_rows === "number") rows = meta.terminal_rows
      if (typeof meta.terminal_cols === "number") cols = meta.terminal_cols
    } catch {
      // meta optional
    }
    const frames = [{ timestamp: Date.now(), chunk: `${raw}\n` }]
    const grid = renderGridFromFrames(frames, Math.max(1, rows), Math.max(10, cols))
    const gridPath = path.join(dir, "frame.grid.json")
    await fs.writeFile(
      gridPath,
      `${JSON.stringify(
        {
          rows,
          cols,
          grid: grid.grid,
          activeBuffer: grid.activeBuffer,
          normalGrid: grid.normalGrid,
          alternateGrid: grid.alternateGrid,
          timestamp: Date.now(),
        },
        null,
        2,
      )}\n`,
      "utf8",
    )
    console.log(`[claude_capture_to_reference] grid -> ${gridPath}`)
  }
}

main().catch((err) => {
  console.error("[claude_capture_to_reference] failed:", err)
  process.exit(1)
})
