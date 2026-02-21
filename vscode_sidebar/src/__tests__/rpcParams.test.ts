import test from "node:test"
import assert from "node:assert/strict"
import {
  parseApprovePermissionParams,
  parseAttachSessionParams,
  parseDeleteSessionParams,
  parseListFilesParams,
  parseOpenDiffParams,
  parseReadSnippetParams,
  parseSendMessageParams,
  parseStopSessionParams,
} from "../rpcParams"

test("rpc param parsers accept valid payloads", () => {
  assert.deepEqual(parseAttachSessionParams({ sessionId: "s1" }), { sessionId: "s1" })
  assert.deepEqual(parseSendMessageParams({ sessionId: "s1", text: "hello" }), { sessionId: "s1", text: "hello" })
  assert.deepEqual(parseStopSessionParams({ sessionId: "s1" }), { sessionId: "s1" })
  assert.deepEqual(parseDeleteSessionParams({ sessionId: "s1" }), { sessionId: "s1" })
  assert.deepEqual(parseListFilesParams({ sessionId: "s1", path: "src" }), { sessionId: "s1", path: "src" })
  assert.deepEqual(
    parseReadSnippetParams({ sessionId: "s1", path: "README.md", headLines: 10, tailLines: 5, maxBytes: 1000 }),
    { sessionId: "s1", path: "README.md", headLines: 10, tailLines: 5, maxBytes: 1000 },
  )
  assert.deepEqual(
    parseOpenDiffParams({ sessionId: "s1", filePath: "a.txt", artifactPath: "art/a.txt" }),
    { sessionId: "s1", filePath: "a.txt", artifactPath: "art/a.txt" },
  )
  assert.deepEqual(
    parseApprovePermissionParams({ sessionId: "s1", requestId: "req-1", decision: "allow_once" }),
    { sessionId: "s1", requestId: "req-1", decision: "allow_once" },
  )
})

test("rpc param parsers reject invalid payloads", () => {
  assert.equal(parseAttachSessionParams({}), null)
  assert.equal(parseSendMessageParams({ text: "" }), null)
  assert.deepEqual(parseStopSessionParams({}), {})
  assert.equal(parseDeleteSessionParams({}), null)
  assert.equal(parseListFilesParams({ path: "." }), null)
  assert.equal(parseReadSnippetParams({ sessionId: "s1" }), null)
  assert.equal(parseOpenDiffParams({ sessionId: "s1" }), null)
  assert.equal(parseApprovePermissionParams({ sessionId: "s1", requestId: "x", decision: "nope" }), null)
})
