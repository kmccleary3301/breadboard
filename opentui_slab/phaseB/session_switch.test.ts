import { describe, expect, test } from "bun:test"

import { resolveCommandTargetSessionId } from "./session_switch.ts"

describe("resolveCommandTargetSessionId", () => {
  test("prefers explicit target session id when available", () => {
    const target = resolveCommandTargetSessionId("session_child_next", {
      switched: true,
      target_session_id: "sess-target",
      child_session_id: "sess-child",
    })
    expect(target).toBe("sess-target")
  })

  test("uses child session id for child navigation when target absent", () => {
    const target = resolveCommandTargetSessionId("session_child_previous", {
      switched: true,
      child_session_id: "sess-child-2",
    })
    expect(target).toBe("sess-child-2")
  })

  test("uses parent session id for parent navigation when target absent", () => {
    const target = resolveCommandTargetSessionId("session_parent", {
      switched: true,
      parent_session_id: "sess-parent-1",
      child_session_id: "sess-child-1",
    })
    expect(target).toBe("sess-parent-1")
  })

  test("returns null when switched=false", () => {
    const target = resolveCommandTargetSessionId("session_parent", {
      switched: false,
      target_session_id: "sess-parent-1",
    })
    expect(target).toBeNull()
  })

  test("returns null for invalid detail payload", () => {
    expect(resolveCommandTargetSessionId("session_parent", null)).toBeNull()
    expect(resolveCommandTargetSessionId("session_parent", undefined)).toBeNull()
    expect(resolveCommandTargetSessionId("session_parent", {})).toBeNull()
  })
})

