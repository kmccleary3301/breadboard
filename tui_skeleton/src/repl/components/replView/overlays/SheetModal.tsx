import React from "react"
import { SelectPanel, type SelectPanelProps } from "../../SelectPanel.js"

type SheetModalProps = Omit<SelectPanelProps, "alignSelf" | "marginTop"> & {
  readonly sheetMode: boolean
}

export const SheetModal = ({ sheetMode, ...props }: SheetModalProps) => (
  <SelectPanel
    {...props}
    alignSelf={sheetMode ? "flex-start" : "center"}
    marginTop={sheetMode ? 0 : 2}
  />
)

