import React from "react"
import { ReplViewShell } from "./replView/ReplViewShell.js"
import { useReplViewController } from "./replView/controller/useReplViewController.js"
import type { ReplViewProps } from "./replView/replViewTypes.js"
import { DEFAULT_RESOLVED_TUI_CONFIG } from "../../tui_config/presets.js"
import { TuiConfigProvider } from "../../tui_config/context.js"
import { UIClockProvider } from "../clock/context.js"

const ReplViewInner: React.FC<ReplViewProps> = (props) => {
  const controller = useReplViewController(props)
  return <ReplViewShell controller={controller} />
}

export const ReplView: React.FC<ReplViewProps> = (props) => {
  return (
    <UIClockProvider>
      <TuiConfigProvider value={props.tuiConfig ?? DEFAULT_RESOLVED_TUI_CONFIG}>
        <ReplViewInner {...props} />
      </TuiConfigProvider>
    </UIClockProvider>
  )
}
