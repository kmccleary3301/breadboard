import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

import { afterEach, describe, expect, it } from "vitest"

import { runContractChecks } from "../tools/assertions/contractChecks.js"

const tempPaths: string[] = []

afterEach(async () => {
  await Promise.all(tempPaths.splice(0).map((filePath) => fs.rm(filePath, { force: true }).catch(() => undefined)))
})

const writeSse = async (contents: string): Promise<string> => {
  const filePath = path.join(os.tmpdir(), `bb-contract-${Date.now()}-${Math.random().toString(16).slice(2)}.txt`)
  tempPaths.push(filePath)
  await fs.writeFile(filePath, contents, "utf8")
  return filePath
}

describe("contractChecks", () => {
  it("does not warn on legacy payload-only events in non-strict mode", async () => {
    const filePath = await writeSse(
      'data: {"id":"1","type":"assistant.message.delta","payload":{"delta":"hi"},"seq":1,"timestamp":1}\n\n',
    )

    const report = await runContractChecks(filePath)
    expect(report.warnings).not.toContainEqual(expect.objectContaining({ code: "data-missing" }))
  })

  it("ignores bootstrap todo snapshot tool_result without prior tool_call", async () => {
    const filePath = await writeSse(
      'data: {"id":"1","type":"tool_result","payload":{"call_id":"todo:snapshot:connect:abc","todo":{"op":"snapshot","items":[]}},"timestamp":1}\n\n' +
        'data: {"id":"1b","type":"tool_result","payload":{"call_id":"todo:snapshot:init","todo":{"op":"snapshot","items":[]}},"timestamp":1}\n\n' +
        'data: {"id":"2","type":"run_finished","payload":{"completed":true},"seq":1,"timestamp":2}\n\n',
    )

    const report = await runContractChecks(filePath)
    expect(report.errors).not.toContainEqual(expect.objectContaining({ code: "tool-result-without-call" }))
    expect(report.warnings).not.toContainEqual(expect.objectContaining({ code: "seq-missing" }))
  })
})
