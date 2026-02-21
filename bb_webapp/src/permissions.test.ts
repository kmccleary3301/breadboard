import { describe, expect, it } from "vitest"
import type { PermissionRequestRow } from "./projection"
import { buildPermissionDecisionPayload } from "./permissions"

const request: PermissionRequestRow = {
  requestId: "permission_1",
  tool: "bash",
  kind: "shell",
  summary: "Permission required",
  diffText: null,
  ruleSuggestion: "npm install *",
  defaultScope: "project",
  rewindable: true,
  createdAt: Date.now(),
}

describe("buildPermissionDecisionPayload", () => {
  it("builds allow-always payload with scope and rule fallback", () => {
    const payload = buildPermissionDecisionPayload(
      request,
      { note: "approved", rule: "", scope: "session" },
      "allow-always",
    )
    expect(payload).toEqual({
      request_id: "permission_1",
      decision: "allow-always",
      note: "approved",
      scope: "session",
      rule: "npm install *",
    })
  })

  it("builds deny-stop payload with explicit stop flag", () => {
    const payload = buildPermissionDecisionPayload(
      request,
      { note: "", rule: "", scope: "project" },
      "deny-stop",
    )
    expect(payload).toEqual({
      request_id: "permission_1",
      decision: "deny-stop",
      stop: true,
    })
  })
})
