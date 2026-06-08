import React from "react"
import { Box, Text, useStdout } from "ink"
import { useTerminalSize } from "../hooks/useTerminalSize.js"

export interface ModalDescriptor {
  readonly id: string
  readonly render: () => React.ReactNode
  readonly layout?: "center" | "sheet"
  readonly estimatedRows?: number
}

interface ModalHostProps {
  readonly stack: ReadonlyArray<ModalDescriptor>
  readonly children: React.ReactNode
}

export const ModalHost: React.FC<ModalHostProps> = ({ stack, children }) => {
  const { stdout } = useStdout()
  const terminalSize = useTerminalSize(stdout)
  const topModal = stack.length > 0 ? stack[stack.length - 1] : null
  const fixedFrameWidthRaw = String(process.env.BREADBOARD_TUI_FRAME_WIDTH ?? "").trim()
  const fixedFrameWidth = fixedFrameWidthRaw.length > 0 ? Number(fixedFrameWidthRaw) : NaN
  const width = Number.isFinite(fixedFrameWidth) && fixedFrameWidth > 0
    ? Math.min(Math.floor(fixedFrameWidth), terminalSize.columns)
    : terminalSize.columns
  const layout = topModal?.layout ?? "center"

  if (topModal && layout === "sheet") {
    // Sheet overlays should occupy composer space in-flow, not float at terminal bottom.
    return (
      <Box flexDirection="column" width={width}>
        {children}
        {topModal.render()}
      </Box>
    )
  }

  // Default inline mode does not get a terminal-height takeover surface. Even
  // centered overlays stay in-flow below the active viewport so they remain a
  // bounded interaction surface rather than a hidden fullscreen layer.
  const modalAlign = layout === "sheet" ? "stretch" : "center"
  const modalMarginTop = layout === "sheet" ? 0 : 1

  return (
    <Box flexDirection="column" width={width}>
      {children}
      {topModal && (
        <Box
          width="100%"
          flexDirection="column"
          alignItems={modalAlign}
          marginTop={modalMarginTop}
        >
          {topModal.render()}
        </Box>
      )}
    </Box>
  )
}
