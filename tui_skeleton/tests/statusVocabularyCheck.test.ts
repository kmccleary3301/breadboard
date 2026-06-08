import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"
import { evaluateStatusVocabulary } from "../tools/assertions/statusVocabularyCheck.ts"

const makeCase = async (body: string): Promise<string> => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-status-vocab-"))
  await mkdir(path.join(dir, "terminal_text"), { recursive: true })
  await writeFile(path.join(dir, "terminal_text", "visible_final.txt"), body)
  return dir
}

describe("statusVocabularyCheck", () => {
  it("accepts approved Live Shell statuses", async () => {
    const dir = await makeCase("Live Shell · Ready · Ctrl+O Transcript\nLive Shell · Streaming... · Working\n")
    try {
      expect(await evaluateStatusVocabulary(dir)).toEqual([])
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it("rejects unapproved Live Shell status words", async () => {
    const dir = await makeCase("Live Shell · settled · session abc\n")
    try {
      const anomalies = await evaluateStatusVocabulary(dir)
      expect(anomalies.map((item) => item.id)).toContain("status-vocabulary-unapproved")
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})
