import { describe, expect, it } from "vitest"
import {
  renderActionArrowLine,
  renderActionFromLine,
  renderActionItemsLine,
  renderActionToLine,
  renderSavedToLine,
} from "../src/commands/commandListDetail.js"

describe("commandListDetail", () => {
  it("renders saved-to lines", () => {
    expect(renderSavedToLine("File", "/tmp/out.txt")).toBe("File saved to /tmp/out.txt")
  })

  it("renders action-to lines", () => {
    expect(renderActionToLine("Installed", "plugin@1.0.0", "/tmp/plugins/p"))
      .toBe("Installed plugin@1.0.0 to /tmp/plugins/p")
  })

  it("renders action-from lines", () => {
    expect(renderActionFromLine("Removed", "plugin", "workspace"))
      .toBe("Removed plugin from workspace")
  })

  it("renders action-arrow lines", () => {
    expect(renderActionArrowLine("Packed", "plugin@1.0.0", "/tmp/plugin.tgz"))
      .toBe("Packed plugin@1.0.0 -> /tmp/plugin.tgz")
  })

  it("renders list-bearing action lines with fallback", () => {
    expect(renderActionItemsLine("Applying diff affecting", ["a.ts", "b.ts"], "fallback"))
      .toBe("Applying diff affecting a.ts, b.ts")
    expect(renderActionItemsLine("Applying diff affecting", [], "Applying diff (no file headers detected)."))
      .toBe("Applying diff (no file headers detected).")
  })
})
