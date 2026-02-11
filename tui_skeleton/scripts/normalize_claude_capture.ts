import { promises as fs } from "node:fs"
import path from "node:path"

const stripAnsi = (value: string): string => value.replace(/\u001b\[[0-9;]*m/g, "")

const normalize = (value: string): string => {
  let out = stripAnsi(value)
  out = out.replace(/\r\n/g, "\n")
  // Normalize known paths/usernames
  out = out
    .replace(/\/shared_folders\/\S+/g, "<path>")
    .replace(/\/home\/\S+/g, "<path>")
    .replace(/\/Users\/\S+/g, "<path>")
    .replace(/[A-Za-z]:\\\\\S+/g, "<path>")
    .replace(/~\/\S+/g, "<path>")

  // Normalize token counts
  out = out.replace(/(tokens?:\s*)(\d[\d,]*)/gi, "$1<tokens>")
  out = out.replace(/\b(\d[\d,]*)\s+tokens?\b/gi, "<tokens> tokens")

  // Normalize elapsed times
  out = out.replace(/Cooked for [0-9hms\s.]+/gi, "Cooked for <elapsed>")
  out = out.replace(/Cooking [0-9hms\s.]+/gi, "Cooking <elapsed>")

  // Normalize timestamps like [12:34:56]
  out = out.replace(/\[[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{3})?\]/g, "[<time>]")

  // Trim trailing whitespace on each line
  out = out
    .split("\n")
    .map((line) => line.replace(/\s+$/g, ""))
    .join("\n")
  return out
}

const parseArgs = () => {
  const args = process.argv.slice(2)
  let input = ""
  let out = ""
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--input":
        input = args[++i] ?? ""
        break
      case "--out":
        out = args[++i] ?? ""
        break
      default:
        break
    }
  }
  if (!input) {
    throw new Error("Usage: tsx scripts/normalize_claude_capture.ts --input <file> [--out <file>]")
  }
  return { input, out }
}

const main = async () => {
  const { input, out } = parseArgs()
  const inputPath = path.resolve(input)
  const raw = await fs.readFile(inputPath, "utf8")
  const normalized = normalize(raw)
  const outputPath = out ? path.resolve(out) : path.join(path.dirname(inputPath), "normalized.txt")
  await fs.writeFile(outputPath, normalized, "utf8")
  console.log(`[normalize_claude_capture] ${inputPath} -> ${outputPath}`)
}

main().catch((err) => {
  console.error("[normalize_claude_capture] failed:", err)
  process.exit(1)
})
