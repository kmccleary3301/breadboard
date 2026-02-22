import { describe, expect, it } from "vitest"
import type { PermissionLedgerRow } from "./projection"
import { applyPermissionLedgerFilters } from "./permissionLedgerFilter"

const row = (partial: Partial<PermissionLedgerRow> & Pick<PermissionLedgerRow, "requestId" | "tool">): PermissionLedgerRow => ({
  requestId: partial.requestId,
  tool: partial.tool,
  scope: partial.scope ?? "project",
  decision: partial.decision ?? "allow-once",
  rule: partial.rule ?? null,
  note: partial.note ?? null,
  timestamp: partial.timestamp ?? 1_000,
  revoked: partial.revoked ?? false,
})

describe("permissionLedgerFilter", () => {
  it("filters by tool query case-insensitively", () => {
    const rows = [
      row({ requestId: "1", tool: "exec_command" }),
      row({ requestId: "2", tool: "read_file" }),
    ]
    const filtered = applyPermissionLedgerFilters(rows, { tool: "  EXEC  ", scope: "all", decision: "all" })
    expect(filtered.map((entry) => entry.requestId)).toEqual(["1"])
  })

  it("filters by scope and decision", () => {
    const rows = [
      row({ requestId: "1", tool: "exec_command", scope: "project", decision: "allow-always" }),
      row({ requestId: "2", tool: "exec_command", scope: "session", decision: "allow-always" }),
      row({ requestId: "3", tool: "exec_command", scope: "project", decision: "deny-once" }),
    ]
    const filtered = applyPermissionLedgerFilters(rows, { tool: "", scope: "project", decision: "allow-always" })
    expect(filtered.map((entry) => entry.requestId)).toEqual(["1"])
  })
})
