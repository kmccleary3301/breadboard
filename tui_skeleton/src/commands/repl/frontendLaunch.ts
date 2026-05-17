import { spawn } from "node:child_process"
import { resolveBreadboardRepoPath } from "../../utils/paths.js"

export interface OpenTuiLaunchOptions {
  readonly configPath: string
  readonly workspace?: string | null
  readonly permissionMode?: string | null
}

export interface OpenTuiLaunchSpec {
  readonly cwd: string
  readonly command: string
  readonly args: string[]
  readonly env: NodeJS.ProcessEnv
}

export const buildOpenTuiLaunchSpec = (options: OpenTuiLaunchOptions): OpenTuiLaunchSpec => {
  const args = ["run", "phaseB/controller.ts"]
  if (options.configPath) {
    args.push("--config", options.configPath)
  }
  if (options.workspace) {
    args.push("--workspace", options.workspace)
  }
  if (options.permissionMode) {
    args.push("--permission-mode", options.permissionMode)
  }

  return {
    cwd: resolveBreadboardRepoPath("opentui_slab"),
    command: "bun",
    args,
    env: {
      ...process.env,
      BREADBOARD_ENGINE_PREFER_BUNDLE: "0",
    },
  }
}

export const runOpenTui = async (options: OpenTuiLaunchOptions): Promise<void> => {
  const spec = buildOpenTuiLaunchSpec(options)
  const child = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    stdio: "inherit",
    env: spec.env,
  })

  await new Promise<void>((resolve, reject) => {
    child.once("error", (err) => reject(err))
    child.once("exit", (code) => {
      if (code && code !== 0) {
        reject(new Error(`OpenTUI exited with code ${code}`))
        return
      }
      resolve()
    })
  })
}
