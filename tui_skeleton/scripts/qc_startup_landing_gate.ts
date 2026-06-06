import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

const countMatches = (text: string, pattern: RegExp): number => {
  const matches = text.match(pattern)
  return matches ? matches.length : 0
}

const main = async () => {
  const arg = process.argv[2]
  const target = arg?.trim() ? arg.trim() : "scripts/_tmp_qc_startup_landing.txt"
  const resolved = path.isAbsolute(target) ? target : path.join(process.cwd(), target)
  const text = await fs.readFile(resolved, "utf8")

  const failures: string[] = []
  const landingMarkers = [
    [/BreadBoard v(?:0\.2\.0|0\.0\.0a)/g, "landing header"],
    [/(?:Type your request…|Try \"refactor <filepath>\")/g, "composer placeholder"],
  ] as const
  for (const [pattern, label] of landingMarkers) {
    const count = countMatches(text, pattern)
    if (count !== 1) {
      failures.push(`expected exactly 1 ${label}, found ${count}`)
    }
  }
  if (countMatches(text, /Skills selection updated\./g) > 0) {
    failures.push("startup frame should not include skills-selection hint noise")
  }
  if (!text.includes("[ready]")) {
    failures.push("startup frame did not reach ready footer")
  }

  const body = text
    .split(/\r?\n/)
    .filter((line) => !line.startsWith("# "))
  const landingEnd = Math.max(
    body.findIndex((line) => line.includes("╰")),
    body.findIndex((line) => line.includes("Using Config")),
    body.findIndex((line) => line.includes("BreadBoard v")),
  )
  const composerStart = body.findIndex(
    (line, index) => index > landingEnd && (line.includes("Type your request") || line.includes('Try "refactor <filepath>"')),
  )
  if (landingEnd >= 0 && composerStart > landingEnd) {
    const gapLines = body.slice(landingEnd + 1, composerStart).filter((line) => line.trim().length === 0).length
    if (gapLines > 3) {
      failures.push(`startup frame has ${gapLines} blank lines between landing and composer; expected <=3 for scrollback shape`)
    }
  }

  if (failures.length > 0) {
    throw new Error(`Startup landing gate failed:\n${failures.join("\n")}`)
  }
  console.log("[qc] startup landing gate passed")
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error)
  console.error(message)
  process.exitCode = 1
})
