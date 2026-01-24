import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import ansiToSvg from "ansi-to-svg"

const args = process.argv.slice(2)
let inputPath: string | undefined
let outputPath: string | undefined
let lineLimit: number | null = null

for (let i = 0; i < args.length; i += 1) {
  const arg = args[i]
  if (arg === "--lines" || arg === "--tail" || arg === "--max-lines") {
    const raw = args[i + 1]
    if (raw) {
      lineLimit = Number(raw)
      i += 1
    }
    continue
  }
  if (!inputPath) {
    inputPath = arg
    continue
  }
  if (!outputPath) {
    outputPath = arg
    continue
  }
}

if (!inputPath) {
  console.error("Usage: node --import tsx scripts/ansi_to_svg.ts <input.ansi> [output.svg] [--lines N]")
  process.exit(1)
}

const resolveOutput = (input: string, output?: string) => {
  if (output) return output
  const ext = path.extname(input)
  if (ext) return input.slice(0, -ext.length) + ".svg"
  return `${input}.svg`
}

const main = async () => {
  const input = path.resolve(inputPath)
  const output = path.resolve(resolveOutput(inputPath, outputPath))
  let payload = await fs.readFile(input, "utf8")
  if (lineLimit && Number.isFinite(lineLimit) && lineLimit > 0) {
    const lines = payload.split(/\r?\n/)
    if (lines.length > lineLimit) {
      payload = lines.slice(-lineLimit).join("\n")
    }
  }
  const svg = ansiToSvg(payload, {
    fontFamily: "Menlo, Monaco, Consolas, 'Liberation Mono', monospace",
    fontSize: 14,
    lineHeight: 18,
  })
  await fs.writeFile(output, svg, "utf8")
  console.log(`[ansi-to-svg] wrote ${output}`)
}

main().catch((error) => {
  console.error(`[ansi-to-svg] failed: ${(error as Error).message}`)
  process.exit(1)
})
