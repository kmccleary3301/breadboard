import React from "react"
import { Box, Text } from "ink"
import { LineEditor } from "../../LineEditor.js"
import { SelectPanel, type SelectPanelLine, type SelectPanelRow } from "../../SelectPanel.js"
import { CHALK, COLORS, GLYPHS, uiText, ASCII_ONLY, DASH_GLYPH } from "../theme.js"
import { formatBytes, formatCell } from "../utils/format.js"
import { highlightFuzzyLabel } from "../utils/text.js"
import { RuntimePreviewStack } from "./RuntimePreviewStack.js"

// Intentionally broad while the controller continues to own most state.
type ComposerPanelContext = Record<string, any>

export const ComposerPanel: React.FC<{ context: ComposerPanelContext }> = ({ context }) => {
  const {
    claudeChrome,
    todosOpen,
    todos,
    todoRows,
    todoScroll,
    todoMaxScroll,
    todoViewportRows,
    modelMenu,
    pendingClaudeStatus,
    todoPreviewModel,
    promptRule,
    composerPromptPrefix,
    composerPlaceholderClassic,
    composerPlaceholderClaude,
    composerShowTopRule,
    composerShowBottomRule,
    input,
    cursor,
    inputLocked,
    attachments,
    fileMentions,
    inputMaxVisibleLines,
    handleLineEditGuarded,
    handleLineSubmit,
    handleAttachment,
    overlayActive,
    filePickerActive,
    fileIndexMeta,
    fileMenuMode,
    filePicker,
    fileMenuRows,
    fileMenuHasLarge,
    fileMenuWindow,
    fileMenuIndex,
    fileMenuNeedlePending,
    filePickerQueryParts,
    filePickerConfig,
    fileMentionConfig,
    selectedFileIsLarge,
    columnWidth,
    suggestions,
    suggestionWindow,
    suggestionPrefix,
    suggestionLayout,
    buildSuggestionLines,
    suggestIndex,
    activeSlashQuery,
    hintNodes,
    shortcutHintNodes,
  } = context

  if (claudeChrome && modelMenu.status !== "hidden") return null

  const ruleWidth = Number.isFinite(columnWidth)
    ? Math.max(1, Math.floor(columnWidth) - (claudeChrome ? 0 : 1))
    : Math.max(1, promptRule.length - (claudeChrome ? 0 : 1))
  const promptRuleLine =
    promptRule.length >= ruleWidth ? promptRule.slice(0, ruleWidth) : promptRule.padEnd(ruleWidth, " ")

  const showClaudePlaceholder =
    claudeChrome && input.length === 0 && attachments.length === 0 && fileMentions.length === 0

  if (claudeChrome && todosOpen) {
    const scroll = Math.max(0, Math.min(todoScroll ?? 0, todoMaxScroll ?? 0))
    const visibleRows = Array.isArray(todoRows) ? todoRows.slice(scroll, scroll + (todoViewportRows ?? 0)) : []
    const colorForStatus = (status?: string) => {
      switch (status) {
        case "in_progress":
          return COLORS.info
        case "done":
          return COLORS.success
        case "blocked":
          return COLORS.error
        case "canceled":
          return COLORS.muted
        default:
          return COLORS.warning
      }
    }
    const titleLines: SelectPanelLine[] = [{ text: CHALK.bold("Todos"), color: COLORS.info }]
    const hintLines: SelectPanelLine[] = [
      {
        text:
          (Array.isArray(todos) ? todos.length : 0) === 0
            ? "No todos yet."
            : `${Array.isArray(todos) ? todos.length : 0} item${Array.isArray(todos) && todos.length === 1 ? "" : "s"} • ↑/↓ scroll • PgUp/PgDn page • Esc close`,
        color: "gray",
      },
    ]
    const panelRows: SelectPanelRow[] = []
    if (!Array.isArray(todoRows) || todoRows.length === 0) {
      panelRows.push({ kind: "empty", text: "TodoWrite output will appear here once the agent updates the board.", color: "dim" })
    } else {
      visibleRows.forEach((row: any) => {
        if (row.kind === "header") {
          panelRows.push({ kind: "header", text: row.label, color: colorForStatus(row.status) })
        } else {
          panelRows.push({ kind: "item", text: `  • ${row.label}`, wrap: "truncate-end" })
        }
      })
      if (todoRows.length > (todoViewportRows ?? 0)) {
        panelRows.push({
          kind: "header",
          text: `${scroll + 1}-${Math.min(scroll + (todoViewportRows ?? 0), todoRows.length)} of ${todoRows.length}`,
          color: "dim",
        })
      }
    }

    return (
      <Box marginTop={0} flexDirection="column" width={ruleWidth}>
        {composerShowTopRule ? (
          <Text color={COLORS.textMuted} wrap="truncate">
            {promptRuleLine}
          </Text>
        ) : null}
        <SelectPanel
          width={ruleWidth}
          showBorder={false}
          paddingX={0}
          paddingY={0}
          marginTop={0}
          alignSelf="flex-start"
          rowsMarginTop={0}
          titleLines={titleLines}
          hintLines={hintLines}
          rows={panelRows}
        />
        {composerShowBottomRule ? (
          <Text color={COLORS.textMuted} wrap="truncate">
            {promptRuleLine}
          </Text>
        ) : null}
      </Box>
    )
  }

  return (
    <Box marginTop={claudeChrome ? 0 : 1} flexDirection="column" width={ruleWidth}>
      <RuntimePreviewStack
        claudeChrome={claudeChrome}
        overlayActive={overlayActive}
        pendingClaudeStatus={pendingClaudeStatus ?? null}
        todoPreviewModel={todoPreviewModel ?? null}
        hintNodes={!overlayActive ? hintNodes : []}
      />
      {claudeChrome && (
        composerShowTopRule ? (
          <Text color={COLORS.textMuted} wrap="truncate">
            {promptRuleLine}
          </Text>
        ) : null
      )}
      <Box minHeight={1} width={ruleWidth} flexDirection="row">
        <Text>
          <Text color={claudeChrome ? COLORS.textBright : "cyan"}>{`${composerPromptPrefix || GLYPHS.chevron} `}</Text>
        </Text>
        <LineEditor
          value={input}
          cursor={cursor}
          focus={!inputLocked}
          placeholder={
            showClaudePlaceholder
              ? (composerPlaceholderClaude || 'Try "refactor <filepath>"')
              : claudeChrome
                ? ""
                : (composerPlaceholderClassic || `Type your request${GLYPHS.ellipsis}`)
          }
          placeholderPad={!claudeChrome}
          hideCaretWhenPlaceholder={claudeChrome}
          maxVisibleLines={inputMaxVisibleLines}
          onChange={handleLineEditGuarded}
          onSubmit={handleLineSubmit}
          submitOnEnter
          onPasteAttachment={handleAttachment}
        />
      </Box>
      {claudeChrome && (
        composerShowBottomRule ? (
          <Text color={COLORS.textMuted} wrap="truncate">
            {promptRuleLine}
          </Text>
        ) : null
      )}
      {overlayActive ? (
        claudeChrome ? null : <Text color="dim">{uiText("Input locked — use the active modal controls.")}</Text>
      ) : filePickerActive ? (
        (() => {
          const titleLines: SelectPanelLine[] = []
          const hintLines: SelectPanelLine[] = []
          const rows: SelectPanelRow[] = []
          const footerLines: SelectPanelLine[] = []
          const indexingParts: string[] = []
          indexingParts.push(`${fileIndexMeta.fileCount} files`)
          if (fileIndexMeta.dirCount > 0) {
            indexingParts.push(`${fileIndexMeta.dirCount} dirs`)
          }
          const totalDirs = fileIndexMeta.scannedDirs + fileIndexMeta.queuedDirs
          if (totalDirs > 0) {
            indexingParts.push(`${fileIndexMeta.scannedDirs}/${totalDirs} dirs scanned`)
          }
          const indexingStatus = `Indexing${GLYPHS.ellipsis} (${indexingParts.join(ASCII_ONLY ? " . " : " · ")})`

          if (!claudeChrome) {
            titleLines.push({
              text: `Attach file ${CHALK.dim(filePickerQueryParts.cwd === "." ? "(root)" : filePickerQueryParts.cwd)}`,
              color: COLORS.info,
            })
            hintLines.push({
              text: uiText(
                `${fileMenuMode === "fuzzy" ? "Fuzzy search • " : ""}Type to filter • ↑/↓ navigate • PgUp/PgDn page • Tab/Enter complete • Esc clear${fileMenuHasLarge ? " • * large file" : ""}`,
              ),
              color: "gray",
            })
          }

          if (fileMenuMode === "tree" && (filePicker.status === "loading" || filePicker.status === "hidden")) {
            rows.push({ kind: "empty", text: `Loading${GLYPHS.ellipsis}`, color: "gray" })
          } else if (fileMenuMode === "tree" && filePicker.status === "error") {
            rows.push({
              kind: "empty",
              text: filePicker.message ? `Error: ${filePicker.message}` : "Error loading files.",
              color: COLORS.error,
            })
            rows.push({ kind: "empty", text: uiText("Tab to retry • Esc to clear"), color: "gray" })
          } else if (fileMenuMode === "fuzzy" && fileIndexMeta.status === "error" && fileMenuRows.length === 0) {
            rows.push({
              kind: "empty",
              text: fileIndexMeta.message ? `Error: ${fileIndexMeta.message}` : "Error indexing files.",
              color: COLORS.error,
            })
            rows.push({ kind: "empty", text: uiText("Tab to retry • Esc to clear"), color: "gray" })
          } else if (fileMenuRows.length === 0) {
            if (fileMenuMode === "fuzzy" && (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning")) {
              rows.push({ kind: "empty", text: indexingStatus, color: "dim" })
            } else {
              rows.push({ kind: "empty", text: "(no matches)", color: "dim" })
            }
          } else {
            const contentWidth = Math.max(10, columnWidth - 4)
            const windowItems = fileMenuWindow.items
            const hiddenAbove = fileMenuWindow.hiddenAbove
            const hiddenBelow = fileMenuWindow.hiddenBelow
            if (fileMenuMode === "fuzzy" && (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning")) {
              rows.push({ kind: "header", text: indexingStatus, color: "dim" })
            }
            if (fileMenuMode === "fuzzy" && fileMenuNeedlePending) {
              rows.push({ kind: "header", text: `Searching${GLYPHS.ellipsis}`, color: "dim" })
            }
            if (fileMenuMode === "fuzzy" && fileIndexMeta.truncated) {
              rows.push({ kind: "header", text: `Index truncated at ${fileIndexMeta.fileCount} files.`, color: "dim" })
            }
            if (hiddenAbove > 0) {
              rows.push({ kind: "header", text: uiText(`↑ ${hiddenAbove} more${GLYPHS.ellipsis}`), color: "dim" })
            }
            windowItems.forEach((row: any, index: number) => {
              const absoluteIndex = fileMenuWindow.start + index
              const selected = absoluteIndex === fileMenuIndex
              if (row.kind === "resource") {
                rows.push({
                  kind: "item",
                  text: formatCell(row.resource.label, contentWidth, "left"),
                  secondaryText: row.resource.detail ? `  ${row.resource.detail}` : undefined,
                  isActive: selected,
                  color: "white",
                  secondaryColor: "dim",
                  activeColor: COLORS.selectionFg,
                  secondaryActiveColor: COLORS.selectionFg,
                  activeBackground: COLORS.selectionBg,
                })
                return
              }
              const display = row.item.path
              const suffix = row.item.type === "directory" ? "/" : ""
              const sizeBytes = row.item.size
              const isLarge = sizeBytes != null && sizeBytes > fileMentionConfig.maxInlineBytesPerFile
              const sizeLabel = sizeBytes != null ? formatBytes(sizeBytes) : ""
              const sizeToken = sizeLabel ? `${sizeLabel}${isLarge ? "*" : ""}` : ""
              const leftWidth = sizeToken ? Math.max(0, contentWidth - sizeToken.length - 1) : contentWidth
              const rightWidth = Math.max(0, contentWidth - leftWidth)
              const leftLabel = formatCell(`${display}${suffix}`, leftWidth, "left")
              const rightLabel = sizeToken ? formatCell(sizeToken, rightWidth, "right") : ""
              const label = sizeToken ? `${leftLabel}${rightLabel}` : leftLabel
              rows.push({
                kind: "item",
                text: label,
                isActive: selected,
                color: row.item.type === "directory" ? "cyan" : "white",
                activeColor: COLORS.selectionFg,
                activeBackground: COLORS.selectionBg,
              })
            })
            if (hiddenBelow > 0) {
              rows.push({ kind: "header", text: uiText(`↓ ${hiddenBelow} more${GLYPHS.ellipsis}`), color: "dim" })
            }
          }

          if (selectedFileIsLarge) {
            footerLines.push({
              text: uiText("Large file selected — will attach a snippet unless explicitly forced to inline."),
              color: "dim",
            })
          }

          return (
            <SelectPanel
              width={columnWidth}
              showBorder={false}
              paddingX={0}
              paddingY={0}
              marginTop={claudeChrome ? 0 : 1}
              alignSelf="flex-start"
              titleLines={titleLines}
              hintLines={hintLines}
              rows={rows}
              footerLines={footerLines}
            />
          )
        })()
      ) : suggestions.length > 0 ? (
        <Box marginTop={claudeChrome ? 0 : 1} flexDirection="column">
          {suggestionWindow.hiddenAbove > 0 && (
            <Text color="dim">
              {uiText(`${suggestionPrefix}↑ ${suggestionWindow.hiddenAbove} more${GLYPHS.ellipsis}`)}
            </Text>
          )}
          {suggestionWindow.items.map((row: any, index: number) => {
            const globalIndex = suggestionWindow.start + index
            const selected = globalIndex === suggestIndex
            const lines = buildSuggestionLines(row, claudeChrome)
            return lines.map((line: any, lineIndex: number) => {
              const left = formatCell(line.label, suggestionLayout.commandWidth)
              const right = formatCell(line.summary, suggestionLayout.summaryWidth)
              if (claudeChrome) {
                const rendered = `${suggestionPrefix}${left}  ${right}`
                return (
                  <Text
                    key={`suggestion-${globalIndex}-${lineIndex}`}
                    color={selected ? COLORS.info : "gray"}
                    wrap="wrap"
                  >
                    {rendered}
                  </Text>
                )
              }
              const styledLeft = selected ? left : highlightFuzzyLabel(left, row.command, activeSlashQuery)
              const styledRight = selected ? right : CHALK.dim(right)
              const rendered = `${styledLeft}  ${styledRight}`
              return (
                <Text key={`suggestion-${globalIndex}-${lineIndex}`} inverse={selected} wrap="truncate-end">
                  {rendered}
                </Text>
              )
            })
          })}
          {suggestionWindow.hiddenBelow > 0 && (
            <Text color="dim">
              {uiText(`${suggestionPrefix}↓ ${suggestionWindow.hiddenBelow} more${GLYPHS.ellipsis}`)}
            </Text>
          )}
        </Box>
      ) : claudeChrome ? null : (
        <Text color="dim">{" "}</Text>
      )}
      {!overlayActive && claudeChrome && shortcutHintNodes.length > 0 && (
        <Box flexDirection="column">
          {shortcutHintNodes}
        </Box>
      )}
      {!overlayActive && !claudeChrome && hintNodes.length > 0 && (
        <Box marginTop={claudeChrome ? 0 : 1} flexDirection="column">
          {hintNodes}
        </Box>
      )}
      {!overlayActive && attachments.length > 0 && (
        <Box marginTop={1} flexDirection="column">
          <Text color={COLORS.accent}>Attachments queued ({attachments.length})</Text>
          {attachments.map((attachment: any) => (
            <Text key={attachment.id} color="gray">
              {GLYPHS.bullet} {attachment.mime} {DASH_GLYPH} {formatBytes(attachment.size)}
            </Text>
          ))}
          <Text color={COLORS.warning}>
            Attachments upload automatically when you submit; they remain queued here until then.
          </Text>
          <Text color="dim">Backspace removes the most recent attachment when the input is empty.</Text>
        </Box>
      )}
      {!overlayActive && fileMentions.length > 0 && (
        <Box marginTop={1} flexDirection="column">
          <Text color={COLORS.info}>Files queued ({fileMentions.length})</Text>
          {fileMentions.map((entry: any) => (
            <Text key={entry.id} color="gray" wrap="truncate-end">
              {GLYPHS.bullet} {entry.path}
              {entry.size != null ? ` ${DASH_GLYPH} ${formatBytes(entry.size)}` : ""}
              {entry.requestedMode !== "auto" ? ` ${DASH_GLYPH} ${entry.requestedMode}` : ""}
            </Text>
          ))}
          <Text color={COLORS.warning}>Files are attached as context on submit; oversized files are truncated.</Text>
        </Box>
      )}
    </Box>
  )
}
