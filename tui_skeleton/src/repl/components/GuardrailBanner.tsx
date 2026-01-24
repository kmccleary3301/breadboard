import React from "react"
import { Box, Text } from "ink"
import chalk from "chalk"
import type { GuardrailNotice } from "../types.js"
import { SEMANTIC_COLORS, resolveAsciiOnly, resolveIcons, resolveColorMode } from "../designSystem.js"
import { SEMANTIC_COLORS, resolveAsciiOnly, resolveIcons, resolveColorMode } from "../designSystem.js"

interface GuardrailBannerProps {
  readonly notice: GuardrailNotice
}

export const GuardrailBanner: React.FC<GuardrailBannerProps> = ({ notice }) => {
  const asciiOnly = resolveAsciiOnly()
  const icons = resolveIcons(asciiOnly)
  const colorMode = resolveColorMode()
  if (colorMode === "none") {
    ;(chalk as typeof chalk & { level: number }).level = 0
  }
  const bullet = asciiOnly ? "*" : "•"
  const summaryLabel = chalk.hex(SEMANTIC_COLORS.error)(`${icons.error} Guardrail:`)
  const hintText =
    notice.detail != null
      ? notice.expanded
        ? `Press e to collapse ${bullet} Press x to dismiss`
        : `Press e to expand ${bullet} Press x to dismiss`
      : "Press x to dismiss"
  const detailHint = chalk.dim(hintText)
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={SEMANTIC_COLORS.error} paddingX={2} paddingY={1} marginTop={1}>
      <Text>
        {summaryLabel} {notice.summary}
      </Text>
      {notice.detail && notice.expanded && (
        <Text color="white" wrap="truncate-end">
          {notice.detail}
        </Text>
      )}
      <Text color="gray">{detailHint}</Text>
    </Box>
  )
}
