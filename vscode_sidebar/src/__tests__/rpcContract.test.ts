import test from "node:test"
import assert from "node:assert/strict"
import { parseRpcReq } from "../rpcContract"

test("parseRpcReq accepts valid v1 request envelope", () => {
  const req = parseRpcReq({
    v: 1,
    kind: "req",
    id: "req-1",
    method: "bb.listSessions",
    params: { limit: 10 },
  })
  assert.deepEqual(req, {
    v: 1,
    kind: "req",
    id: "req-1",
    method: "bb.listSessions",
    params: { limit: 10 },
  })
})

test("parseRpcReq rejects invalid envelope shapes", () => {
  assert.equal(parseRpcReq(null), null)
  assert.equal(parseRpcReq({}), null)
  assert.equal(parseRpcReq({ v: 2, kind: "req", id: "x", method: "m" }), null)
  assert.equal(parseRpcReq({ v: 1, kind: "evt", id: "x", method: "m" }), null)
  assert.equal(parseRpcReq({ v: 1, kind: "req", id: "", method: "m" }), null)
  assert.equal(parseRpcReq({ v: 1, kind: "req", id: "x", method: "" }), null)
})
