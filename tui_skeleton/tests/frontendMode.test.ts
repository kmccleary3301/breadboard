import { describe, expect, it } from "vitest"
import {
  normalizeBreadboardFrontend,
  normalizeLiveShellOwnershipMode,
  normalizeLiveShellRendererHost,
  normalizeLiveShellSceneStrategy,
  normalizeScrollbackPresentationMode,
  resolveBreadboardFrontend,
  resolveFrontendSelectionFromArgv,
  resolveOwnedLiveShellEnabled,
  resolveScrollbackEnabled,
  resolveScrollbackPresentationMode,
  resolveLiveShellOwnershipMode,
  resolveLiveShellRendererHost,
  resolveLiveShellSceneStrategy,
} from "../src/config/frontendMode"

describe("frontendMode", () => {
  it("normalizes product-shell frontend aliases", () => {
    expect(normalizeBreadboardFrontend("classic")).toBe("scrollback")
    expect(normalizeBreadboardFrontend("scrollback")).toBe("scrollback")
    expect(normalizeBreadboardFrontend("opentui")).toBe("opentui")
    expect(normalizeBreadboardFrontend("window")).toBeNull()
  })

  it("normalizes scrollback presentation mode independently", () => {
    expect(normalizeScrollbackPresentationMode("scrollback")).toBe("scrollback")
    expect(normalizeScrollbackPresentationMode("window")).toBe("window")
    expect(normalizeScrollbackPresentationMode("compact")).toBe("window")
    expect(normalizeScrollbackPresentationMode("classic")).toBeNull()
  })

  it("normalizes owned live shell mode aliases independently", () => {
    expect(normalizeLiveShellOwnershipMode("inline")).toBe("inline-scrollback")
    expect(normalizeLiveShellOwnershipMode("scrollback")).toBe("inline-scrollback")
    expect(normalizeLiveShellOwnershipMode("owned-live-shell")).toBe("owned-live")
    expect(normalizeLiveShellOwnershipMode("owned")).toBe("owned-live")
    expect(normalizeLiveShellOwnershipMode("opentui")).toBeNull()
  })

  it("normalizes live shell renderer host aliases independently", () => {
    expect(normalizeLiveShellRendererHost("ink")).toBe("ink-managed")
    expect(normalizeLiveShellRendererHost("current")).toBe("ink-managed")
    expect(normalizeLiveShellRendererHost("renderer-escalated")).toBe("escalated-owned")
    expect(normalizeLiveShellRendererHost("owned-viewport")).toBe("escalated-owned")
    expect(normalizeLiveShellRendererHost("scrollback")).toBeNull()
  })

  it("normalizes scene-owned strategy aliases independently", () => {
    expect(normalizeLiveShellSceneStrategy("scene-owned")).toBe("scene-owned-runtime")
    expect(normalizeLiveShellSceneStrategy("s1")).toBe("scene-owned-runtime")
    expect(normalizeLiveShellSceneStrategy("scene-buffer")).toBe("dedicated-scene-buffer")
    expect(normalizeLiveShellSceneStrategy("s2")).toBe("dedicated-scene-buffer")
    expect(normalizeLiveShellSceneStrategy("ink")).toBeNull()
  })

  it("resolves frontend selection with dedicated env before compat env", () => {
    expect(resolveBreadboardFrontend({ env: { BREADBOARD_TUI_FRONTEND: "opentui", BREADBOARD_TUI_MODE: "classic" } as NodeJS.ProcessEnv })).toBe("opentui")
    expect(resolveBreadboardFrontend({ env: { BREADBOARD_TUI_MODE: "classic" } as NodeJS.ProcessEnv })).toBe("scrollback")
  })

  it("forces scrollback frontend for script mode", () => {
    expect(resolveBreadboardFrontend({ scriptPath: "demo.json", cliValue: "opentui", env: { BREADBOARD_TUI_FRONTEND: "opentui" } as NodeJS.ProcessEnv })).toBe("scrollback")
  })

  it("resolves scrollback presentation with dedicated env before compat env and boolean fallback", () => {
    expect(resolveScrollbackPresentationMode({ BREADBOARD_SCROLLBACK_MODE: "window", BREADBOARD_TUI_MODE: "scrollback" } as NodeJS.ProcessEnv)).toBe("window")
    expect(resolveScrollbackPresentationMode({ BREADBOARD_TUI_MODE: "window" } as NodeJS.ProcessEnv)).toBe("window")
    expect(resolveScrollbackEnabled({ BREADBOARD_TUI_SCROLLBACK: "0" } as NodeJS.ProcessEnv)).toBe(false)
  })

  it("resolves live shell mode with dedicated env before scrollback default", () => {
    expect(resolveLiveShellOwnershipMode({ BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live" } as NodeJS.ProcessEnv)).toBe("owned-live")
    expect(resolveOwnedLiveShellEnabled({ BREADBOARD_TUI_OWNERSHIP_MODE: "owned" } as NodeJS.ProcessEnv)).toBe(true)
    expect(resolveLiveShellOwnershipMode({ BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback" } as NodeJS.ProcessEnv)).toBe("inline-scrollback")
    expect(resolveLiveShellOwnershipMode({} as NodeJS.ProcessEnv)).toBe("inline-scrollback")
  })

  it("resolves renderer host with dedicated env before ownership-aware default", () => {
    expect(resolveLiveShellRendererHost({ BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned" } as NodeJS.ProcessEnv)).toBe("escalated-owned")
    expect(resolveLiveShellRendererHost({ BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback" } as NodeJS.ProcessEnv)).toBe("ink-managed")
    expect(resolveLiveShellRendererHost({} as NodeJS.ProcessEnv)).toBe("ink-managed")
  })

  it("resolves scene-owned strategy with dedicated env before S1 default", () => {
    expect(resolveLiveShellSceneStrategy({ BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "dedicated-scene-buffer" } as NodeJS.ProcessEnv)).toBe("dedicated-scene-buffer")
    expect(resolveLiveShellSceneStrategy({ BREADBOARD_TUI_RENDERER_STRATEGY: "s2" } as NodeJS.ProcessEnv)).toBe("dedicated-scene-buffer")
    expect(resolveLiveShellSceneStrategy({} as NodeJS.ProcessEnv)).toBe("scene-owned-runtime")
  })

  it("parses argv frontend selection for repl/ui", () => {
    expect(resolveFrontendSelectionFromArgv(["node", "bb", "repl", "--tui", "opentui"], {} as NodeJS.ProcessEnv)).toBe("opentui")
    expect(resolveFrontendSelectionFromArgv(["node", "bb", "ui", "--script", "demo.json", "--tui", "opentui"], {} as NodeJS.ProcessEnv)).toBe("scrollback")
  })
})
