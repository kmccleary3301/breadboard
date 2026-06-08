import { describe, expect, it } from "vitest"
import { buildSuggestions, SLASH_COMMANDS, SLASH_COMMAND_REGISTRY } from "../slashCommands.js"
import {
  findNearestSlashCommands,
  findSlashCommandEntry,
  getVisibleSlashCommandEntries,
  resolveSlashCommandAvailability,
  validateSlashCommandArguments,
} from "../slashCommandRegistry.js"

describe("slash command registry", () => {
  it("derives the compatibility command list from visible registry entries", () => {
    expect(SLASH_COMMANDS.map((command) => command.name)).toEqual(
      getVisibleSlashCommandEntries().map((command) => command.name),
    )
    expect(SLASH_COMMANDS.every((command) => command.category && command.availability)).toBe(true)
  })

  it("keeps every registry entry addressable by id/name and sorted by explicit order", () => {
    const ids = new Set<string>()
    for (const command of SLASH_COMMAND_REGISTRY) {
      expect(command.id).toBe(command.name)
      expect(ids.has(command.id)).toBe(false)
      ids.add(command.id)
    }
    const visible = getVisibleSlashCommandEntries()
    expect([...visible].sort((a, b) => a.order - b.order)).toEqual(visible)
  })

  it("prefers exact commands before aliases", () => {
    expect(findSlashCommandEntry("sessions")?.name).toBe("sessions")
    expect(findSlashCommandEntry("model")?.name).toBe("model")
    expect(findSlashCommandEntry("mention")?.name).toBe("mention")
    expect(findSlashCommandEntry("session")).toBeNull()
  })

  it("keeps debug commands dispatchable but out of the visible popup command list", () => {
    expect(getVisibleSlashCommandEntries().map((command) => command.name)).not.toContain("debug-config")
    expect(findSlashCommandEntry("debug-config")).toMatchObject({
      name: "debug-config",
      visibility: "debug",
      dispatchKind: "debug-local",
    })
  })

  it("keeps deferred parity commands explicitly addressable but out of visible suggestions", () => {
    const visibleNames = getVisibleSlashCommandEntries().map((command) => command.name)
    for (const name of ["goal", "fork"]) {
      expect(visibleNames).not.toContain(name)
      expect(findSlashCommandEntry(name)).toMatchObject({
        name,
        visibility: "experimental",
        availability: "feature-gated",
        dispatchKind: "deferred",
      })
    }
    expect(visibleNames).not.toContain("diff")
    expect(findSlashCommandEntry("diff")).toMatchObject({
      name: "diff",
      visibility: "experimental",
      availability: "available",
      dispatchKind: "ui-local",
    })
    expect(visibleNames).not.toContain("permissions")
    expect(findSlashCommandEntry("permissions")).toMatchObject({
      name: "permissions",
      visibility: "experimental",
      availability: "available",
      dispatchKind: "ui-local",
    })
    expect(visibleNames).not.toContain("agents")
    expect(findSlashCommandEntry("agents")).toMatchObject({
      name: "agents",
      visibility: "experimental",
      availability: "available",
      dispatchKind: "ui-local",
    })
  })

  it("fails closed for feature-gated parity commands", () => {
    const featureGated = SLASH_COMMAND_REGISTRY.filter((command) => command.availability === "feature-gated")
    expect(featureGated.map((command) => command.name).sort()).toEqual(["fork", "goal"])
    for (const command of featureGated) {
      expect(command.visibility).toBe("experimental")
      expect(command.dispatchKind).toBe("deferred")
      expect(command.disabledReason).toBeTruthy()
    }
  })

  it("resolves runtime and feature-gated command availability", () => {
    const stop = findSlashCommandEntry("stop")
    const raw = findSlashCommandEntry("raw")
    const copy = findSlashCommandEntry("copy")
    const diff = findSlashCommandEntry("diff")
    const permissions = findSlashCommandEntry("permissions")
    const goal = findSlashCommandEntry("goal")
    const fork = findSlashCommandEntry("fork")
    expect(stop && resolveSlashCommandAvailability(stop, { pendingResponse: false })).toMatchObject({
      availability: "context-gated",
      disabledReason: "No running response to stop.",
    })
    expect(stop && resolveSlashCommandAvailability(stop, { pendingResponse: true })).toMatchObject({
      availability: "available",
    })
    expect(raw && resolveSlashCommandAvailability(raw)).toMatchObject({
      availability: "available",
    })
    expect(copy && resolveSlashCommandAvailability(copy)).toMatchObject({
      availability: "available",
    })
    expect(diff && resolveSlashCommandAvailability(diff)).toMatchObject({
      availability: "available",
    })
    expect(permissions && resolveSlashCommandAvailability(permissions)).toMatchObject({
      availability: "available",
    })
    expect(goal && resolveSlashCommandAvailability(goal)).toMatchObject({
      availability: "feature-gated",
      disabledReason: expect.stringContaining("durable goal"),
    })
    expect(fork && resolveSlashCommandAvailability(fork)).toMatchObject({
      availability: "feature-gated",
      disabledReason: expect.stringContaining("session graph"),
    })
  })

  it("preserves suggestion defaults and fuzzy lookup through the registry-backed compatibility API", () => {
    expect(buildSuggestions("/", 5).map((row) => row.command)).toEqual([
      "/resume",
      "/transcript",
      "/attach",
      "/models",
      "/shortcuts",
    ])
    expect(buildSuggestions("/tr", 5).map((row) => row.command)).toContain("/transcript")
  })

  it("returns nearest matches for invalid command handling", () => {
    expect(findNearestSlashCommands("modles", 2).map((entry) => entry.name)).toContain("models")
  })

  it("validates command arguments from registry schemas", () => {
    const mode = findSlashCommandEntry("mode")
    const model = findSlashCommandEntry("model")
    const help = findSlashCommandEntry("help")
    const toolDisplay = findSlashCommandEntry("tool-display")
    expect(mode && validateSlashCommandArguments(mode, ["plan"])).toMatchObject({ ok: true })
    expect(mode && validateSlashCommandArguments(mode, ["chaos"])).toMatchObject({
      ok: false,
      errorLines: expect.arrayContaining(["Expected: plan|build|auto"]),
    })
    expect(model && validateSlashCommandArguments(model, ["gpt-5.4-mini"])).toMatchObject({ ok: true })
    expect(model && validateSlashCommandArguments(model, [])).toMatchObject({
      ok: false,
      errorLines: expect.arrayContaining(["Missing required argument <id>."]),
    })
    expect(help && validateSlashCommandArguments(help, ["extra"])).toMatchObject({
      ok: false,
      errorLines: expect.arrayContaining(["/help does not take arguments."]),
    })
    expect(toolDisplay && validateSlashCommandArguments(toolDisplay, ["list"])).toMatchObject({ ok: true })
  })
})
