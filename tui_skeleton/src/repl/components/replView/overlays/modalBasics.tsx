import React from "react"
import { SelectPanel, type SelectPanelLine, type SelectPanelRow } from "../../SelectPanel.js"
import type { ModalDescriptor } from "../../ModalHost.js"
import { CHALK, COLORS } from "../theme.js"
import { SheetModal } from "./SheetModal.js"

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

export const estimateSelectPanelRows = (input: {
  readonly rowCount: number
  readonly titleLineCount?: number
  readonly hintLineCount?: number
  readonly footerLineCount?: number
  readonly paddingY?: number
  readonly marginTop?: number
  readonly showBorder?: boolean
  readonly rowsMarginTop?: number
  readonly footerMarginTop?: number
}): number => {
  const titleLineCount = Math.max(0, Math.floor(input.titleLineCount ?? 0))
  const hintLineCount = Math.max(0, Math.floor(input.hintLineCount ?? 0))
  const footerLineCount = Math.max(0, Math.floor(input.footerLineCount ?? 0))
  const rowCount = Math.max(0, Math.floor(input.rowCount))
  let rows = Math.max(0, Math.floor(input.marginTop ?? 2))
  if (input.showBorder !== false) rows += 2
  rows += Math.max(0, Math.floor(input.paddingY ?? 1)) * 2
  rows += titleLineCount
  if (titleLineCount > 0 && hintLineCount > 0) rows += 1
  rows += hintLineCount
  if (rowCount > 0) rows += Math.max(0, Math.floor(input.rowsMarginTop ?? 1)) + rowCount
  if (footerLineCount > 0) rows += Math.max(0, Math.floor(input.footerMarginTop ?? 1)) + footerLineCount
  return rows
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
  const sheetMode = isBreadboardProfile
  const maxVisibleShortcutRows = sheetMode ? 10 : 12
  const visibleShortcutLines = shortcutLines.slice(0, maxVisibleShortcutRows)
  const hiddenShortcutCount = Math.max(0, shortcutLines.length - visibleShortcutLines.length)
  return {
    id: "shortcuts",
    layout: sheetMode ? "sheet" : undefined,
    estimatedRows: estimateSelectPanelRows({
      rowCount: visibleShortcutLines.length,
      titleLineCount: 1,
      hintLineCount: 1,
      footerLineCount: hiddenShortcutCount > 0 ? 1 : 0,
      paddingY: claudeChrome ? 0 : 1,
      marginTop: sheetMode ? 0 : 2,
    }),
    render: () => {
      const width = sheetMode ? columnWidth : panelWidth
      const titleLines: SelectPanelLine[] = [{ text: CHALK.bold("Shortcuts"), color: COLORS.info }]
      const hintLines: SelectPanelLine[] = [{ text: "Press ? or Esc to close", color: "dim" }]
      const rows: SelectPanelRow[] = visibleShortcutLines.map((line: any) => ({
        kind: "item",
        text: line,
      }))
      const footerLines: SelectPanelLine[] =
        hiddenShortcutCount > 0
          ? [{ text: `+${hiddenShortcutCount} more shortcuts are available from /help.`, color: "dim" }]
          : []
      return (
        <SheetModal
          sheetMode={sheetMode}
          width={width}
          borderColor={COLORS.info}
          paddingX={2}
          paddingY={claudeChrome ? 0 : 1}
          titleLines={titleLines}
          hintLines={hintLines}
          rows={rows}
          footerLines={footerLines}
        />
      )
    },
  }
}
