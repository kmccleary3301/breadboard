import React, { createContext, useContext } from "react"
import { DEFAULT_RESOLVED_TUI_CONFIG } from "./presets.js"
import type { ResolvedTuiConfig } from "./types.js"

const TuiConfigContext = createContext<ResolvedTuiConfig>(DEFAULT_RESOLVED_TUI_CONFIG)

export const TuiConfigProvider: React.FC<{ value: ResolvedTuiConfig; children: React.ReactNode }> = ({
  value,
  children,
}) => <TuiConfigContext.Provider value={value}>{children}</TuiConfigContext.Provider>

export const useTuiConfig = (): ResolvedTuiConfig => useContext(TuiConfigContext)

