import type { PermissionLedgerDecision, PermissionLedgerRow } from "./projection"

export type PermissionLedgerFilterState = {
  tool: string
  scope: "all" | "session" | "project"
  decision: "all" | PermissionLedgerDecision
}

export const applyPermissionLedgerFilters = (
  rows: readonly PermissionLedgerRow[],
  filter: PermissionLedgerFilterState,
): PermissionLedgerRow[] => {
  const toolQuery = filter.tool.trim().toLowerCase()
  return rows.filter((row) => {
    if (toolQuery && !row.tool.toLowerCase().includes(toolQuery)) return false
    if (filter.scope !== "all" && row.scope !== filter.scope) return false
    if (filter.decision !== "all" && row.decision !== filter.decision) return false
    return true
  })
}
