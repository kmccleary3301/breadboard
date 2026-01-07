import { Command, Args, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { promises as fs } from "node:fs"
import { homedir } from "node:os"
import path from "node:path"
import { spawnSync } from "node:child_process"

const PLUGIN_MANIFEST = "breadboard.plugin.json"

type PluginScope = "workspace" | "user"

type PluginManifest = {
  readonly id: string
  readonly version: string
  readonly name: string
  readonly description: string
  readonly skills?: unknown
  readonly mcp?: unknown
  readonly permissions?: unknown
  readonly runtime?: unknown
}

type PluginListEntry = {
  readonly id: string
  readonly version: string
  readonly name: string
  readonly description: string
  readonly root: string
  readonly source: string
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const resolveScopeRoot = (scope: PluginScope): string => {
  if (scope === "user") {
    return path.join(homedir(), ".breadboard", "plugins")
  }
  return path.join(process.cwd(), ".breadboard", "plugins")
}

const splitPathList = (value: string): string[] =>
  value
    .split(path.delimiter)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)

const resolveDefaultRoots = (): Array<{ root: string; source: string }> => {
  const roots: Array<{ root: string; source: string }> = [
    { root: resolveScopeRoot("workspace"), source: "workspace" },
    { root: resolveScopeRoot("user"), source: "user" },
  ]
  const env = process.env.BREADBOARD_PLUGIN_DIRS?.trim()
  if (env) {
    for (const entry of splitPathList(env)) {
      roots.push({ root: path.resolve(entry), source: "env" })
    }
  }
  return roots
}

const validatePluginManifestPayload = (payload: unknown): string[] => {
  if (!isRecord(payload)) return ["manifest must be a JSON object"]
  const errors: string[] = []
  const requiredString = (key: string) => {
    const value = payload[key]
    if (typeof value !== "string" || value.trim().length === 0) {
      errors.push(`Missing required field: ${key}`)
    }
  }
  requiredString("id")
  requiredString("version")
  requiredString("name")
  requiredString("description")

  const skills = payload.skills
  if (skills != null) {
    if (Array.isArray(skills)) {
      if (!skills.every((item) => typeof item === "string" && item.trim().length > 0)) {
        errors.push("skills must be a list of non-empty strings")
      }
    } else if (isRecord(skills)) {
      const paths = skills.paths
      if (paths != null && (!Array.isArray(paths) || !paths.every((item) => typeof item === "string" && item.trim().length > 0))) {
        errors.push("skills.paths must be a list of non-empty strings")
      }
    } else {
      errors.push("skills must be an array or an object")
    }
  }

  const permissions = payload.permissions
  if (permissions != null && !isRecord(permissions)) {
    errors.push("permissions must be an object")
  }

  const runtime = payload.runtime
  if (runtime != null && !isRecord(runtime)) {
    errors.push("runtime must be an object")
  }

  const mcp = payload.mcp
  if (mcp != null) {
    if (!isRecord(mcp)) {
      errors.push("mcp must be an object")
    } else {
      const servers = mcp.servers
      if (servers != null && (!Array.isArray(servers) || !servers.every((item) => isRecord(item)))) {
        errors.push("mcp.servers must be a list of objects")
      }
    }
  }

  return errors
}

const readManifest = async (manifestPath: string): Promise<PluginManifest> => {
  const raw = await fs.readFile(manifestPath, "utf8")
  const parsed = JSON.parse(raw) as unknown
  const errors = validatePluginManifestPayload(parsed)
  if (errors.length > 0) {
    throw new Error(`Invalid plugin manifest: ${errors.join("; ")}`)
  }
  return parsed as PluginManifest
}

async function* walk(dir: string, depth: number): AsyncGenerator<string> {
  if (depth < 0) return
  let handle
  try {
    handle = await fs.opendir(dir)
  } catch {
    return
  }
  for await (const entry of handle) {
    const next = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name.startsWith(".")) continue
      yield* walk(next, depth - 1)
    } else if (entry.isFile()) {
      yield next
    }
  }
}

const discoverPlugins = async (): Promise<PluginListEntry[]> => {
  const results: PluginListEntry[] = []
  for (const { root, source } of resolveDefaultRoots()) {
    for await (const file of walk(root, 6)) {
      if (path.basename(file) !== PLUGIN_MANIFEST) continue
      try {
        const manifest = await readManifest(file)
        results.push({
          id: manifest.id,
          version: manifest.version,
          name: manifest.name,
          description: manifest.description,
          root: path.dirname(file),
          source,
        })
      } catch {
        // Skip invalid manifests for list; validate command provides details.
      }
    }
  }
  results.sort((a, b) => (a.id === b.id ? a.version.localeCompare(b.version) : a.id.localeCompare(b.id)))
  return results
}

const outputArg = Options.text("output").pipe(Options.withDefault("table"))

const pluginListCommand = Command.make("list", { output: outputArg }, ({ output }) =>
  Effect.tryPromise(async () => {
    const entries = await discoverPlugins()
    if (output === "json") {
      await Console.log(JSON.stringify(entries, null, 2))
      return
    }
    if (entries.length === 0) {
      await Console.log("(no plugins found)")
      return
    }
    const headers = ["Id", "Version", "Name", "Source", "Root"]
    const rows = entries.map((p) => [p.id, p.version, p.name, p.source, p.root])
    const widths = headers.map((header, idx) => Math.max(header.length, ...rows.map((row) => row[idx].length)))
    const formatRow = (row: string[]) => row.map((cell, idx) => cell.padEnd(widths[idx], " ")).join("  ")
    await Console.log([formatRow(headers), formatRow(widths.map((w) => "".padEnd(w, "-"))), ...rows.map(formatRow)].join("\n"))
  }),
)

