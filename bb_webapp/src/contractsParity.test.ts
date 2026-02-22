import { describe, expect, it } from "vitest"
import {
  buildSessionDownloadPath,
  FORBIDDEN_COMMAND_ALIASES,
  FORBIDDEN_ROUTE_MARKERS,
  NORMALIZED_COMMAND_CATALOG,
  NORMALIZED_ROUTE_CATALOG,
} from "./bridgeContracts"
import { requestCheckpointList, requestCheckpointRestore } from "./sessionCommands"

describe("bridge contract parity guards", () => {
  it("does not reference deprecated or mismatched route shapes", () => {
    for (const marker of FORBIDDEN_ROUTE_MARKERS) {
      expect(buildSessionDownloadPath("session-1").includes(marker)).toBe(false)
    }
  })

  it("uses normalized download path contract", () => {
    expect(buildSessionDownloadPath("session-1")).toBe("/sessions/session-1/download")
  })

  it("includes normalized bridge route list coverage", () => {
    expect(NORMALIZED_ROUTE_CATALOG).toContain("/sessions/{session_id}/events")
    expect(NORMALIZED_ROUTE_CATALOG).toContain("/sessions/{session_id}/command")
    expect(NORMALIZED_ROUTE_CATALOG).toContain("/status")
  })

  it("declares only canonical command names", () => {
    expect(NORMALIZED_COMMAND_CATALOG).toEqual(["list_checkpoints", "restore_checkpoint", "permission_decision"])
  })

  it("does not emit forbidden command aliases from command wrappers", async () => {
    const attempted: string[] = []
    const post = async (command: string) => {
      attempted.push(command)
      return { status: "ok" }
    }

    await requestCheckpointList(post)
    await requestCheckpointRestore(post, "cp-123")

    for (const command of attempted) {
      expect(FORBIDDEN_COMMAND_ALIASES).not.toContain(command)
    }
  })
})
