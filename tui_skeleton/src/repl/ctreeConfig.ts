import { loadProfileConfig } from "./profile.js"
import { loadUserConfigSync } from "../config/userConfig.js"

const parseBool = (value?: string): boolean => value === "1" || value === "true"

const hasValue = (value?: string): boolean => value !== undefined && value.trim().length > 0

export interface CTreeUiConfig {
  readonly enabled: boolean
  readonly showSummary: boolean
  readonly showTaskNode: boolean
}

export const loadCtreeConfig = (): CTreeUiConfig => {
  const profileEnabled = loadProfileConfig().name === "breadboard_v1"
  const userConfig = loadUserConfigSync()
  const userCtrees = userConfig.ctrees
  const envEnabled = hasValue(process.env.BREADBOARD_CTREES_ENABLED)
    ? parseBool(process.env.BREADBOARD_CTREES_ENABLED)
    : undefined
  const envSummary = hasValue(process.env.BREADBOARD_CTREES_SUMMARY)
    ? parseBool(process.env.BREADBOARD_CTREES_SUMMARY)
    : undefined
  const envTaskNode = hasValue(process.env.BREADBOARD_CTREES_TASK_NODE)
    ? parseBool(process.env.BREADBOARD_CTREES_TASK_NODE)
    : undefined
  const enabled =
    envEnabled ??
    (typeof userCtrees?.enabled === "boolean" ? userCtrees.enabled : undefined) ??
    profileEnabled
  const showSummary =
    envSummary ??
    (typeof userCtrees?.showSummary === "boolean" ? userCtrees.showSummary : undefined) ??
    enabled
  const showTaskNode =
    envTaskNode ??
    (typeof userCtrees?.showTaskNode === "boolean" ? userCtrees.showTaskNode : undefined) ??
    enabled
  return { enabled, showSummary, showTaskNode }
}
