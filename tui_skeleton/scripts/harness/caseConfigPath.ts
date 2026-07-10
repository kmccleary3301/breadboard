export interface StressCaseConfigInput {
  readonly caseConfigPath?: string | null
  readonly requiresLive?: boolean
  readonly defaultConfigPath: string
  readonly liveConfigPath?: string | null
}

// Case-level configs are part of a scenario's contract. The live default only
// fills cases that do not pin their own config.
export const resolveStressCaseConfigPath = ({
  caseConfigPath,
  requiresLive = false,
  defaultConfigPath,
  liveConfigPath,
}: StressCaseConfigInput): string => {
  if (caseConfigPath) {
    return caseConfigPath
  }
  if (requiresLive && liveConfigPath) {
    return liveConfigPath
  }
  return defaultConfigPath
}
