import { describe, it, expect } from "vitest"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { buildCTreeTreeRows } from "../treeView.js"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const fixturePath = path.resolve(__dirname, "../../../../../tests/fixtures/ctrees/simple_ctree_tree_frozen.json")

const loadFixture = () => JSON.parse(fs.readFileSync(fixturePath, "utf8"))

describe("buildCTreeTreeRows", () => {
  it("builds a deterministic depth-first row list", () => {
    const tree = loadFixture()
    const { rows } = buildCTreeTreeRows(tree, new Set())
    const ids = rows.map((row) => row.id)
    expect(ids).toEqual([
      "ctrees:root",
      "ctrees:turn:0",
      "ctrees:turn:1",
      "ctrees:node:n00000001_54dd7f92",
      "ctrees:node:n00000002_282a55bf",
      "ctrees:tasks",
      "ctrees:task:t1",
      "ctrees:node:n00000003_c21875af",
    ])
    const turnRow = rows.find((row) => row.id === "ctrees:turn:1")
    const messageRow = rows.find((row) => row.id === "ctrees:node:n00000001_54dd7f92")
    const taskRow = rows.find((row) => row.id === "ctrees:task:t1")
    expect(turnRow?.depth).toBe(1)
    expect(messageRow?.depth).toBe(2)
    expect(taskRow?.depth).toBe(2)
  })

  it("respects collapsed parent nodes", () => {
    const tree = loadFixture()
    const { rows } = buildCTreeTreeRows(tree, new Set(["ctrees:turn:1"]))
    const ids = rows.map((row) => row.id)
    expect(ids).not.toContain("ctrees:node:n00000001_54dd7f92")
    expect(ids).not.toContain("ctrees:node:n00000002_282a55bf")
  })
})