const initDirArg = Args.text({ name: "dir" })
const idOption = Options.text("id")
const nameOption = Options.text("name")
const versionOption = Options.text("version").pipe(Options.withDefault("0.1.0"))
const descOption = Options.text("description").pipe(Options.withDefault("BreadBoard plugin."))

const pluginInitCommand = Command.make(
  "init",
  { dir: initDirArg, id: idOption, name: nameOption, version: versionOption, description: descOption },
  ({ dir, id, name, version, description }) =>
    Effect.tryPromise(async () => {
      const target = path.resolve(dir)
      await fs.mkdir(target, { recursive: true })
      const manifestPath = path.join(target, PLUGIN_MANIFEST)
      const payload: PluginManifest = {
        id,
        version,
        name,
        description,
        skills: { paths: ["skills"] },
        mcp: { servers: [] },
        permissions: {},
        runtime: {},
      }
      await fs.mkdir(path.join(target, "skills"), { recursive: true })
      await fs.writeFile(manifestPath, JSON.stringify(payload, null, 2), "utf8")
      await Console.log(`Created ${manifestPath}`)
    }),
)

const validatePathArg = Args.text({ name: "path" })

const pluginValidateCommand = Command.make("validate", { path: validatePathArg }, ({ path: validatePath }) =>
  Effect.tryPromise(async () => {
    const resolved = path.resolve(validatePath)
    const stat = await fs.stat(resolved)
    const manifest = stat.isDirectory() ? path.join(resolved, PLUGIN_MANIFEST) : resolved
    const payload = await readManifest(manifest)
    await Console.log(JSON.stringify(payload, null, 2))
  }),
)

const scopeOption = Options.choice("scope", ["workspace", "user"] as const).pipe(Options.withDefault("workspace"))
const forceOption = Options.boolean("force").pipe(Options.withDefault(false))
const installPathArg = Args.text({ name: "path" })

const pluginInstallCommand = Command.make(
  "install",
  { path: installPathArg, scope: scopeOption, force: forceOption },
  ({ path: installPath, scope, force }) =>
    Effect.tryPromise(async () => {
      const resolved = path.resolve(installPath)
      const stat = await fs.stat(resolved)
      if (!stat.isDirectory()) {
        throw new Error("Only directory installs are supported for now (provide a plugin folder).")
      }
      const manifestPath = path.join(resolved, PLUGIN_MANIFEST)
      const manifest = await readManifest(manifestPath)
      const destRoot = resolveScopeRoot(scope)
      const destDir = path.join(destRoot, manifest.id)
      await fs.mkdir(destRoot, { recursive: true })
      try {
        const existing = await fs.stat(destDir)
        if (existing.isDirectory()) {
          if (!force) {
            throw new Error(`Plugin ${manifest.id} already exists at ${destDir} (use --force to overwrite).`)
          }
          await fs.rm(destDir, { recursive: true, force: true })
        }
      } catch {
        // ok
      }
      await fs.cp(resolved, destDir, { recursive: true })
      await Console.log(`Installed ${manifest.id}@${manifest.version} to ${destDir}`)
    }),
)

const removeIdArg = Args.text({ name: "id" })
const pluginRemoveCommand = Command.make("remove", { id: removeIdArg, scope: scopeOption }, ({ id, scope }) =>
  Effect.tryPromise(async () => {
    const destDir = path.join(resolveScopeRoot(scope), id)
    await fs.rm(destDir, { recursive: true, force: true })
    await Console.log(`Removed ${id} from ${scope}`)
  }),
)

const packPathArg = Args.text({ name: "path" })
const packOutOption = Options.text("out").pipe(Options.optional)

const pluginPackCommand = Command.make(
  "pack",
  { path: packPathArg, out: packOutOption },
  ({ path: packPath, out }) =>
    Effect.tryPromise(async () => {
      const resolved = path.resolve(packPath)
      const stat = await fs.stat(resolved)
      if (!stat.isDirectory()) {
        throw new Error("pack expects a plugin directory")
      }
      const manifest = await readManifest(path.join(resolved, PLUGIN_MANIFEST))
      const outValue = Option.getOrNull(out)
      const defaultName = `${manifest.id}-${manifest.version}.tgz`
      const outPath = outValue && outValue.trim().length > 0 ? path.resolve(outValue) : path.join(process.cwd(), defaultName)
      await fs.mkdir(path.dirname(outPath), { recursive: true })
      const tar = spawnSync("tar", ["-czf", outPath, "-C", resolved, "."], { encoding: "utf8" })
      if (tar.error) {
        throw tar.error
      }
      if (tar.status !== 0) {
        throw new Error(tar.stderr || "tar failed")
      }
      await Console.log(`Packed ${manifest.id}@${manifest.version} -> ${outPath}`)
    }),
)

export const pluginCommand = Command.make("plugin", {}, () => Effect.succeed(undefined)).pipe(
  Command.withSubcommands([pluginListCommand, pluginInitCommand, pluginValidateCommand, pluginInstallCommand, pluginRemoveCommand, pluginPackCommand]),
)
