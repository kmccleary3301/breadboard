import { promises as fs } from "node:fs"

export interface GridDiffOptions {
  readonly labelA?: string
  readonly labelB?: string
}

export interface GridDiffResult {
  readonly areEqual: boolean
  readonly report: string
}

export const readGridLines = async (filePath: string): Promise<string[]> => {
  const contents = await fs.readFile(filePath, "utf8")
  return contents.split(/\r?\n/)
}

export const computeGridDiff = (
  linesA: ReadonlyArray<string>,
  linesB: ReadonlyArray<string>,
  options?: GridDiffOptions,
): GridDiffResult => {
  const labelA = options?.labelA ?? "A"
  const labelB = options?.labelB ?? "B"
  const maxLen = Math.max(linesA.length, linesB.length)
  const blocks: string[] = []
  let differences = 0
  for (let i = 0; i < maxLen; i += 1) {
    const left = i < linesA.length ? linesA[i] : ""
    const right = i < linesB.length ? linesB[i] : ""
    if (left === right) continue
    differences += 1
    blocks.push(`@@ line ${i + 1} @@`)
    blocks.push(`- [${labelA}] ${left}`)
    blocks.push(`+ [${labelB}] ${right}`)
    blocks.push("")
  }
  if (differences === 0) {
    return {
      areEqual: true,
      report: `Grids are identical (${linesA.length} rows).\n`,
    }
  }
  const header = [`Total differences: ${differences}`, ""]
  return {
    areEqual: false,
    report: [...header, ...blocks].join("\n"),
  }
}

export const computeFileGridDiff = async (
  fileA: string,
  fileB: string,
  options?: GridDiffOptions,
): Promise<GridDiffResult> => {
  const [left, right] = await Promise.all([readGridLines(fileA), readGridLines(fileB)])
  return computeGridDiff(left, right, options)
}
