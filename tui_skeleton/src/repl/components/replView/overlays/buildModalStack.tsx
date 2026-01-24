import React from "react"
import { SelectPanel, type SelectPanelLine, type SelectPanelRow } from "../../SelectPanel.js"
import type { ModalDescriptor } from "../../ModalHost.js"
import { CHALK, COLORS, DOT_SEPARATOR, GLYPHS, DASH_GLYPH, ASCII_ONLY, uiText } from "../theme.js"
import { formatCell, formatCostUsd, formatDuration, formatLatency } from "../utils/format.js"
import { formatIsoTimestamp } from "../utils/diff.js"
import { stripAnsiCodes } from "../utils/ansi.js"
import type { SlashCommandInfo } from "../../../slashCommands.js"
import type { PermissionRuleScope } from "../../../types.js"
import { buildConfirmModal, buildShortcutsModal } from "./modalBasics.js"
import type { SlashCommandInfo } from "../../../slashCommands.js"
import type { PermissionRuleScope } from "../../../types.js"
import { buildConfirmModal, buildShortcutsModal } from "./modalBasics.js"

// Intentionally broad to keep modal composition decoupled from controller internals.
type ModalStackContext = Record<string, any>

export const buildModalStack = (context: ModalStackContext): ModalDescriptor[] => {
  const {
    confirmState,
    shortcutsOpen,
    claudeChrome,
    isBreadboardProfile,
    columnWidth,
    PANEL_WIDTH,
    shortcutLines,
    paletteState,
    paletteItems,
    MAX_VISIBLE_MODELS,
    clearToEnd,
    modelMenu,
    modelMenuCompact,
    modelSearch,
    modelProviderLabel,
    modelProviderFilter,
    stats,
    filteredModels,
    modelMenuHeaderText,
    visibleModelRows,
    modelIndex,
    formatModelRowText,
    modelOffset,
    MODEL_VISIBLE_ROWS,
    skillsMenu,
    skillsSelected,
    skillsMode,
    skillsSearch,
    skillsIndex,
    skillsDisplayRows,
    skillsOffset,
    SKILLS_VISIBLE_ROWS,
    skillsSources,
    isSkillSelected,
    skillsDirty,
    rewindMenu,
    rewindCheckpoints,
    rewindVisible,
    rewindOffset,
    rewindSelectedIndex,
    rewindVisibleLimit,
    todosOpen,
    todoScroll,
    todoMaxScroll,
    todoRows,
    todoViewportRows,
    todos,
    usageOpen,
    contentWidth,
    inspectMenu,
    inspectRawOpen,
    inspectRawScroll,
    inspectRawMaxScroll,
    inspectRawViewportRows,
    inspectRawLines,
    sessionId,
    status,
    mode,
    pendingResponse,
    tasks,
    ctreeOpen,
    ctreeScroll,
    ctreeMaxScroll,
    ctreeRows,
    ctreeViewportRows,
    ctreeStage,
    ctreeIncludePreviews,
    ctreeSource,
    ctreeTree,
    ctreeTreeStatus,
    ctreeTreeError,
    ctreeUpdatedAt,
    ctreeCollapsedNodes,
    selectedCTreeIndex,
    selectedCTreeRow,
    ctreeShowDetails,
    formatCTreeNodeLabel,
    formatCTreeNodePreview,
    formatCTreeNodeFlags,
    tasksOpen,
    taskScroll,
    taskMaxScroll,
    taskRows,
    taskViewportRows,
    taskSearchQuery,
    taskStatusFilter,
    selectedTaskIndex,
    selectedTask,
    taskNotice,
    taskTailLines,
    taskTailPath,
    formatCtreeSummary,
    ctreeSnapshot,
    permissionRequest,
    permissionQueueDepth,
    permissionTab,
    permissionScope,
    permissionDiffLines,
    permissionViewportRows,
    permissionDiffSections,
    permissionDiffPreview,
    permissionSelectedSection,
    permissionSelectedFileIndex,
    permissionScroll,
    permissionError,
    permissionNote,
    permissionNoteCursor,
    renderPermissionNoteLine,
  } = context

  const modalStack: ModalDescriptor[] = []

  const confirmModal = buildConfirmModal(confirmState, PANEL_WIDTH)
  if (confirmModal) modalStack.push(confirmModal)

  const shortcutsModal = buildShortcutsModal({
    shortcutsOpen,
    claudeChrome,
    isBreadboardProfile,
    columnWidth,
    panelWidth: PANEL_WIDTH,
    shortcutLines,
  })
  if (shortcutsModal) modalStack.push(shortcutsModal)

  if (paletteState.status === "open") {
    modalStack.push({
      id: "palette",
      layout: isBreadboardProfile ? "sheet" : undefined,
      render: () => {
        const sheetMode = isBreadboardProfile
        const panelWidth = sheetMode ? columnWidth : PANEL_WIDTH
        const titleLines: SelectPanelLine[] = [{ text: "Command palette", color: COLORS.info }]
        const hintLines: SelectPanelLine[] = [
          {
            text: `Search: ${paletteState.query.length > 0 ? paletteState.query : CHALK.dim("<type to filter>")}`,
            color: "dim",
          },
        ]
        const rows: SelectPanelRow[] = []
        if (paletteItems.length === 0) {
          rows.push({ kind: "empty", text: "No commands match.", color: "dim" })
        } else {
          paletteItems.slice(0, MAX_VISIBLE_MODELS).forEach((item: SlashCommandInfo, idx: number) => {
            const isActive = idx === Math.min(paletteState.index, paletteItems.length - 1)
            const label = `/${item.name}${item.usage ? ` ${item.usage}` : ""} — ${item.summary}`
            rows.push({
              kind: "item",
              text: `${isActive ? "›" : " "} ${label}`,
              isActive,
              activeColor: COLORS.selectionFg,
              activeBackground: COLORS.info,
            })
          })
        }
        return (
          <SelectPanel
            width={panelWidth}
            borderColor={COLORS.info}
            paddingX={2}
            paddingY={1}
            alignSelf={sheetMode ? "flex-start" : "center"}
            marginTop={sheetMode ? 0 : 2}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
          />
        )
      },
    })
  }

  if (modelMenu.status !== "hidden") {
    modalStack.push({
      id: "model-picker",
      layout: isBreadboardProfile ? "sheet" : undefined,
      render: () => {
        const sheetMode = isBreadboardProfile
        const panelWidth = sheetMode ? columnWidth : PANEL_WIDTH
        const panelInnerWidth = Math.max(0, panelWidth - 4)
        const titleLines: SelectPanelLine[] = []
        const hintLines: SelectPanelLine[] = []
        const rows: SelectPanelRow[] = []
        const footerLines: SelectPanelLine[] = []

        if (modelMenu.status === "loading") {
          rows.push({ kind: "empty", text: "Loading model catalog…", color: "cyan" })
        } else if (modelMenu.status === "error") {
          rows.push({ kind: "empty", text: modelMenu.message, color: "red" })
        } else if (modelMenu.status === "ready") {
          if (claudeChrome) {
            titleLines.push({ text: clearToEnd(" ") })
            titleLines.push({
              text: clearToEnd(formatCell("Select model — Switch between models.", panelInnerWidth, "left")),
              color: "dim",
            })
            titleLines.push({
              text: clearToEnd(formatCell("Use --model for other/previous names.", panelInnerWidth, "left")),
              color: "dim",
            })
            titleLines.push({
              text: clearToEnd(
                formatCell(
                  `Provider: ${modelProviderLabel} (←/→ filter${modelProviderFilter ? " · Backspace clear" : ""})${modelSearch.trim().length > 0 ? ` • Filter: ${modelSearch.trim()}` : ""}`,
                  panelInnerWidth,
                  "left",
                ),
              ),
              color: "dim",
            })
            titleLines.push({
              text: clearToEnd(formatCell("Enter to confirm · Esc to exit", panelInnerWidth, "left")),
              color: "dim",
            })
          } else {
            titleLines.push({
              text: modelMenuCompact ? "Select a model" : "Select a model (Enter to confirm, Esc to cancel)",
              color: "green",
            })
            hintLines.push({
              text: `Search: ${modelSearch.length > 0 ? modelSearch : CHALK.dim("<type to filter>")}`,
              color: "dim",
            })
            hintLines.push({
              text: `Provider: ${modelProviderLabel}${modelProviderFilter ? " (←/→ change · Backspace clear)" : " (←/→ filter)"}`,
              color: "dim",
            })
            if (!modelMenuCompact) {
              hintLines.push({ text: `Current: ${CHALK.cyan(stats.model)}`, color: "dim" })
            }
            hintLines.push({
              text: modelMenuCompact
                ? "Nav: ↑/↓ PgUp/PgDn • Enter confirm • Esc cancel"
                : "Navigate: ↑/↓ or Tab • Shift+Tab up • PgUp/PgDn page • Legend: ● current · ★ default",
              color: "dim",
            })
            if (modelMenuCompact) {
              hintLines.push({ text: "Legend: ● current · ★ default", color: "dim" })
            }
          }

          if (filteredModels.length === 0) {
            rows.push({ kind: "empty", text: "No models match.", color: "dim" })
          } else {
            rows.push({ kind: "header", text: modelMenuHeaderText, color: "gray" })
            visibleModelRows.forEach((row: any, idx: number) => {
              if (row.kind === "header") {
                const countSuffix = row.count != null ? ` (${row.count})` : ""
                rows.push({ kind: "header", text: `${row.label}${countSuffix}`, color: "dim" })
                return
              }
              const isActive = row.index === modelIndex
              const rowText = formatModelRowText(row.item)
              rows.push({
                kind: "item",
                text: `${isActive ? "› " : "  "}${rowText}`,
                isActive,
                activeColor: COLORS.selectionFg,
                activeBackground: COLORS.info,
              })
            })
          }

          if (filteredModels.length > MODEL_VISIBLE_ROWS) {
            footerLines.push({
              text: `${modelOffset + 1}-${Math.min(modelOffset + MODEL_VISIBLE_ROWS, filteredModels.length)} of ${filteredModels.length}`,
              color: "dim",
            })
          }
        }

        return (
          <SelectPanel
            width={panelWidth}
            borderColor={COLORS.info}
            paddingX={2}
            paddingY={1}
            alignSelf={sheetMode ? "flex-start" : "center"}
            marginTop={sheetMode ? 0 : 2}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
            footerLines={footerLines}
          />
        )
      },
    })
  }

  if (skillsMenu.status !== "hidden") {
    modalStack.push({
      id: "skills-picker",
      layout: isBreadboardProfile ? "sheet" : undefined,
      render: () => {
        const titleLines: SelectPanelLine[] = [{ text: CHALK.bold("Skills"), color: COLORS.info }]
        const hintLines: SelectPanelLine[] = []
        const rows: SelectPanelRow[] = []
        const footerLines: SelectPanelLine[] = []
        const sheetMode = isBreadboardProfile
        const panelWidth = sheetMode ? columnWidth : PANEL_WIDTH

        if (skillsMenu.status === "loading") {
          rows.push({ kind: "empty", text: "Loading skills catalog…", color: "cyan" })
        } else if (skillsMenu.status === "error") {
          rows.push({ kind: "empty", text: skillsMenu.message, color: "red" })
        } else if (skillsMenu.status === "ready") {
          const selectionCount = skillsSelected.size
          const modeLabel = skillsMode === "allowlist" ? "allowlist" : "blocklist"
          hintLines.push({
            text: `Mode: ${modeLabel} (${skillsMode === "allowlist" ? "selected = enabled" : "selected = disabled"})`,
            color: "dim",
          })
          hintLines.push({
            text: `Search: ${skillsSearch.length > 0 ? skillsSearch : CHALK.dim("<type to filter>")} • Selected: ${selectionCount}`,
            color: "dim",
          })
          if (skillsSources?.config_path) {
            hintLines.push({ text: `Config: ${skillsSources.config_path}`, color: "dim" })
          }
          if (skillsDisplayRows.length === 0) {
            rows.push({ kind: "empty", text: "No skills match.", color: "dim" })
          } else {
            const start = Math.max(0, Math.min(skillsOffset, Math.max(0, skillsDisplayRows.length - SKILLS_VISIBLE_ROWS)))
            const windowRows = skillsDisplayRows.slice(start, start + SKILLS_VISIBLE_ROWS)
            windowRows.forEach((row: any) => {
              if (row.kind === "header") {
                rows.push({ kind: "header", text: row.label, color: "dim" })
                return
              }
              const entry = row.entry
              const selected = isSkillSelected(skillsSelected, entry)
              const blocked = skillsMode === "blocklist" && selected
              const disabled = skillsMode === "allowlist" && !selected
              const marker = selected
                ? CHALK.hex(blocked ? COLORS.error : COLORS.success)("[x]")
                : CHALK.dim("[ ]")
              const label = entry.label ?? entry.id
              const versionLabel = entry.version ? `@${entry.version}` : ""
              const line = `${marker} ${label}${versionLabel ? CHALK.dim(` ${versionLabel}`) : ""}`
              const metaParts: string[] = []
              if (entry.type) metaParts.push(entry.type)
              if (entry.group) metaParts.push(entry.group)
              if (entry.slot) metaParts.push(entry.slot)
              if (entry.steps != null) metaParts.push(`${entry.steps} steps`)
              if (entry.determinism) metaParts.push(entry.determinism)
              const detail = metaParts.length > 0 ? metaParts.join(" · ") : entry.description ?? ""
              const isActive = row.index === skillsIndex
              rows.push({
                kind: "item",
                text: `${isActive ? "› " : "  "}${line}`,
                secondaryText: detail ? CHALK.dim(detail) : undefined,
                isActive,
                color: disabled || blocked ? "gray" : "white",
                activeColor: COLORS.selectionFg,
                secondaryColor: "dim",
                secondaryActiveColor: COLORS.selectionFg,
                activeBackground: COLORS.info,
              })
            })
          }
          footerLines.push({
            text: "Space toggle • M mode • R reset • Enter apply • Esc cancel",
            color: "gray",
          })
          if (skillsDirty) {
            footerLines.push({ text: "Unsaved changes", color: COLORS.warning })
          }
        }

        return (
          <SelectPanel
            width={panelWidth}
            borderColor={COLORS.info}
            paddingX={2}
            paddingY={1}
            alignSelf={sheetMode ? "flex-start" : "center"}
            marginTop={sheetMode ? 0 : 2}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
            footerLines={footerLines}
          />
        )
      },
    })
  }

  if (rewindMenu.status !== "hidden") {
    modalStack.push({
      id: "rewind",
      layout: isBreadboardProfile ? "sheet" : undefined,
      render: () => {
        const sheetMode = isBreadboardProfile
        const panelWidth = sheetMode ? columnWidth : PANEL_WIDTH
        const isLoading = rewindMenu.status === "loading"
        const isError = rewindMenu.status === "error"
        const titleLines: SelectPanelLine[] = [{ text: CHALK.bold("Rewind checkpoints"), color: COLORS.info }]
        const hintLines: SelectPanelLine[] = [
          { text: "↑/↓ select • PgUp/PgDn page • 1 convo • 2 code • 3 both • Esc close", color: "gray" },
        ]
        const rows: SelectPanelRow[] = []
        const footerLines: SelectPanelLine[] = [
          {
            text: "Restores conversation and tracked files; external shell changes aren't tracked. Code-only restores do not prune history.",
            color: "dim",
          },
        ]

        if (isLoading) {
          rows.push({ kind: "empty", text: "Loading checkpoints…", color: "cyan" })
        }
        if (isError) {
          rows.push({ kind: "empty", text: rewindMenu.message, color: "red" })
        }
        if (!isLoading && !isError) {
          if (rewindCheckpoints.length === 0) {
            rows.push({
              kind: "empty",
              text: "No checkpoints yet. (/rewind again after the next tool run.)",
              color: "dim",
            })
          } else {
            rewindVisible.forEach((entry: any, idx: number) => {
              const listIndex = rewindOffset + idx
              const isActive = listIndex === rewindSelectedIndex
              const statsParts: string[] = []
              if (entry.trackedFiles != null) statsParts.push(`${entry.trackedFiles} files`)
              if (entry.additions != null || entry.deletions != null) {
                statsParts.push(`+${entry.additions ?? 0} -${entry.deletions ?? 0}`)
              }
              if (entry.hasUntrackedChanges) statsParts.push("untracked")
              const meta = [formatIsoTimestamp(entry.createdAt), ...statsParts].filter(Boolean).join(" · ")
              const label = `${entry.preview}`
              rows.push({
                kind: "item",
                text: `${isActive ? "› " : "  "}${label}`,
                secondaryText: `${isActive ? "  " : "  "}${CHALK.dim(meta || entry.checkpointId)}`,
                isActive,
                color: undefined,
                secondaryColor: "dim",
                activeColor: COLORS.selectionFg,
                secondaryActiveColor: COLORS.selectionFg,
                activeBackground: COLORS.info,
              })
            })
          }
        }
        if (rewindCheckpoints.length > rewindVisibleLimit) {
          rows.push({
            kind: "header",
            text: `${rewindOffset + 1}-${Math.min(rewindOffset + rewindVisibleLimit, rewindCheckpoints.length)} of ${rewindCheckpoints.length}`,
            color: "dim",
          })
        }

        return (
          <SelectPanel
            width={panelWidth}
            borderColor={COLORS.info}
            paddingX={2}
            paddingY={1}
            alignSelf={sheetMode ? "flex-start" : "center"}
            marginTop={sheetMode ? 0 : 2}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
            footerLines={footerLines}
          />
        )
      },
    })
  }

  if (todosOpen) {
    modalStack.push({
      id: "todos",
      layout: isBreadboardProfile ? "sheet" : undefined,
      render: () => {
        const sheetMode = isBreadboardProfile
        const panelWidth = sheetMode ? columnWidth : PANEL_WIDTH
        const scroll = Math.max(0, Math.min(todoScroll, todoMaxScroll))
        const visible = todoRows.slice(scroll, scroll + todoViewportRows)
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
              todos.length === 0
                ? "No todos yet."
                : `${todos.length} item${todos.length === 1 ? "" : "s"} • ↑/↓ scroll • PgUp/PgDn page • Esc close`,
            color: "gray",
          },
        ]
        const panelRows: SelectPanelRow[] = []
        if (todoRows.length === 0) {
          panelRows.push({ kind: "empty", text: "TodoWrite output will appear here once the agent updates the board.", color: "dim" })
        } else {
          visible.forEach((row: any, idx: number) => {
            if (row.kind === "header") {
              panelRows.push({ kind: "header", text: row.label, color: colorForStatus(row.status) })
            } else {
              panelRows.push({ kind: "item", text: `  • ${row.label}`, wrap: "truncate-end" })
            }
          })
          if (todoRows.length > todoViewportRows) {
            panelRows.push({
              kind: "header",
              text: `${scroll + 1}-${Math.min(scroll + todoViewportRows, todoRows.length)} of ${todoRows.length}`,
              color: "dim",
            })
          }
        }
        return (
          <SelectPanel
            width={panelWidth}
            borderColor={COLORS.info}
            paddingX={2}
            paddingY={1}
            alignSelf={sheetMode ? "flex-start" : "center"}
            marginTop={sheetMode ? 0 : 2}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={panelRows}
          />
        )
      },
    })
  }

  if (usageOpen) {
    modalStack.push({
      id: "usage",
      layout: isBreadboardProfile ? "sheet" : undefined,
      render: () => {
        const sheetMode = isBreadboardProfile
        const panelWidth = sheetMode ? columnWidth : Math.min(PANEL_WIDTH, contentWidth + 2)
        const usage = stats.usage
        const rows: Array<{ label: string; value: string }> = []
        if (usage?.promptTokens != null) rows.push({ label: "Prompt tokens", value: `${Math.round(usage.promptTokens)}` })
        if (usage?.completionTokens != null)
          rows.push({ label: "Completion tokens", value: `${Math.round(usage.completionTokens)}` })
        if (usage?.totalTokens != null) rows.push({ label: "Total tokens", value: `${Math.round(usage.totalTokens)}` })
        if (usage?.cacheReadTokens != null)
          rows.push({ label: "Cache read tokens", value: `${Math.round(usage.cacheReadTokens)}` })
        if (usage?.cacheWriteTokens != null)
          rows.push({ label: "Cache write tokens", value: `${Math.round(usage.cacheWriteTokens)}` })
        if (usage?.costUsd != null) rows.push({ label: "Cost", value: formatCostUsd(usage.costUsd) })
        if (usage?.latencyMs != null) rows.push({ label: "Latency", value: formatLatency(usage.latencyMs) })
        const titleLines: SelectPanelLine[] = [{ text: CHALK.bold("Usage"), color: COLORS.success }]
        const hintLines: SelectPanelLine[] = [{ text: "Esc close", color: "dim" }]
        const panelRows: SelectPanelRow[] = []
        if (rows.length === 0) {
          panelRows.push({ kind: "empty", text: "No usage metrics reported yet.", color: "dim" })
        } else {
          rows.forEach((row: any) => {
            panelRows.push({
              kind: "item",
              text: `${CHALK.cyan(row.label.padEnd(18, " "))} ${row.value}`,
            })
          })
        }
        return (
          <SelectPanel
            width={panelWidth}
            borderColor={COLORS.success}
            paddingX={2}
            paddingY={1}
            alignSelf={sheetMode ? "flex-start" : "center"}
            marginTop={sheetMode ? 0 : 2}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={panelRows}
          />
        )
      },
    })
  }

  if (inspectMenu.status !== "hidden") {
    modalStack.push({
      id: "inspect",
      layout: isBreadboardProfile ? "sheet" : undefined,
      render: () => {
        const sheetMode = isBreadboardProfile
        const panelWidth = sheetMode ? columnWidth : Math.min(PANEL_WIDTH, contentWidth + 2)
        const titleLines: SelectPanelLine[] = [{ text: CHALK.bold("Inspect"), color: COLORS.accent }]
        const hintLines: SelectPanelLine[] = [
          {
            text: inspectRawOpen ? "J summary • ↑↓ scroll • R refresh • Esc close" : "J raw • R refresh • Esc close",
            color: "dim",
          },
        ]
        const rows: SelectPanelRow[] = []
        const footerLines: SelectPanelLine[] = []

        const isRecord = (value: unknown): value is Record<string, unknown> =>
          typeof value === "object" && value !== null && !Array.isArray(value)

        const pushKV = (label: string, value: unknown, color: string = "white") => {
          const textValue = value == null ? "—" : typeof value === "string" ? value : JSON.stringify(value)
          rows.push({ kind: "item", text: `${CHALK.cyan(label.padEnd(18, " "))} ${textValue}`, color })
        }

        if (inspectMenu.status === "loading") {
          rows.push({ kind: "empty", text: "Loading inspector snapshot…", color: "cyan" })
        } else if (inspectMenu.status === "error") {
          rows.push({ kind: "empty", text: inspectMenu.message, color: "red" })
        } else if (inspectMenu.status === "ready") {
          if (inspectRawOpen) {
            rows.push({ kind: "header", text: "Raw JSON", color: "dim" })
            const scroll = Math.max(0, Math.min(inspectRawScroll, inspectRawMaxScroll))
            const visible = inspectRawLines.slice(scroll, scroll + inspectRawViewportRows)
            visible.forEach((line: any) => {
              rows.push({ kind: "item", text: line, color: "gray" })
            })
            if (inspectRawLines.length > inspectRawViewportRows) {
              footerLines.push({
                text: `${scroll + 1}-${Math.min(scroll + inspectRawViewportRows, inspectRawLines.length)} of ${inspectRawLines.length}`,
                color: "dim",
              })
            }
          } else {
            const session = inspectMenu.session
            const skillsPayload = inspectMenu.skills

            rows.push({ kind: "header", text: "Session", color: "dim" })
            if (isRecord(session)) {
              pushKV("ID", sessionId, "gray")
              pushKV("Status", session.status, "white")
              pushKV("Model", session.model ?? stats.model, "white")
              pushKV("Mode", session.mode ?? mode ?? "—", "white")
              pushKV("Created", session.created_at ?? session.createdAt, "gray")
              pushKV("Last activity", session.last_activity_at ?? session.lastActivityAt, "gray")
              if (session.logging_dir ?? session.loggingDir) {
                pushKV("Logging", session.logging_dir ?? session.loggingDir, "gray")
              }
              const meta = isRecord(session.metadata) ? session.metadata : null
              if (meta) {
                rows.push({ kind: "header", text: "Metadata", color: "dim" })
                pushKV("Keys", Object.keys(meta).length, "gray")
                const pluginSnapshot = meta.plugin_snapshot ?? meta.plugins ?? null
                const mcpSnapshot = meta.mcp_snapshot ?? meta.mcp ?? null
                if (pluginSnapshot) pushKV("Plugin snapshot", typeof pluginSnapshot, "gray")
                if (mcpSnapshot) pushKV("MCP snapshot", typeof mcpSnapshot, "gray")
              }
            } else {
              pushKV("ID", sessionId, "gray")
              pushKV("Status", status, "white")
              pushKV("Model", stats.model, "white")
            }

            if (isRecord(skillsPayload)) {
              const sources = isRecord(skillsPayload.sources) ? skillsPayload.sources : null
              const catalog = isRecord(skillsPayload.catalog) ? skillsPayload.catalog : null
              const catalogSkills = catalog && Array.isArray(catalog.skills) ? catalog.skills : []
              rows.push({ kind: "header", text: "Skills", color: "dim" })
              pushKV("Count", catalogSkills.length, "gray")
              const selection = isRecord(skillsPayload.selection) ? skillsPayload.selection : null
              if (selection?.mode) {
                pushKV("Selection", selection.mode, "gray")
              }
              if (sources) {
                if (sources.config_path) pushKV("Config", sources.config_path, "gray")
                if (sources.workspace) pushKV("Workspace", sources.workspace, "gray")
                if (sources.plugin_count != null) pushKV("Plugins", sources.plugin_count, "gray")
                const snapshot = sources.plugin_snapshot
                if (isRecord(snapshot) && Array.isArray(snapshot.plugins)) {
                  const plugins = snapshot.plugins.slice(0, 6).map((p: any) => p?.id ?? "?")
                  if (plugins.length > 0) pushKV("Plugin IDs", plugins.join(", "), "gray")
                }
              }
            }

            if (inspectMenu.ctree) {
              rows.push({ kind: "header", text: "CTree", color: "dim" })
              pushKV("Loaded", "yes", "gray")
            }

            rows.push({ kind: "header", text: "Local", color: "dim" })
            pushKV("Pending response", pendingResponse ? "yes" : "no", pendingResponse ? COLORS.warning : "gray")
            pushKV("Events", stats.eventCount, "gray")
            pushKV("Tools", stats.toolCount, "gray")
            pushKV("Todos", todos.length, "gray")
            pushKV("Tasks", tasks.length, "gray")
          }
        }

        return (
          <SelectPanel
            width={panelWidth}
            borderColor={COLORS.accent}
            paddingX={2}
            paddingY={1}
            alignSelf={sheetMode ? "flex-start" : "center"}
            marginTop={sheetMode ? 0 : 2}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
            footerLines={footerLines}
          />
        )
      },
    })
  }

  if (ctreeOpen) {
    modalStack.push({
      id: "ctree",
      layout: isBreadboardProfile ? "sheet" : undefined,
      render: () => {
        const sheetMode = isBreadboardProfile
        const panelWidth = sheetMode ? columnWidth : Math.min(PANEL_WIDTH, contentWidth + 2)
        const scroll = Math.max(0, Math.min(ctreeScroll, ctreeMaxScroll))
        const visible = ctreeRows.slice(scroll, scroll + ctreeViewportRows)
        const lineWidth = Math.max(12, panelWidth - 6)

        const titleLines: SelectPanelLine[] = [{ text: CHALK.bold("C-Tree"), color: COLORS.info }]
        const hintLines: SelectPanelLine[] = [
          {
            text:
              ctreeRows.length === 0
                ? "No C-Tree nodes yet."
                : `${ctreeRows.length} node${ctreeRows.length === 1 ? "" : "s"} • ↑/↓ select • PgUp/PgDn page • Enter toggle • Esc close`,
            color: "gray",
          },
          {
            text: `Stage: ${ctreeStage || "—"} • Source: ${ctreeSource || "—"} • Previews: ${ctreeIncludePreviews ? "on" : "off"} • Details: ${ctreeShowDetails ? "on" : "off"}`,
            color: "dim",
          },
        ]

        const panelRows: SelectPanelRow[] = []
        if (ctreeTreeStatus === "loading") {
          panelRows.push({ kind: "empty", text: "Loading C-Tree…", color: "dim" })
        } else if (ctreeTreeError) {
          panelRows.push({ kind: "empty", text: `Error: ${ctreeTreeError}`, color: COLORS.error })
        } else if (ctreeRows.length === 0) {
          panelRows.push({ kind: "empty", text: "No C-Tree nodes loaded.", color: "dim" })
        } else {
          visible.forEach((row: any, idx: number) => {
            const globalIndex = scroll + idx
            const isActive = globalIndex === selectedCTreeIndex
            const indent = "  ".repeat(row.depth ?? 0)
            const hasChildren = Boolean(row.hasChildren)
            const isCollapsed = hasChildren && ctreeCollapsedNodes.has(row.id)
            const marker = hasChildren ? (isCollapsed ? "▸" : "▾") : " "
            const label = formatCTreeNodeLabel(row.node)
            const preview = formatCTreeNodePreview(row.node)
            const flags = formatCTreeNodeFlags(row.node)
            const flagsText = flags.length > 0 ? ` ${CHALK.dim(`[${flags.join(", ")}]`)}` : ""
            const suffix = row.isOrphan ? CHALK.dim(" (orphan)") : ""
            const text = `${indent}${marker} ${label}${flagsText}${suffix}`
            panelRows.push({
              kind: "item",
              text: `${isActive ? "› " : "  "}${formatCell(text, lineWidth, "left")}`,
              secondaryText: preview ? `${indent}  ${preview}` : undefined,
              isActive,
              color: COLORS.text,
              activeColor: COLORS.selectionFg,
              activeBackground: COLORS.info,
            })
          })
          if (ctreeRows.length > ctreeViewportRows) {
            panelRows.push({
              kind: "header",
              text: `${scroll + 1}-${Math.min(scroll + ctreeViewportRows, ctreeRows.length)} of ${ctreeRows.length}`,
              color: "dim",
            })
          }
        }

        const footerLines: SelectPanelLine[] = []
        const ctreeSummary = formatCtreeSummary(ctreeSnapshot ?? null)
        if (ctreeSummary) footerLines.push({ text: ctreeSummary, color: "dim" })
        if (ctreeUpdatedAt) {
          footerLines.push({ text: `Updated: ${formatIsoTimestamp(ctreeUpdatedAt)}`, color: "dim" })
        }

        return (
          <SelectPanel
            width={panelWidth}
            borderColor={COLORS.info}
            paddingX={2}
            paddingY={1}
            alignSelf={sheetMode ? "flex-start" : "center"}
            marginTop={sheetMode ? 0 : 2}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={panelRows}
            footerLines={footerLines}
          />
        )
      },
    })
  }

  if (tasksOpen) {
    modalStack.push({
      id: "tasks",
      layout: isBreadboardProfile ? "sheet" : undefined,
      render: () => {
        const sheetMode = isBreadboardProfile
        const panelWidth = sheetMode ? columnWidth : Math.min(PANEL_WIDTH, contentWidth + 2)
        const scroll = Math.max(0, Math.min(taskScroll, taskMaxScroll))
        const visible = taskRows.slice(scroll, scroll + taskViewportRows)
        const colorForStatus = (status?: string) => {
          switch (status) {
            case "running":
              return COLORS.info
            case "completed":
              return COLORS.success
            case "failed":
              return COLORS.error
            default:
              return COLORS.warning
          }
        }
        const lineWidth = Math.max(12, panelWidth - 6)
        const titleLines: SelectPanelLine[] = [{ text: CHALK.bold("Background tasks"), color: COLORS.info }]
        const hintLines: SelectPanelLine[] = [
          {
            text:
              tasks.length === 0
                ? "No background tasks yet."
                : `${tasks.length} task${tasks.length === 1 ? "" : "s"} • ↑/↓ select • PgUp/PgDn page • Enter tail • Esc close`,
            color: "gray",
          },
          {
            text: `Search: ${taskSearchQuery.length > 0 ? taskSearchQuery : CHALK.dim("<type to filter>")} • Filter: ${taskStatusFilter} (0 all · 1 run · 2 done · 3 fail)`,
            color: "dim",
          },
        ]
        const panelRows: SelectPanelRow[] = []
        if (taskRows.length === 0) {
          panelRows.push({ kind: "empty", text: "No tasks match the current filter.", color: "dim" })
        } else {
          visible.forEach((row: any, idx: number) => {
            const globalIndex = scroll + idx
            const isActive = globalIndex === selectedTaskIndex
            const statusLabel = (row.status || "update").replace(/[_-]+/g, " ")
            const laneLabel = row.task.subagentType || "primary"
            const idLabel = row.id.slice(0, 8)
            const suffix = CHALK.dim(`#${idLabel}`)
            const laneWidth = Math.min(16, Math.max(8, Math.floor(lineWidth * 0.25)))
            const laneCell = formatCell(`[${laneLabel}]`, laneWidth)
            const leftWidth = Math.max(0, lineWidth - stripAnsiCodes(suffix).length - laneWidth - 2)
            const left = formatCell(`${statusLabel} · ${row.label}`, leftWidth)
            const line = `${laneCell} ${left} ${suffix}`
            panelRows.push({
              kind: "item",
              text: `${isActive ? "› " : "  "}${line}`,
              isActive,
              color: colorForStatus(row.status),
              activeColor: COLORS.selectionFg,
              activeBackground: COLORS.info,
            })
          })
          if (taskRows.length > taskViewportRows) {
            panelRows.push({
              kind: "header",
              text: `${scroll + 1}-${Math.min(scroll + taskViewportRows, taskRows.length)} of ${taskRows.length}`,
              color: "dim",
            })
          }
        }

        const footerLines: SelectPanelLine[] = []
        if (selectedTask) {
          footerLines.push({ text: `ID: ${selectedTask.id}`, color: "dim" })
          if (selectedTask.outputExcerpt) {
            footerLines.push({ text: `Output: ${selectedTask.outputExcerpt}`, color: "dim" })
          }
          if (selectedTask.artifactPath) {
            footerLines.push({ text: `Artifact: ${selectedTask.artifactPath}`, color: "dim" })
          }
          if (selectedTask.error) {
            footerLines.push({ text: `Error: ${selectedTask.error}`, color: COLORS.error })
          }
          if (selectedTask.ctreeNodeId) {
            footerLines.push({ text: `CTree node: ${selectedTask.ctreeNodeId}`, color: "dim" })
          }
          footerLines.push({ text: "Enter: load output tail.", color: "dim" })
        }
        const ctreeSummary = formatCtreeSummary(selectedTask?.ctreeSnapshot ?? ctreeSnapshot ?? null)
        if (ctreeSummary) {
          footerLines.push({ text: ctreeSummary, color: "dim" })
        }
        if (taskNotice) {
          footerLines.push({ text: taskNotice, color: "dim" })
        }
        if (taskTailLines.length > 0) {
          footerLines.push({ text: taskTailPath ? `Tail: ${taskTailPath}` : "Tail output", color: "dim" })
          taskTailLines.forEach((line: any) => footerLines.push({ text: line, color: "dim" }))
        }
        return (
          <SelectPanel
            width={panelWidth}
            borderColor={COLORS.info}
            paddingX={2}
            paddingY={1}
            alignSelf={sheetMode ? "flex-start" : "center"}
            marginTop={sheetMode ? 0 : 2}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={panelRows}
            footerLines={footerLines}
          />
        )
      },
    })
  }

  if (permissionRequest) {
    modalStack.push({
      id: "permission",
      render: () => {
        const queue = Math.max(0, permissionQueueDepth ?? 0)
        const queueText = queue > 0 ? ` • +${queue} queued` : ""
        const rewindableText = permissionRequest.rewindable ? "rewindable" : "not rewindable"
        const diffAvailable = Boolean(permissionRequest.diffText)
        const activeTabLabel = (tab: "summary" | "diff" | "rules" | "note", label: string) =>
          permissionTab === tab ? CHALK.bold.cyan(label) : CHALK.gray(label)
        const tabLine = [
          activeTabLabel("summary", "Summary"),
          activeTabLabel("diff", diffAvailable ? "Diff" : "Diff (none)"),
          activeTabLabel("rules", "Rules"),
          activeTabLabel("note", "Feedback"),
        ].join(CHALK.gray("  |  "))

        const diffScrollMax = Math.max(0, permissionDiffLines.length - permissionViewportRows)
        const diffScroll = Math.max(0, Math.min(permissionScroll, diffScrollMax))
        const visibleDiff = permissionDiffLines.slice(diffScroll, diffScroll + permissionViewportRows)

        const scopeLabel = (scope: PermissionRuleScope, label: string) =>
          permissionScope === scope ? CHALK.bold.cyan(label) : CHALK.gray(label)

        const titleLines: SelectPanelLine[] = [
          { text: `${CHALK.bold("Permission required")} ${CHALK.dim(`(${permissionRequest.tool})`)}`, color: COLORS.warning },
        ]
        const hintLines: SelectPanelLine[] = [
          { text: `${permissionRequest.kind} • ${rewindableText}${queueText}`, color: "dim" },
          { text: tabLine },
        ]
        const rows: SelectPanelRow[] = []
        const footerLines: SelectPanelLine[] = []

        if (permissionTab === "summary") {
          rows.push({ kind: "item", text: permissionRequest.summary, wrap: "wrap" })
          if (permissionDiffPreview) {
            rows.push({
              kind: "header",
              text: `Diff: +${permissionDiffPreview.additions} -${permissionDiffPreview.deletions}${permissionDiffPreview.files.length > 0 ? ` • ${permissionDiffPreview.files.join(", ")}` : ""}`,
              color: "dim",
            })
          }
          if (!permissionDiffPreview && diffAvailable) {
            rows.push({ kind: "header", text: "Diff attached.", color: "dim" })
          }
          if (!diffAvailable) {
            rows.push({ kind: "header", text: "No diff attached.", color: "dim" })
          }
        }

        if (permissionTab === "diff") {
          if (!diffAvailable) {
            rows.push({ kind: "empty", text: "No diff attached.", color: "dim" })
          } else {
            rows.push({
              kind: "header",
              text: permissionSelectedSection
                ? `File ${permissionSelectedFileIndex + 1}/${permissionDiffSections.length}: ${permissionSelectedSection.file}`
                : "Diff",
              color: "gray",
            })
            rows.push({
              kind: "header",
              text: `${permissionDiffSections.length > 1 ? "←/→ files • " : ""}↑/↓ scroll • PgUp/PgDn page.${permissionDiffLines.length > 0 ? ` Lines ${diffScroll + 1}-${Math.min(diffScroll + permissionViewportRows, permissionDiffLines.length)} of ${permissionDiffLines.length}.` : ""}`,
              color: "dim",
            })
            if (visibleDiff.length === 0) {
              rows.push({ kind: "empty", text: "Diff is empty.", color: "dim" })
            } else {
              visibleDiff.forEach((line: any, index: number) => {
                rows.push({ kind: "item", text: line, wrap: "truncate-end" })
              })
            }
          }
        }

        if (permissionTab === "rules") {
          rows.push({ kind: "header", text: `Default scope: ${permissionRequest.defaultScope}`, color: "gray" })
          rows.push({
            kind: "item",
            text: `Scope: ${scopeLabel("project", "Project")} ${CHALK.dim("(fixed)")}`,
          })
          rows.push({
            kind: "header",
            text: `Suggested rule: ${permissionRequest.ruleSuggestion ? CHALK.italic(permissionRequest.ruleSuggestion) : CHALK.dim("<none>")}`,
            color: "dim",
          })
          rows.push({ kind: "header", text: "Press 2 or Shift+Tab to allow always (project scope).", color: "dim" })
        }

        if (permissionTab === "note") {
          rows.push({ kind: "header", text: "Tell me what to do differently:", color: "gray" })
          rows.push({ kind: "item", text: renderPermissionNoteLine(permissionNote, permissionNoteCursor), wrap: "truncate-end" })
          rows.push({ kind: "header", text: "Type feedback • Enter deny once • Tab switch tabs", color: "dim" })
        }

        if (permissionError) {
          rows.push({ kind: "header", text: `Error: ${permissionError}`, color: COLORS.error })
        }

        footerLines.push({
          text: "Tab switch panel • Enter allow once • Shift+Tab allow always (project) • 1 allow once • 2 allow always (project) • 3 feedback • d deny once • D deny always",
          color: "gray",
        })
        footerLines.push({ text: "Esc stop (deny)", color: COLORS.error })

        return (
          <SelectPanel
            width={PANEL_WIDTH}
            borderColor={COLORS.accent}
            paddingX={2}
            paddingY={1}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
            footerLines={footerLines}
          />
        )
      },
    })
  }
  return modalStack
}
