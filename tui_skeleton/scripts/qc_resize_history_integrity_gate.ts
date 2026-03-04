import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

const countMatches = (text: string, pattern: RegExp): number => {
  const matches = text.match(pattern)
  return matches ? matches.length : 0
}

const main = async () => {
  const arg = process.argv[2]
  const target = arg?.trim() ? arg.trim() : "scripts/_tmp_qc_resize_history_integrity.txt"
  const resolved = path.isAbsolute(target) ? target : path.join(process.cwd(), target)
  const raw = await fs.readFile(resolved, "utf8")
  const body = raw
    .split(/\r?\n/)
    .filter((line) => !line.startsWith("# "))
    .join("\n")

  const failures: string[] = []

  const breadboardCount = countMatches(body, /BreadBoard v(?:0\.2\.0|0\.0\.0a)/g)
  if (breadboardCount < 1) {
    failures.push(`expected at least 1 landing header in history, found ${breadboardCount}`)
  }

  const tipsCount = countMatches(body, /Tips for getting started/g)
  if (tipsCount < 1) {
    failures.push(`expected at least 1 landing tips block in history, found ${tipsCount}`)
  }

  const activityCount = countMatches(body, /Recent activity/g)
  if (activityCount < 1) {
    failures.push(`expected at least 1 landing activity block in history, found ${activityCount}`)
  }

  const promptCount = countMatches(body, /Try "refactor <filepath>"/g)
  if (promptCount > 30) {
    failures.push(`expected <=30 prompt echoes in history, found ${promptCount}`)
  }

  const footerCount = countMatches(body, /\/ commands · @ files/g)
  if (footerCount > 40) {
    failures.push(`expected <=40 footer echoes in history, found ${footerCount}`)
  }

  if (failures.length > 0) {
    throw new Error(`Resize history integrity gate failed:\n${failures.join("\n")}`)
  }

  console.log("[qc] resize history integrity gate passed")
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error)
  console.error(message)
  process.exitCode = 1
})
