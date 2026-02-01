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
  showLandingInline: boolean
  landingNode: React.ReactNode | null
  transcriptNodes: React.ReactNode[]
  toolNodes: React.ReactNode[]
  overlayActive: boolean
  liveSlotNodes: React.ReactNode[]
  collapsedHintNode: React.ReactNode | null
  virtualizationHintNode: React.ReactNode | null
  composerPanelContext: Record<string, any>
}

export const ReplViewBaseContent: React.FC<ReplViewBaseContentProps> = ({
  claudeChrome,
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
  showLandingInline,
  landingNode,
  transcriptNodes,
  toolNodes,
  overlayActive,
  liveSlotNodes,
  collapsedHintNode,
  virtualizationHintNode,
  composerPanelContext,
}) => (
  <Box flexDirection="column" paddingX={claudeChrome ? 0 : 1} marginTop={scrollbackMode ? 0 : 1}>
    {!claudeChrome && (
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
        paddingX={2}
        paddingY={1}
        marginTop={1}
      >
        <Text color={networkBanner.tone === "error" ? COLORS.error : COLORS.warning}>
          {CHALK.bold(`${networkBanner.label}:`)} {networkBanner.message}
        </Text>
        <Text color="gray">If this persists, retry the command or restart the session.</Text>
      </Box>
    )}

    <Box flexDirection="column" marginTop={claudeChrome ? 0 : 1}>
      {showLandingInline && landingNode}
      {transcriptNodes.length > 0 && (
        <Box flexDirection="column">{transcriptNodes}</Box>
      )}
      {toolNodes.length > 0 && (
        <Box marginTop={1} flexDirection="column">
          {toolNodes}
        </Box>
      )}
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

    <ComposerPanel context={composerPanelContext} />
  </Box>
)
