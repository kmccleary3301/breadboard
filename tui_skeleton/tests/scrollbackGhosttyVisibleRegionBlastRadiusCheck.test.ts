import { mkdtemp, mkdir, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"
import { evaluateScrollbackGhosttyVisibleRegionBlastRadius } from "../tools/assertions/scrollbackGhosttyVisibleRegionBlastRadiusCheck.js"

const makeCase = async (files: Record<string, string>): Promise<string> => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-ghostty-visible-check-"))
  const observer = path.join(dir, "observer_text")
  await mkdir(observer, { recursive: true })
  await writeFile(path.join(dir, "app_start_anchor.txt"), '{"mode": "preserved-scrollback","landingLifecycleState":"fresh-visible","landingLifecycleReason":"rich-landing-fits-active-region"}\n', "utf8")
  await writeFile(path.join(dir, "managed_region_bounds.ndjson"), '{"event":"managed_region_bounds"}\n', "utf8")
  await writeFile(
    path.join(dir, "surface_model.ndjson"),
    '{"event":"surface_model","landingLifecycleState":"fresh-visible","landingLifecycleReason":"rich-landing-fits-active-region","landingLifecycleVisibleInline":true,"landingLifecycleCommittedSnapshot":false,"pendingResponse":false,"transcriptCommittedCount":0,"transcriptTailCount":0}\n',
    "utf8",
  )
  await writeFile(
    path.join(observer, "startup.txt"),
    'BreadBoard v0.0.0\nTips for getting started\nUsing Config\n❯ Try "refactor <filepath>"\n• [ready]\n',
    "utf8",
  )
  for (const [name, body] of Object.entries(files)) {
    await writeFile(path.join(observer, name), body, "utf8")
  }
  return dir
}

describe("scrollbackGhosttyVisibleRegionBlastRadiusCheck", () => {
  it("keeps visible-pane and scrollback-history duplicate clauses separate", async () => {
    const dir = await makeCase({
      "turn1-settled.tmux.txt": "# Streaming\nfirst item\n",
      "turn1-settled.tmux_scrollback.txt": "# Streaming\nfirst item\n",
      "after-width-shrink.tmux.txt": [
        "❯ Write a markdown answer",
        "# Streaming",
        "first item",
        "second item",
        "third item",
        "mode slow",
        "lane ghostty",
        "console.log('ghostty')",
      ].join("\n"),
      "after-width-shrink.tmux_scrollback.txt": [
        "❯ Write a markdown answer",
        "# Streaming",
        "old-width partial",
        "❯ Write a markdown answer",
        "# Streaming",
        "console.log('ghostty')",
      ].join("\n"),
    })

    const anomalies = await evaluateScrollbackGhosttyVisibleRegionBlastRadius(dir)
    expect(anomalies.map((entry) => entry.id)).toEqual([
      "after-shrink-scrollback-duplicate-write-a-markdown-answer",
      "after-shrink-scrollback-duplicate--streaming",
    ])
  })

  it("flags visible duplicates independently when the current pane itself is corrupted", async () => {
    const dir = await makeCase({
      "turn1-settled.tmux.txt": "# Streaming\nfirst item\n",
      "turn1-settled.tmux_scrollback.txt": "# Streaming\nfirst item\n",
      "after-width-shrink.tmux.txt": [
        "❯ Write a markdown answer",
        "# Streaming",
        "❯ Write a markdown answer",
        "# Streaming",
        "first item",
        "console.log('ghostty')",
      ].join("\n"),
      "after-width-shrink.tmux_scrollback.txt": "❯ Write a markdown answer\n# Streaming\nfirst item\nconsole.log('ghostty')\n",
    })

    const anomalies = await evaluateScrollbackGhosttyVisibleRegionBlastRadius(dir)
    expect(anomalies.map((entry) => entry.id)).toEqual([
      "after-shrink-visible-near-blank",
      "after-shrink-visible-duplicate-write-a-markdown-answer",
      "after-shrink-visible-duplicate--streaming",
    ])
  })

  it("reads Ghostty-native screen and scrollback exports when tmux captures are absent", async () => {
    const dir = await makeCase({
      "turn1-settled.ghostty_screen.txt": "# Streaming\nfirst item\n",
      "turn1-settled.ghostty_scrollback.txt": "# Streaming\nfirst item\n",
      "after-width-shrink.ghostty_screen.txt": [
        "❯ Write a markdown answer",
        "# Streaming",
        "first item",
        "second item",
        "third item",
        "mode native",
        "lane ghostty",
        "console.log('ghostty')",
      ].join("\n"),
      "after-width-shrink.ghostty_scrollback.txt": [
        "❯ Write a markdown answer",
        "# Streaming",
        "old-width partial",
        "❯ Write a markdown answer",
        "# Streaming",
        "console.log('ghostty')",
      ].join("\n"),
    })

    const anomalies = await evaluateScrollbackGhosttyVisibleRegionBlastRadius(dir)
    expect(anomalies.map((entry) => entry.id)).toEqual([
      "after-shrink-scrollback-duplicate-write-a-markdown-answer",
      "after-shrink-scrollback-duplicate--streaming",
    ])
  })

  it("flags a missing second-turn context when a turn2 snapshot exists", async () => {
    const dir = await makeCase({
      "turn1-settled.ghostty_screen.txt": "# Streaming\nfirst item\n",
      "turn1-settled.ghostty_scrollback.txt": "# Streaming\nfirst item\n",
      "after-width-shrink.ghostty_screen.txt": [
        "❯ Write a markdown answer",
        "# Streaming",
        "first item",
        "second item",
        "third item",
        "mode native",
        "lane ghostty",
        "console.log('ghostty')",
      ].join("\n"),
      "after-width-shrink.ghostty_scrollback.txt": "❯ Write a markdown answer\n# Streaming\nfirst item\nconsole.log('ghostty')\n",
      "turn2-settled.ghostty_screen.txt": "first turn only\n",
    })

    const anomalies = await evaluateScrollbackGhosttyVisibleRegionBlastRadius(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("turn2-context-missing")
  })

  it("flags a turn2 settled snapshot that is still actively responding", async () => {
    const dir = await makeCase({
      "turn1-settled.ghostty_screen.txt": "# Streaming\nfirst item\n",
      "turn1-settled.ghostty_scrollback.txt": "# Streaming\nfirst item\n",
      "after-width-shrink.ghostty_screen.txt": [
        "❯ Write a markdown answer",
        "# Streaming",
        "first item",
        "second item",
        "third item",
        "mode native",
        "lane ghostty",
        "console.log('ghostty')",
      ].join("\n"),
      "after-width-shrink.ghostty_scrollback.txt": "❯ Write a markdown answer\n# Streaming\nfirst item\nconsole.log('ghostty')\n",
      "turn2-settled.ghostty_screen.txt": "❯ Write a second markdown answer after the resize\n✶ [responding] elapsed 24s · follow live · esc interrupt\n",
    })

    const anomalies = await evaluateScrollbackGhosttyVisibleRegionBlastRadius(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("turn2-settled-still-active")
  })

})
