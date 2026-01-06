import { Args, Command, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { loadUserConfigSync, writeUserConfig } from "../config/userConfig.js"
import { downloadBundle, getEngineCacheRoot, listCachedBundles } from "../engine/engineBundles.js"
import { CLI_PROTOCOL_VERSION, CLI_VERSION } from "../config/version.js"

const manifestOption = Options.text("manifest").pipe(Options.optional)
const versionOption = Options.text("version").pipe(Options.optional)
const pinOption = Options.boolean("pin").pipe(Options.optional)

const formatBundle = (entry: {
  version: string
  platform: string
  arch: string
  entry: string
  minCliVersion?: string
  protocolVersion?: string
}) =>
  `${entry.version} (${entry.platform}/${entry.arch}) -> ${entry.entry}${
    entry.protocolVersion ? ` [proto ${entry.protocolVersion}]` : ""
  }${entry.minCliVersion ? ` [min cli ${entry.minCliVersion}]` : ""}`

const statusCommand = Command.make("status", {}, () =>
  Effect.tryPromise(async () => {
    const config = loadUserConfigSync()
    const cached = await listCachedBundles()
    await Console.log("Engine status")
    await Console.log(`Cache root: ${getEngineCacheRoot()}`)
    await Console.log(`Pinned version: ${config.engineVersion ?? "none"}`)
    await Console.log(`Explicit path: ${config.enginePath ?? "none"}`)
    if (cached.length === 0) {
      await Console.log("Cached bundles: none")
    } else {
      await Console.log(`Cached bundles (${cached.length}):`)
      for (const entry of cached) {
        await Console.log(`- ${formatBundle(entry)}`)
      }
    }
  }),
)

const updateCommand = Command.make(
  "update",
  {
    manifest: manifestOption,
    version: versionOption,
    pin: pinOption,
  },
  ({ manifest, version, pin }) =>
    Effect.tryPromise(async () => {
      const manifestUrl =
        Option.getOrNull(manifest) || process.env.BREADBOARD_ENGINE_MANIFEST_URL?.trim() || ""
      if (!manifestUrl) {
        throw new Error("Provide --manifest or set BREADBOARD_ENGINE_MANIFEST_URL to download bundles.")
      }
      const versionValue = Option.getOrNull(version)
      const info = await downloadBundle(manifestUrl, {
        version: versionValue ?? undefined,
        cliVersion: CLI_VERSION,
        protocolVersion: CLI_PROTOCOL_VERSION,
        retries: 2,
      })
      await Console.log(`Downloaded engine ${info.version} to ${info.root}`)
      const shouldPin = Option.match(pin, { onNone: () => false, onSome: (value) => value })
      if (shouldPin) {
        const current = loadUserConfigSync()
        await writeUserConfig({
          ...current,
          engineVersion: info.version,
          enginePath: undefined,
        })
        await Console.log(`Pinned engine version ${info.version}`)
      }
    }),
)

const pinCommand = Command.make(
  "pin",
  {
    version: Args.text({ name: "version" }),
  },
  ({ version }) =>
    Effect.tryPromise(async () => {
      const current = loadUserConfigSync()
      await writeUserConfig({
        ...current,
        engineVersion: version,
        enginePath: undefined,
      })
      await Console.log(`Pinned engine version ${version}`)
    }),
)

const useCommand = Command.make(
  "use",
  {
    path: Args.text({ name: "path" }),
  },
  ({ path }) =>
    Effect.tryPromise(async () => {
      const current = loadUserConfigSync()
      await writeUserConfig({
        ...current,
        enginePath: path,
        engineVersion: undefined,
      })
      await Console.log(`Using engine binary at ${path}`)
    }),
)

export const engineCommand = Command.make("engine", {}, () => Effect.succeed(undefined)).pipe(
  Command.withSubcommands([statusCommand, updateCommand, pinCommand, useCommand]),
)
