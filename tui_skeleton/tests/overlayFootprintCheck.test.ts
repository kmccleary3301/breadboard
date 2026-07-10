import { describe, expect, it } from "vitest"
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises"
import path from "node:path"
import os from "node:os"
import { evaluateOverlayFootprint } from "../tools/assertions/overlayFootprintCheck.js"

const withCaseDir = async (body: string, fn: (dir: string) => Promise<void>) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "overlay-footprint-check-"))
  try {
    await mkdir(path.join(dir, "observer_text"), { recursive: true })
    await writeFile(path.join(dir, "observer_text", "model-picker-open.txt"), body, "utf8")
    await fn(dir)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
}

describe("evaluateOverlayFootprint", () => {
  it("accepts compact model picker sheet with prior context", async () => {
    await withCaseDir(
      [
        "BreadBoard v0.2.0",
        "❯ Try \"refactor <filepath>\"",
        "",
        "╭ Select model ╮",
        "│ Search: <type to filter> │",
        "│ › openai gpt │",
        "╰──────────────╯",
      ].join("\n"),
      async (dir) => {
        expect(await evaluateOverlayFootprint(dir)).toEqual([])
      },
    )
  })

  it("rejects giant blank gaps before model picker", async () => {
    await withCaseDir(
      [
        "BreadBoard v0.2.0",
        "❯ Try \"refactor <filepath>\"",
        "",
        "",
        "",
        "",
        "",
        "╭ Select model ╮",
      ].join("\n"),
      async (dir) => {
        expect((await evaluateOverlayFootprint(dir)).map((item) => item.id)).toContain("model-picker-large-pre-overlay-gap")
      },
    )
  })

  it("accepts trailing viewport whitespace after a settled model picker snapshot", async () => {
    await withCaseDir(
      [
        "BreadBoard v0.2.0",
        "❯ Type your request…",
        "",
        "╭ Select model ╮",
        "│ Search: <type to filter> │",
        "│ › openai gpt │",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
      ].join("\n"),
      async (dir) => {
        expect(await evaluateOverlayFootprint(dir)).toEqual([])
      },
    )
  })

  it("rejects large interior blank gaps inside a model picker snapshot", async () => {
    await withCaseDir(
      [
        "BreadBoard v0.2.0",
        "❯ Type your request…",
        "",
        "╭ Select model ╮",
        "│ Search: <type to filter> │",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "│ › openai gpt │",
      ].join("\n"),
      async (dir) => {
        expect((await evaluateOverlayFootprint(dir)).map((item) => item.id)).toContain("overlay-large-blank-gap")
      },
    )
  })

  it("rejects duplicated model picker slabs", async () => {
    await withCaseDir(
      [
        "BreadBoard v0.2.0",
        "❯",
        "Select model",
        "row",
        "Select model",
      ].join("\n"),
      async (dir) => {
        expect((await evaluateOverlayFootprint(dir)).map((item) => item.id)).toContain("model-picker-duplicated")
      },
    )
  })

  it("rejects stale model picker rows after a closed picker snapshot", async () => {
    await withCaseDir(
      [
        "# model-picker-closed",
        "BreadBoard v0.2.0",
        "│  Models                                                  │",
        "",
        "❯ picker-close-probe",
        "• [ready] enter send",
      ].join("\n"),
      async (dir) => {
        expect((await evaluateOverlayFootprint(dir)).map((item) => item.id)).toContain("model-picker-stale-after-close")
      },
    )
  })
})
