import { loadAppConfig } from "./config/appConfig.js"
import { resolveFrontendSelectionFromArgv } from "./config/frontendMode.js"

export interface CliBootPlan {
  readonly argv: string[]
  readonly defaultedToRepl: boolean
  readonly shouldSkipEngine: boolean
  readonly command: string | undefined
}

export const computeCliArgv = (rawArgv: string[]): { argv: string[]; defaultedToRepl: boolean } => {
  const defaultedToRepl = rawArgv.length <= 2
  return {
    argv: defaultedToRepl ? [...rawArgv.slice(0, 2), "repl"] : rawArgv,
    defaultedToRepl,
  }
}

export const isLocalBaseUrl = (value: string): boolean => {
  try {
    const url = new URL(value)
    const host = url.hostname.toLowerCase()
    return host === "localhost" || host === "127.0.0.1" || host === "::1"
  } catch {
    return false
  }
}

export const shouldSkipEngineForArgv = (args: string[]): boolean => {
  const command = args[2]
  if (!command) return false
  if (command.startsWith("-")) return true
  if (command === "connect" || command === "config" || command === "engine" || command === "auth") return true
  if (args.includes("--help") || args.includes("-h") || args.includes("--version") || args.includes("-v")) {
    return true
  }
  if (command === "repl" || command === "ui") {
    return resolveFrontendSelectionFromArgv(args) === "opentui"
  }
  return false
}

export const shouldUseIsolatedEngine = (args: string[], baseUrl: string, env: NodeJS.ProcessEnv = process.env): boolean => {
  const command = args[2]
  return command === "run" && env.BREADBOARD_ENGINE_ISOLATED !== "0" && isLocalBaseUrl(baseUrl)
}

export const parseEngineModeArgv = (args: string[]): string | undefined => {
  for (let i = 2; i < args.length; i += 1) {
    const arg = args[i]
    if (arg === "--engine-mode") {
      const value = args[i + 1]
      return value && !value.startsWith("-") ? value : undefined
    }
    if (arg.startsWith("--engine-mode=")) {
      const value = arg.slice("--engine-mode=".length).trim()
      return value || undefined
    }
  }
  return undefined
}

export const computeCliBootPlan = (rawArgv: string[]): CliBootPlan => {
  const { argv, defaultedToRepl } = computeCliArgv(rawArgv)
  return {
    argv,
    defaultedToRepl,
    shouldSkipEngine: shouldSkipEngineForArgv(argv),
    command: argv[2],
  }
}

export const loadCliEnginePlan = (args: string[], env: NodeJS.ProcessEnv = process.env) => {
  const config = loadAppConfig()
  return {
    config,
    isolated: shouldUseIsolatedEngine(args, config.baseUrl, env),
    engineMode: parseEngineModeArgv(args),
  }
}
