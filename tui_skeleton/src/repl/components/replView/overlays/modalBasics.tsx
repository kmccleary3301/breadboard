import React from "react"
import { SelectPanel, type SelectPanelLine, type SelectPanelRow } from "../../SelectPanel.js"
import type { ModalDescriptor } from "../../ModalHost.js"
import { CHALK, COLORS } from "../theme.js"

export const buildConfirmModal = (confirmState: any, panelWidth: number): ModalDescriptor | null => {
  if (confirmState.status !== "prompt") return null
  return {
    id: "confirm",
    render: () => {
      const titleLines: SelectPanelLine[] = [
        { text: confirmState.message ?? "Confirm action?", color: COLORS.warning },
      ]
      const hintLines: SelectPanelLine[] = [{ text: "Enter to confirm • Esc to cancel", color: "gray" }]
      return (
        <SelectPanel
          width={Math.min(80, panelWidth)}
          borderColor={COLORS.accent}
          paddingX={2}
          paddingY={1}
          titleLines={titleLines}
          hintLines={hintLines}
          rows={[]}
        />
      )
    },
  }
}

type ShortcutsModalParams = {
  shortcutsOpen: boolean
  claudeChrome: boolean
  isBreadboardProfile: boolean
  columnWidth: number
  panelWidth: number
  shortcutLines: Array<string>
}

export const buildShortcutsModal = ({
  shortcutsOpen,
  claudeChrome,
  isBreadboardProfile,
  columnWidth,
  panelWidth,
  shortcutLines,
}: ShortcutsModalParams): ModalDescriptor | null => {
  if (!shortcutsOpen || claudeChrome) return null
  return {
    id: "shortcuts",
    layout: isBreadboardProfile ? "sheet" : undefined,
    render: () => {
      const sheetMode = isBreadboardProfile
      const width = sheetMode ? columnWidth : panelWidth
      const titleLines: SelectPanelLine[] = [{ text: CHALK.bold("Shortcuts"), color: COLORS.info }]
      const hintLines: SelectPanelLine[] = [{ text: "Press ? or Esc to close", color: "dim" }]
      const rows: SelectPanelRow[] = shortcutLines.map((line: any) => ({
        kind: "item",
        text: line,
      }))
      return (
        <SelectPanel
          width={width}
          borderColor={COLORS.info}
          paddingX={2}
          paddingY={claudeChrome ? 0 : 1}
          alignSelf={sheetMode ? "flex-start" : "center"}
          marginTop={sheetMode ? 0 : 2}
          titleLines={titleLines}
          hintLines={hintLines}
          rows={rows}
        />
      )
    },
  }
}
