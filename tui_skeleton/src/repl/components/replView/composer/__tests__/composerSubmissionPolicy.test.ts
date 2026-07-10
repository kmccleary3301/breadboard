import { describe, expect, it } from "vitest"
import { resolveComposerSubmission } from "../composerSubmissionPolicy.js"

describe("resolveComposerSubmission", () => {
  it("classifies slash, at-command, shell-prefix, prompt, and empty submissions", () => {
    expect(resolveComposerSubmission("   ").kind).toBe("empty")
    expect(resolveComposerSubmission("/models").kind).toBe("slash")
    expect(resolveComposerSubmission("@read src/main.ts").kind).toBe("at-command")
    expect(resolveComposerSubmission("hello").kind).toBe("prompt")
    expect(resolveComposerSubmission("!pwd && ls")).toMatchObject({
      kind: "shell-prefix",
      normalized: "!pwd && ls",
      shellCommand: "pwd && ls",
    })
  })

  it("turns shell-prefix input into an explicit shell intent payload", () => {
    const plan = resolveComposerSubmission("!pwd")
    expect(plan.payload).toContain("Run this shell command")
    expect(plan.payload).toContain("```bash\npwd\n```")
  })
})
