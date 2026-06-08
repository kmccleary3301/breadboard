import { execFile } from "node:child_process"
import { promises as fs } from "node:fs"
import path from "node:path"
import { promisify } from "node:util"
import { writeClipboardText } from "../../util/clipboard.js"

const execFileAsync = promisify(execFile)

export interface WorkingTreeChangedFile {
  readonly path: string
  readonly status: string
  readonly staged: boolean
  readonly unstaged: boolean
  readonly untracked: boolean
}

export interface WorkingTreeDiffSummary {
  readonly kind: "not-git" | "clean" | "dirty" | "error"
  readonly workspace: string
  readonly repoRoot?: string
  readonly changedFiles: readonly WorkingTreeChangedFile[]
  readonly additions: number
  readonly deletions: number
  readonly patch: string
  readonly untrackedCount: number
  readonly warnings: readonly string[]
  readonly error?: string
}

export interface WorkingTreePatchWriteResult {
  readonly ok: boolean
  readonly path?: string
  readonly bytes?: number
  readonly error?: string
}

export interface WorkingTreePatchCopyResult {
  readonly ok: boolean
  readonly method: string
  readonly bytes?: number
  readonly error?: string
}

const execGit = async (
  workspace: string,
  args: readonly string[],
  options: { readonly maxBuffer?: number } = {},
): Promise<string> => {
  const { stdout } = await execFileAsync("git", [...args], {
    cwd: workspace,
    maxBuffer: options.maxBuffer ?? 10 * 1024 * 1024,
    timeout: 10_000,
  })
  return stdout
}

const parseStatusLine = (line: string): WorkingTreeChangedFile | null => {
  if (!line.trim()) return null
  const xy = line.slice(0, 2)
  const rawPath = line.slice(3).trim()
  const path = rawPath.includes(" -> ") ? rawPath.split(" -> ").pop()?.trim() ?? rawPath : rawPath
  if (!path) return null
  const x = xy[0] ?? " "
  const y = xy[1] ?? " "
  const untracked = xy === "??"
  return {
    path,
    status: xy,
    staged: !untracked && x !== " ",
    unstaged: !untracked && y !== " ",
    untracked,
  }
}

const parseNumstat = (raw: string): { additions: number; deletions: number } => {
  let additions = 0
  let deletions = 0
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim()) continue
    const [addRaw, delRaw] = line.split(/\s+/)
    const add = Number(addRaw)
    const del = Number(delRaw)
    if (Number.isFinite(add)) additions += add
    if (Number.isFinite(del)) deletions += del
  }
  return { additions, deletions }
}

export const readWorkingTreeDiff = async (workspace: string): Promise<WorkingTreeDiffSummary> => {
  const resolvedWorkspace = workspace || process.cwd()
  let repoRoot = ""
  try {
    repoRoot = (await execGit(resolvedWorkspace, ["rev-parse", "--show-toplevel"])).trim()
  } catch {
    return {
      kind: "not-git",
      workspace: resolvedWorkspace,
      changedFiles: [],
      additions: 0,
      deletions: 0,
      patch: "",
      untrackedCount: 0,
      warnings: ["Workspace is not inside a git repository."],
    }
  }

  try {
    const [statusRaw, numstatRaw, patchRaw] = await Promise.all([
      execGit(repoRoot, ["status", "--porcelain=v1", "--untracked-files=normal"]),
      execGit(repoRoot, ["diff", "--no-ext-diff", "--numstat", "HEAD", "--"]),
      execGit(repoRoot, ["diff", "--no-ext-diff", "--binary", "HEAD", "--"], { maxBuffer: 20 * 1024 * 1024 }),
    ])
    const changedFiles = statusRaw
      .split(/\r?\n/)
      .map(parseStatusLine)
      .filter((item): item is WorkingTreeChangedFile => item != null)
    const { additions, deletions } = parseNumstat(numstatRaw)
    const untrackedCount = changedFiles.filter((file) => file.untracked).length
    const warnings: string[] = []
    if (untrackedCount > 0) {
      warnings.push(`${untrackedCount} untracked file${untrackedCount === 1 ? "" : "s"} listed; untracked file contents are not included in the patch preview.`)
    }
    if (!patchRaw.trim() && changedFiles.length > 0) {
      warnings.push("No tracked patch is available; changes may be untracked or binary-only.")
    }
    return {
      kind: changedFiles.length > 0 ? "dirty" : "clean",
      workspace: resolvedWorkspace,
      repoRoot,
      changedFiles,
      additions,
      deletions,
      patch: patchRaw.replace(/\r\n?/g, "\n").trimEnd(),
      untrackedCount,
      warnings,
    }
  } catch (error) {
    return {
      kind: "error",
      workspace: resolvedWorkspace,
      repoRoot,
      changedFiles: [],
      additions: 0,
      deletions: 0,
      patch: "",
      untrackedCount: 0,
      warnings: [],
      error: error instanceof Error ? error.message : String(error),
    }
  }
}

const isInside = (root: string, candidate: string): boolean => {
  const relative = path.relative(root, candidate)
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative))
}

export const exportWorkingTreePatch = async (
  workspace: string,
  targetPath?: string | null,
): Promise<WorkingTreePatchWriteResult> => {
  const diff = await readWorkingTreeDiff(workspace)
  if (diff.kind !== "dirty") {
    return { ok: false, error: `No dirty working-tree patch to export (state=${diff.kind}).` }
  }
  if (!diff.patch.trim()) {
    return { ok: false, error: "No tracked patch is available to export." }
  }
  const repoRoot = diff.repoRoot
  if (!repoRoot) return { ok: false, error: "Repository root unavailable." }
  const requested = targetPath?.trim() || ".breadboard/diff.patch"
  const absolute = path.resolve(repoRoot, requested)
  if (!isInside(repoRoot, absolute)) {
    return { ok: false, error: "Refusing to export patch outside the repository root." }
  }
  await fs.mkdir(path.dirname(absolute), { recursive: true })
  await fs.writeFile(absolute, `${diff.patch}\n`, "utf8")
  return { ok: true, path: absolute, bytes: Buffer.byteLength(`${diff.patch}\n`, "utf8") }
}

export const copyWorkingTreePatch = async (workspace: string): Promise<WorkingTreePatchCopyResult> => {
  const diff = await readWorkingTreeDiff(workspace)
  if (diff.kind !== "dirty") {
    return { ok: false, method: "none", error: `No dirty working-tree patch to copy (state=${diff.kind}).` }
  }
  if (!diff.patch.trim()) {
    return { ok: false, method: "none", error: "No tracked patch is available to copy." }
  }
  const result = await writeClipboardText(diff.patch)
  return {
    ok: result.ok,
    method: result.method,
    bytes: result.ok ? Buffer.byteLength(diff.patch, "utf8") : undefined,
    error: result.error,
  }
}
