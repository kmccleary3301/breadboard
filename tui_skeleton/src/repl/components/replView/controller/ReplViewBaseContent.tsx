import React from "react"
import { Box, Text } from "ink"
import type { GuardrailNotice, StreamStats } from "../../../types.js"
import { GuardrailBanner } from "../../GuardrailBanner.js"
import { ComposerPanel } from "../composer/ComposerPanel.js"
import { CHALK, COLORS } from "../theme.js"

type NetworkBanner = {
  tone: "error" | "warning"
  label: string
  message: string
} | null

type ReplViewBaseContentProps = {
  claudeChrome: boolean
  footerV2Enabled?: boolean
  scrollbackMode: boolean
  sessionId: string
  statusGlyph: string
  status: string
  modelGlyph: string
  stats: StreamStats
  remoteGlyph: string
  eventsGlyph: string
  toolsGlyph: string
  modeBadge: string
  permissionBadge: string
  turnGlyph: string
  usageSummary: string | null
  metaNodes: React.ReactNode[]
  guardrailNotice: GuardrailNotice | null
  networkBanner: NetworkBanner
  networkBannerWidth?: number
  showLandingInline: boolean
  showSessionHeaderInline: boolean
  landingNode: React.ReactNode | null
  sessionHeaderNode: React.ReactNode | null
  transcriptNodes: React.ReactNode[]
  toolNodes: React.ReactNode[]
  overlayActive: boolean
  subagentStripNode: React.ReactNode | null
  liveSlotNodes: React.ReactNode[]
  collapsedHintNode: React.ReactNode | null
  virtualizationHintNode: React.ReactNode | null
  activeBodyMinRows?: number
  composerPanelContext: Record<string, any>
}

export const ReplViewBaseContent: React.FC<ReplViewBaseContentProps> = ({
  claudeChrome,
  footerV2Enabled = false,
  scrollbackMode,
  sessionId,
  statusGlyph,
  status,
  modelGlyph,
  stats,
  remoteGlyph,
  eventsGlyph,
  toolsGlyph,
  modeBadge,
  permissionBadge,
  turnGlyph,
  usageSummary,
  metaNodes,
  guardrailNotice,
  networkBanner,
  networkBannerWidth,
  showLandingInline,
  showSessionHeaderInline,
  landingNode,
  sessionHeaderNode,
  transcriptNodes,
  toolNodes,
  overlayActive,
  subagentStripNode,
  liveSlotNodes,
  collapsedHintNode,
  virtualizationHintNode,
  activeBodyMinRows = 0,
  composerPanelContext,
}) => {
  const bodyMarginTop = scrollbackMode ? 0 : claudeChrome || footerV2Enabled ? 0 : 1
  const resolvedActiveBodyMinRows =
    scrollbackMode && Number.isFinite(activeBodyMinRows)
      ? Math.max(0, Math.floor(activeBodyMinRows))
      : undefined
  const resolvedNetworkBannerWidth =
    typeof networkBannerWidth === "number" && Number.isFinite(networkBannerWidth)
      ? Math.max(20, Math.floor(networkBannerWidth))
      : undefined
  const networkBannerPaddingX = resolvedNetworkBannerWidth && resolvedNetworkBannerWidth <= 50 ? 1 : 2
  return (
    <Box flexDirection="column" paddingX={claudeChrome ? 0 : 1} marginTop={bodyMarginTop}>
      {!claudeChrome && !footerV2Enabled && (
        <Text>
          {CHALK.dim(sessionId.slice(0, 12))} {statusGlyph} {status} {modelGlyph} model {CHALK.bold(stats.model)}{" "}
          {remoteGlyph} {stats.remote ? "remote" : "local"} {eventsGlyph} events {stats.eventCount} {toolsGlyph} tools{" "}
          {stats.toolCount} {modeBadge} {permissionBadge}
          {stats.lastTurn != null ? ` ${turnGlyph} turn ${stats.lastTurn}` : ""}
          {usageSummary ? ` ${CHALK.dim(usageSummary)}` : ""}
        </Text>
      )}
      {metaNodes.length > 0 && (
        <Box flexDirection="column" marginTop={1}>
          {metaNodes}
        </Box>
      )}
      {guardrailNotice && <GuardrailBanner notice={guardrailNotice} />}
      {networkBanner && (
        <Box
          flexDirection="column"
          borderStyle="round"
          borderColor={networkBanner.tone === "error" ? COLORS.error : COLORS.warning}
          paddingX={networkBannerPaddingX}
          paddingY={1}
          marginTop={1}
          width={resolvedNetworkBannerWidth}
        >
          <Text color={networkBanner.tone === "error" ? COLORS.error : COLORS.warning} wrap="wrap">
            {CHALK.bold(`${networkBanner.label}:`)} {networkBanner.message}
          </Text>
          <Text color="gray" wrap="wrap">If this persists, retry the command or restart the session.</Text>
        </Box>
      )}

      <Box flexDirection="column" marginTop={scrollbackMode || claudeChrome ? 0 : 1} minHeight={resolvedActiveBodyMinRows}>
        {showLandingInline && landingNode}
        {showSessionHeaderInline && !showLandingInline && sessionHeaderNode}
        {transcriptNodes.length > 0 && <Box flexDirection="column">{transcriptNodes}</Box>}
        {toolNodes.length > 0 && (
          <Box marginTop={1} flexDirection="column">
            {toolNodes}
          </Box>
        )}
        {!overlayActive && subagentStripNode && <Box marginTop={1}>{subagentStripNode}</Box>}
        {!overlayActive && liveSlotNodes.length > 0 && (
          <Box marginTop={1} flexDirection="column">
            {liveSlotNodes}
          </Box>
        )}
        {!overlayActive && collapsedHintNode && <Box marginTop={1}>{collapsedHintNode}</Box>}
        {!overlayActive && virtualizationHintNode && (
          <Box marginTop={collapsedHintNode ? 0 : 1}>{virtualizationHintNode}</Box>
        )}
      </Box>

      {(!scrollbackMode || !overlayActive) && <ComposerPanel context={composerPanelContext} />}
    </Box>
  )
}
