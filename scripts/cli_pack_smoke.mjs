#!/usr/bin/env node
import { spawnSync } from "node:child_process"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(here, "..")
const tuiDir = path.join(rootDir, "tui_skeleton")
const npmCmd = process.platform === "win32" ? "npm.cmd" : "npm"

const run = (command, args, options = {}) => {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    ...options,
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(" ")} (exit ${result.status})`)
  }
}

const runCapture = (command, args, options = {}) => {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "inherit"],
    ...options,
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(" ")} (exit ${result.status})`)
  }
  return (result.stdout || "").trim()
}

const rimraf = async (target) => {
  try {
    await fs.rm(target, { recursive: true, force: true })
  } catch {
    // ignore
  }
}

const main = async () => {
  console.log("[pack-smoke] building TUI")
  run(npmCmd, ["run", "build"], {
    cwd: tuiDir,
    env: {
      ...process.env,
      BREADBOARD_SKIP_LOCAL_BIN_INSTALL: "1",
      BREADBOARD_INSTALL_QUIET: "1",
    },
    shell: process.platform === "win32",
  })

  console.log("[pack-smoke] packing npm tarball")
  const tarball = runCapture(npmCmd, ["pack", "--silent"], { cwd: tuiDir, shell: process.platform === "win32" })
  const tarballPath = path.join(tuiDir, tarball)

  const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "breadboard-pack-smoke-"))
  try {
    console.log("[pack-smoke] installing packed artifact")
    run(npmCmd, ["install", "--silent", "--prefix", tmpDir, tarballPath], {
      cwd: rootDir,
      shell: process.platform === "win32",
    })

    console.log("[pack-smoke] running CLI from installed artifact")
    const binDir = path.join(tmpDir, "node_modules", ".bin")
    const binPath =
      process.platform === "win32" ? path.join(binDir, "breadboard.cmd") : path.join(binDir, "breadboard")
    run(binPath, ["--help"], {
      cwd: tmpDir,
      env: {
        ...process.env,
        PATH: `${binDir}${path.delimiter}${process.env.PATH ?? ""}`,
      },
      shell: process.platform === "win32",
    })
  } finally {
    await rimraf(tmpDir)
    await rimraf(tarballPath)
  }

  console.log("[pack-smoke] ok")
}

main().catch((error) => {
  console.error(`[pack-smoke] failed: ${error.message}`)
  process.exit(1)
})

