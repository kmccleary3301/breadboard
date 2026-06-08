import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

const countMatches = (text: string, pattern: RegExp): number => {
  const matches = text.match(pattern)
  return matches ? matches.length : 0
}

const main = async () => {
  const arg = process.argv[2]
  const target = arg?.trim() ? arg.trim() : "scripts/_tmp_qc_resize_history_stress.txt"
  const resolved = path.isAbsolute(target) ? target : path.join(process.cwd(), target)
  const raw = await fs.readFile(resolved, "utf8")
  const body = raw
    .split(/\r?\n/)
    .filter((line) => !line.startsWith("# "))
    .join("\n")

  const failures: string[] = []

  const requiredPresence: Array<{ label: string; pattern: RegExp }> = [
    { label: "landing header", pattern: /BreadBoard v(?:0\.2\.0|0\.0\.0a)/g },
    { label: "landing config row", pattern: /Using Config `[^`]+`/g },
    { label: "landing model row", pattern: /gpt-[^\s]+ · Codex/g },
    { label: "landing workspace row", pattern: /\/shared_folders\/querylake_server\/ray_testing\/ray_SCE/g },
  ]

  for (const check of requiredPresence) {
    const count = countMatches(body, check.pattern)
    if (count < 1) {
      failures.push(`expected at least 1 ${check.label} block in history, found ${count}`)
    }
    if (count > 1) {
      failures.push(`expected <=1 ${check.label} block in history under resize churn, found ${count}`)
    }
  }

  const footerCount = countMatches(body, /\/ commands · @ files/g)
  if (footerCount > 60) {
    failures.push(`expected <=60 footer echoes in history under resize churn, found ${footerCount}`)
  }

  const footerShortcutCount = countMatches(body, /\? shortcuts/g)
  if (footerShortcutCount > 45) {
    failures.push(`expected <=45 footer shortcut echoes in history under resize churn, found ${footerShortcutCount}`)
  }

  if (failures.length > 0) {
    throw new Error(`Resize history stress gate failed:\n${failures.join("\n")}`)
  }

  console.log("[qc] resize history stress gate passed")
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error)
  console.error(message)
  process.exitCode = 1
})
