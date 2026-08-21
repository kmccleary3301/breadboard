import { describe, expect, it } from "vitest"
import { renderArtifactListTable } from "../src/commands/artifacts.js"

describe("artifacts command", () => {
  it("renders the public artifact size_bytes field", () => {
    const table = renderArtifactListTable([
      {
        name: "conversation",
        digest: "sha256:abc",
        size_bytes: 42,
        media_type: "application/json",
      },
    ])

    expect(table).toContain("conversation")
    expect(table).toContain("42")
  })
})
