import React, { createContext, useContext, useMemo } from "react"
import type { UIClock } from "./UIClock.js"
import { DEFAULT_SYSTEM_CLOCK } from "./systemClock.js"
import { resolveUIClockFromEnv } from "./resolveClock.js"

const UIClockContext = createContext<UIClock>(DEFAULT_SYSTEM_CLOCK)

export const UIClockProvider: React.FC<{ readonly clock?: UIClock; readonly children: React.ReactNode }> = ({
  clock,
  children,
}) => {
  const resolvedClock = useMemo(() => clock ?? resolveUIClockFromEnv(), [clock])
  return <UIClockContext.Provider value={resolvedClock}>{children}</UIClockContext.Provider>
}

export const useUIClock = (): UIClock => useContext(UIClockContext)
