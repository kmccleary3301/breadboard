import * as vscode from "vscode"

export const ENGINE_BASE_URL_KEY = "breadboardSidebar.engineBaseUrl"
export const DEFAULT_CONFIG_PATH_KEY = "breadboardSidebar.defaultConfigPath"
export const ENGINE_TOKEN_SECRET_KEY = "breadboardSidebar.engineToken"

export type SidebarConfig = {
  engineBaseUrl: string
  defaultConfigPath: string
}

export const readSidebarConfig = (): SidebarConfig => {
  const conf = vscode.workspace.getConfiguration()
  return {
    engineBaseUrl: conf.get<string>(ENGINE_BASE_URL_KEY, "http://127.0.0.1:9099"),
    defaultConfigPath: conf.get<string>(DEFAULT_CONFIG_PATH_KEY, "agent_configs/base_v2.yaml"),
  }
}

