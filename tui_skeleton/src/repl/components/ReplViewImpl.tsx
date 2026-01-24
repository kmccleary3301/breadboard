import React from "react"
import { ReplViewShell } from "./replView/ReplViewShell.js"
import { useReplViewController } from "./replView/controller/useReplViewController.js"
import type { ReplViewProps } from "./replView/replViewTypes.js"

export const ReplView: React.FC<ReplViewProps> = (props) => {
  const controller = useReplViewController(props)
  return <ReplViewShell controller={controller} />
}
