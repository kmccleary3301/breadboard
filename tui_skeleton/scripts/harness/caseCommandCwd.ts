import path from "node:path"

export interface CaseCommandCwdInput {
  readonly cwd?: string | null
}

export interface ResolveCaseCommandCwdOptions {
  readonly testCase: CaseCommandCwdInput
  readonly command: string
  readonly rootDir: string
  readonly repoRoot: string
  readonly dummyWorkspaceCwd: string
  readonly allowProjectCwd?: boolean
}

export const isInstalledBreadBoardReplCommand = (command: string): boolean => {
  return /^\s*bb\s+repl\b/.test(command) || /\bexec\s+bb\s+repl\b/.test(command)
}

export const resolveTuiRelativePath = (rootDir: string, value: string): string => {
  if (path.isAbsolute(value)) return value
  return path.resolve(rootDir, value)
}

export const resolveCaseCommandCwd = ({
  testCase,
  command,
  rootDir,
  repoRoot,
  dummyWorkspaceCwd,
  allowProjectCwd = false,
}: ResolveCaseCommandCwdOptions): string => {
  const requestedCwd = testCase.cwd ? resolveTuiRelativePath(rootDir, testCase.cwd) : rootDir
  const requestedRepoRoot = path.resolve(requestedCwd) === path.resolve(repoRoot)
  if (requestedRepoRoot && isInstalledBreadBoardReplCommand(command) && !allowProjectCwd) {
    return path.resolve(rootDir, dummyWorkspaceCwd)
  }
  return requestedCwd
}
