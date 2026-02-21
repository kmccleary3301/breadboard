export type RpcReq = {
  v: 1
  kind: "req"
  id: string
  method: string
  params?: unknown
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null

const readNonEmptyString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value : null

export const parseRpcReq = (value: unknown): RpcReq | null => {
  const rec = asRecord(value)
  if (!rec) return null
  if (rec.v !== 1 || rec.kind !== "req") return null
  const id = readNonEmptyString(rec.id)
  const method = readNonEmptyString(rec.method)
  if (!id || !method) return null
  return {
    v: 1,
    kind: "req",
    id,
    method,
    ...(Object.prototype.hasOwnProperty.call(rec, "params") ? { params: rec.params } : {}),
  }
}
