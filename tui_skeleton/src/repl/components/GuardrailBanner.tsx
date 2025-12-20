import React from "react"
import { Box, Text } from "ink"
import chalk from "chalk"
import type { GuardrailNotice } from "../types.js"

interface GuardrailBannerProps {
  readonly notice: GuardrailNotice
}

export const GuardrailBanner: React.FC<GuardrailBannerProps> = ({ notice }) => {
  const summaryLabel = chalk.hex("#fb7185")("! Guardrail:")
  const hintText =
    notice.detail != null
      ? notice.expanded
        ? "Press e to collapse • Press x to dismiss"
        : "Press e to expand • Press x to dismiss"
      : "Press x to dismiss"
  const detailHint = chalk.dim(hintText)
  return (
    <Box flexDirection="column" borderStyle="round" borderColor="#fb7185" paddingX={2} paddingY={1} marginTop={1}>
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
