import { promises as fs } from "node:fs"
import path from "node:path"

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
  while (out.length > 0 && out[out.length - 1]?.trim() === "") {
    out.pop()
  }
  out = out.join("\n")
  return out
}

const toReference = (value: string): string => {
  let out = value
  out = out.replace(/^\s*•\s+/gm, "- ")
  out = out.replace(/\.\.\.\s*\(\d+\s+lines?\s+hidden\)/gi, "... (<n> lines hidden)")
  out = out.replace(/\.\.\.\s*\(\d+\s+entries?\s+hidden\)/gi, "... (<n> entries hidden)")
  out = out.replace(/\t/g, "  ")
  return out
}

const diffSample = (expected: string, actual: string, limit = 12) => {
  const expectedLines = expected.split(/\r?\n/)
  const actualLines = actual.split(/\r?\n/)
  const maxLines = Math.max(expectedLines.length, actualLines.length)
  const samples: Array<{ line: number; expected: string; actual: string }> = []
  let mismatches = 0
  for (let i = 0; i < maxLines; i += 1) {
    const exp = expectedLines[i] ?? ""
    const act = actualLines[i] ?? ""
    if (exp !== act) {
      mismatches += 1
      if (samples.length < limit) samples.push({ line: i + 1, expected: exp, actual: act })
    }
  }
  return { mismatches, samples, expectedLines: expectedLines.length, actualLines: actualLines.length }
}

const parseArgs = () => {
  const args = process.argv.slice(2)
  let refsRoot = "misc/tui_goldens/claude_refs"
  let candidateRoot = "misc/tui_goldens/claude_compare"
  let diffRoot = ""
  let strict = false
  let summary = false
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--refs-root":
        refsRoot = args[++i] ?? refsRoot
        break
      case "--candidate-root":
        candidateRoot = args[++i] ?? candidateRoot
        break
      case "--diff-root":
        diffRoot = args[++i] ?? diffRoot
        break
      case "--strict":
        strict = true
        break
      case "--summary":
        summary = true
        break
      default:
        break
    }
  }
  return { refsRoot, candidateRoot, diffRoot, strict, summary }
}

const main = async () => {
  const { refsRoot, candidateRoot, diffRoot, strict, summary } = parseArgs()
  const refsPath = path.resolve(refsRoot)
  const candidatePath = path.resolve(candidateRoot)
  const diffPath = path.resolve(diffRoot || path.join(candidatePath, "_diffs"))
  await fs.mkdir(diffPath, { recursive: true })

  const scenarioDirs = await fs.readdir(refsPath)
  let failed = false
  let okCount = 0
  let failCount = 0
  let missingReference = 0
  let missingTui = 0
  const index: Array<Record<string, unknown>> = []
  const backlog: Array<Record<string, unknown>> = []

  for (const id of scenarioDirs) {
    const refDir = path.join(refsPath, id)
    const stat = await fs.stat(refDir).catch(() => null)
    if (!stat || !stat.isDirectory()) continue

    const refCapturePath = path.join(refDir, "capture.txt")
    const refNormalizedPath = path.join(refDir, "normalized.txt")
    const refReferencePath = path.join(refDir, "reference.txt")
    const tuiPrimaryPath = path.join(candidatePath, id, "tui_render.txt")
    const tuiFallbackPath = path.join(candidatePath, id, "render.txt")

    const refRaw = (await fs.readFile(refReferencePath, "utf8").catch(() => "")) ||
      (await fs.readFile(refNormalizedPath, "utf8").catch(() => "")) ||
      (await fs.readFile(refCapturePath, "utf8").catch(() => ""))

    const tuiRaw =
      (await fs.readFile(tuiPrimaryPath, "utf8").catch(() => "")) ||
      (await fs.readFile(tuiFallbackPath, "utf8").catch(() => ""))

    if (!refRaw) {
      console.log(`[claude-compare] ${id}: missing reference capture`)
      index.push({ id, ok: false, reason: "missing_reference" })
      failed = true
      failCount += 1
      missingReference += 1
      continue
    }
    if (!tuiRaw) {
      console.log(`[claude-compare] ${id}: missing TUI render`)
      index.push({ id, ok: false, reason: "missing_tui" })
      failed = true
      failCount += 1
      missingTui += 1
      continue
    }

    const ref = toReference(normalize(refRaw))
    const tui = toReference(normalize(tuiRaw))

    if (ref !== tui) {
      const diff = diffSample(ref, tui, 12)
      const diffFile = path.join(diffPath, `${id}.diff.txt`)
      const summaryFile = path.join(diffPath, `${id}.summary.json`)
      const lines = [
        `Scenario: ${id}`,
        `Expected lines: ${diff.expectedLines}`,
        `Actual lines: ${diff.actualLines}`,
        `Mismatched lines: ${diff.mismatches}`,
        "",
        "Samples:",
        ...diff.samples.flatMap((sample) => [
          `- Line ${sample.line}:`,
          `  expected: ${sample.expected}`,
          `  actual:   ${sample.actual}`,
          "",
        ]),
      ]
      await fs.writeFile(diffFile, lines.join("\n"), "utf8")
      await fs.writeFile(summaryFile, `${JSON.stringify(diff, null, 2)}\n`, "utf8")
      index.push({ id, ok: false, diff: diffFile, summary: summaryFile })
      backlog.push({
        id,
        mismatches: diff.mismatches,
        expectedLines: diff.expectedLines,
        actualLines: diff.actualLines,
        samples: diff.samples,
      })
      failed = true
      failCount += 1
      console.log(`[claude-compare] ${id}: mismatch`)
      continue
    }

    index.push({ id, ok: true })
    okCount += 1
    console.log(`[claude-compare] ${id}: ok`)
  }

  await fs.writeFile(
    path.join(diffPath, "index.json"),
    `${JSON.stringify({ okCount, failCount, missingReference, missingTui, index }, null, 2)}\n`,
    "utf8",
  )
  await fs.writeFile(path.join(diffPath, "backlog.json"), `${JSON.stringify(backlog, null, 2)}\n`, "utf8")
  if (summary) {
    const total = okCount + failCount
    console.log(
      `[claude-compare] scenarios=${total} ok=${okCount} fail=${failCount} missing_reference=${missingReference} missing_tui=${missingTui}`,
    )
  }
  if (failed && strict) process.exit(1)
}

main().catch((err) => {
  console.error("[claude-compare] failed:", err)
  process.exit(1)
})
