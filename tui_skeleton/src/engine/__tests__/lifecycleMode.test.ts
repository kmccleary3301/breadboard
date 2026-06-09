import { describe, expect, it } from "vitest"
import {
  ENGINE_LIFECYCLE_MODE_CONTRACT,
  ENGINE_LIFECYCLE_MODES,
  ENGINE_LIFECYCLE_STATUS_CONTRACT,
  isLocalEngineBaseUrl,
  normalizeEngineLifecycleMode,
  resolveEngineLifecycleMode,
} from "../lifecycleMode.js"

describe("normalizeEngineLifecycleMode", () => {
  it("maps legacy managed/spawn/auto modes to local-owned", () => {
    expect(normalizeEngineLifecycleMode("managed")).toBe("local-owned")
    expect(normalizeEngineLifecycleMode("spawn")).toBe("local-owned")
    expect(normalizeEngineLifecycleMode("auto")).toBe("local-owned")
  })

  it("rejects unknown modes", () => {
    expect(normalizeEngineLifecycleMode("surprising")).toBeNull()
  })
})

describe("isLocalEngineBaseUrl", () => {
  it("classifies localhost variants as local", () => {
    expect(isLocalEngineBaseUrl("http://127.0.0.1:9099")).toBe(true)
    expect(isLocalEngineBaseUrl("http://localhost:9099")).toBe(true)
    expect(isLocalEngineBaseUrl("http://[::1]:9099")).toBe(true)
  })

  it("classifies non-local URLs as remote", () => {
    expect(isLocalEngineBaseUrl("https://breadboard.example.com")).toBe(false)
  })
})

describe("resolveEngineLifecycleMode", () => {
  it("lets CLI mode override config, env, and base URL defaults", () => {
    expect(
      resolveEngineLifecycleMode({
        cliMode: "remote",
        configMode: "local-owned",
        envMode: "external",
        baseUrl: "http://127.0.0.1:9099",
      }),
    ).toMatchObject({
      mode: "remote",
      modeSource: "cli",
      owned: false,
      allowSpawn: false,
      restartPolicy: "remote-only",
    })
  })

  it("lets config mode override env mode", () => {
    expect(
      resolveEngineLifecycleMode({
        configMode: "local-owned",
        envMode: "external",
        baseUrl: "http://127.0.0.1:9099",
      }),
    ).toMatchObject({
      mode: "local-owned",
      modeSource: "config",
      owned: true,
      allowSpawn: true,
      restartPolicy: "bounded",
    })
  })

  it("uses BREADBOARD_ENGINE_MODE when no CLI or config mode is present", () => {
    expect(
      resolveEngineLifecycleMode({
        envMode: "external",
        baseUrl: "http://127.0.0.1:9099",
      }),
    ).toMatchObject({
      mode: "external",
      modeSource: "env",
      owned: false,
      allowSpawn: false,
      restartPolicy: "external-only",
    })
  })

  it("treats explicit local base URLs as owned by default", () => {
    expect(
      resolveEngineLifecycleMode({
        baseUrl: "http://127.0.0.1:9500",
        explicitBaseUrlConfigured: true,
      }),
    ).toMatchObject({
      mode: "local-owned",
      modeSource: "explicit-base-url",
      owned: true,
      allowSpawn: true,
      restartPolicy: "bounded",
    })
  })

  it("treats explicit non-local base URLs as remote", () => {
    expect(
      resolveEngineLifecycleMode({
        baseUrl: "https://engine.example.com",
        explicitBaseUrlConfigured: true,
      }),
    ).toMatchObject({
      mode: "remote",
      modeSource: "explicit-base-url",
      owned: false,
      allowSpawn: false,
      restartPolicy: "remote-only",
    })
  })

  it("defaults plain local bb to local-owned", () => {
    expect(
      resolveEngineLifecycleMode({
        baseUrl: "http://127.0.0.1:9099",
        explicitBaseUrlConfigured: false,
      }),
    ).toMatchObject({
      mode: "local-owned",
      modeSource: "default-local",
      owned: true,
      allowSpawn: true,
      restartPolicy: "bounded",
    })
  })

  it("resolves skipped commands to off", () => {
    expect(
      resolveEngineLifecycleMode({
        commandSkipsEngine: true,
        baseUrl: "http://127.0.0.1:9099",
      }),
    ).toMatchObject({
      mode: "off",
      modeSource: "command-skip",
      owned: false,
      allowSpawn: false,
      restartPolicy: "disabled",
    })
  })
})

