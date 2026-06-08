#!/usr/bin/env node
const fs = require('node:fs')
const path = require('node:path')
const { analyzeArtifactRuns } = require('../tools/vscode-terminal-harness/src/v5ArtifactDetectors.cjs')

const args = process.argv.slice(2)
const outIndex = args.indexOf('--out')
let outFile = null
if (outIndex >= 0) {
  outFile = args[outIndex + 1]
  args.splice(outIndex, 2)
}

if (args.length === 0) {
  console.error('Usage: node tui_skeleton/scripts/v5_detect_artifact_defects.cjs [--out report.json] <run-dir>...')
  process.exit(2)
}

const results = analyzeArtifactRuns(args.map((item) => path.resolve(item)))
const summary = {
  ok: results.every((result) => result.ok),
  runCount: results.length,
  findingCount: results.reduce((sum, result) => sum + result.findings.length, 0),
  results,
}

const text = `${JSON.stringify(summary, null, 2)}\n`
if (outFile) {
  fs.mkdirSync(path.dirname(path.resolve(outFile)), { recursive: true })
  fs.writeFileSync(outFile, text)
} else {
  process.stdout.write(text)
}

process.exit(summary.ok ? 0 : 1)

