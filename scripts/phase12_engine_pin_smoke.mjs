#!/usr/bin/env node
import { spawnSync } from "node:child_process"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

const rootDir = process.cwd()
const tuiDir = path.join(rootDir, "tui_skeleton")
const npmCmd = process.platform === "win32" ? "npm.cmd" : "npm"

const run = (command, args, options = {}) => {
  const result = spawnSync(command, args, { stdio: "inherit", ...options })
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(" ")} (exit ${result.status})`)
  }
}

const readJson = async (filePath) => {
  const raw = await fs.readFile(filePath, "utf8")
  return JSON.parse(raw)
}

const main = async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "bb-pin-smoke-"))
  const userConfigPath = path.join(tmp, "config.json")
  try {
    run(npmCmd, ["run", "build"], {
      cwd: tuiDir,
      env: { ...process.env, BREADBOARD_SKIP_LOCAL_BIN_INSTALL: "1", BREADBOARD_INSTALL_QUIET: "1" },
      shell: process.platform === "win32",
    })

    const cli = path.join(tuiDir, "dist", "main.js")
    const env = { ...process.env, BREADBOARD_USER_CONFIG: userConfigPath }

    const version = "0.0.0-smoke-pin"
    run("node", [cli, "engine", "pin", version], { cwd: tmp, env })
    const pinned = await readJson(userConfigPath)
    if (pinned.engineVersion !== version || pinned.enginePath) {
      throw new Error(`Unexpected pinned config: ${JSON.stringify(pinned)}`)
    }

    const fakePath = "/tmp/breadboard-engine"
    run("node", [cli, "engine", "use", fakePath], { cwd: tmp, env })
    const used = await readJson(userConfigPath)
    if (used.enginePath !== fakePath || used.engineVersion) {
      throw new Error(`Unexpected engine use config: ${JSON.stringify(used)}`)
    }

    run("node", [cli, "engine", "status"], { cwd: tmp, env })
    console.log("[engine-pin-smoke] ok")
  } finally {
    await fs.rm(tmp, { recursive: true, force: true })
  }
}

main().catch((error) => {
  console.error(`[engine-pin-smoke] failed: ${error.message}`)
  process.exit(1)
})