describe("ENGINE_LIFECYCLE_MODE_CONTRACT", () => {
  it("covers every lifecycle mode with explicit ownership actions", () => {
    expect(Object.keys(ENGINE_LIFECYCLE_MODE_CONTRACT).sort()).toEqual([...ENGINE_LIFECYCLE_MODES].sort())
    for (const mode of ENGINE_LIFECYCLE_MODES) {
      const contract = ENGINE_LIFECYCLE_MODE_CONTRACT[mode]
      expect(contract.mode).toBe(mode)
      expect(contract.spawn).toMatch(/^(allowed|forbidden|mode-dependent)$/)
      expect(contract.attach).toMatch(/^(allowed|forbidden|mode-dependent)$/)
      expect(contract.health).toMatch(/^(allowed|forbidden|mode-dependent)$/)
      expect(contract.restart).toMatch(/^(allowed|forbidden|mode-dependent)$/)
      expect(contract.kill).toMatch(/^(allowed|forbidden|mode-dependent)$/)
      expect(contract.shutdown).toMatch(/^(allowed|forbidden|mode-dependent)$/)
      expect(contract.logs).toMatch(/^(allowed|forbidden|mode-dependent)$/)
      expect(contract.transcriptPolicy).toMatch(/^(runtime-only|diagnostic-only)$/)
      expect(contract.footerPolicy).toMatch(/^(show-mode-and-recovery|show-unowned-boundary|show-remote-boundary|hidden)$/)
    }
  })

  it("keeps ownership and restart semantics aligned with resolver output", () => {
    for (const mode of ENGINE_LIFECYCLE_MODES) {
      const resolved = resolveEngineLifecycleMode({
        cliMode: mode,
        baseUrl: "http://127.0.0.1:9099",
      })
      const contract = ENGINE_LIFECYCLE_MODE_CONTRACT[mode]
      expect(resolved.owned).toBe(contract.kill === "allowed")
      expect(resolved.allowSpawn).toBe(contract.spawn === "allowed")
      expect(resolved.restartPolicy).toBe(
        mode === "local-owned" ? "bounded" : mode === "external" ? "external-only" : mode === "remote" ? "remote-only" : "disabled",
      )
    }
  })
})

describe("ENGINE_LIFECYCLE_STATUS_CONTRACT", () => {
  it("defines footer, composer, draft, turn, transcript, and recovery policy for each status", () => {
    expect(Object.keys(ENGINE_LIFECYCLE_STATUS_CONTRACT).length).toBeGreaterThanOrEqual(10)
    for (const [key, contract] of Object.entries(ENGINE_LIFECYCLE_STATUS_CONTRACT)) {
      expect(contract.key).toBe(key)
      expect(contract.label).toMatch(/^\[[^\]]+\]$/)
      expect(typeof contract.composerAcceptsInput).toBe("boolean")
      expect(typeof contract.preservesDraft).toBe("boolean")
      expect(typeof contract.unresolvedTurn).toBe("boolean")
      expect(contract.transcriptBodyAllowed).toBe(false)
      expect(contract.recoveryAction).toMatch(/^(send|wait|retry|resume|restart|external-restart|remote-retry|exit)$/)
    }
  })

  it("forbids ready-like input during unresolved recovery states", () => {
    for (const contract of Object.values(ENGINE_LIFECYCLE_STATUS_CONTRACT)) {
      if (contract.unresolvedTurn) {
        expect(contract.composerAcceptsInput).toBe(false)
      }
    }
    expect(ENGINE_LIFECYCLE_STATUS_CONTRACT.ready.composerAcceptsInput).toBe(true)
    expect(ENGINE_LIFECYCLE_STATUS_CONTRACT.recovered.composerAcceptsInput).toBe(true)
  })
})
