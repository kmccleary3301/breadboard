#!/usr/bin/env node
import fs from "node:fs"
import path from "node:path"

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..")
const pkgPath = path.join(root, "package.json")
const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"))

const requiredCommands = [
  "breadboard.sidebar.focus",
  "breadboard.sidebar.newSession",
  "breadboard.sidebar.attachSession",
  "breadboard.sidebar.sendMessage",
  "breadboard.sidebar.stopActiveSession",
  "breadboard.sidebar.deleteSession",
  "breadboard.sidebar.checkConnection",
]

const contributes = pkg.contributes ?? {}
const commands = Array.isArray(contributes.commands) ? contributes.commands : []
const commandSet = new Set(commands.map((c) => c.command).filter(Boolean))
const missingCommands = requiredCommands.filter((command) => !commandSet.has(command))

const activityViews = contributes.views ?? {}
const hasBreadboardView =
  Array.isArray(activityViews.breadboard) &&
  activityViews.breadboard.some((view) => view && view.id === "breadboard.sidebar")

const errors = []
if (!pkg.engines || typeof pkg.engines.vscode !== "string") {
  errors.push("Missing engines.vscode in package.json")
}
if (!hasBreadboardView) {
  errors.push("Missing breadboard.sidebar view contribution")
}
if (missingCommands.length > 0) {
  errors.push(`Missing required commands: ${missingCommands.join(", ")}`)
}

if (errors.length > 0) {
  console.error("cursor smoke failed")
  for (const error of errors) console.error(" -", error)
  process.exit(1)
}

console.log("cursor smoke passed")
console.log(` - engines.vscode = ${pkg.engines.vscode}`)
console.log(` - commands checked = ${requiredCommands.length}`)
console.log(" - view id = breadboard.sidebar")
