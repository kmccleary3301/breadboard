import React from "react"
import { Box, Text, useStdout } from "ink"

export interface ModalDescriptor {
  readonly id: string
  readonly render: () => React.ReactNode
  readonly layout?: "center" | "sheet"
}

interface ModalHostProps {
  readonly stack: ReadonlyArray<ModalDescriptor>
  readonly children: React.ReactNode
}

export const ModalHost: React.FC<ModalHostProps> = ({ stack, children }) => {
  const { stdout } = useStdout()
  const topModal = stack.length > 0 ? stack[stack.length - 1] : null
  const width = stdout?.columns && Number.isFinite(stdout.columns) ? stdout.columns : 80
  const height = stdout?.rows && Number.isFinite(stdout.rows) ? stdout.rows : 40
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

  const modalAlign = layout === "sheet" ? "stretch" : "center"
  const modalPaddingBottom = layout === "sheet" ? 0 : 1

  return (
    <Box flexDirection="column" width={width} height={height}>
      <Box flexDirection="column" width="100%" height="100%">
        {children}
      </Box>
      {topModal && (
        <Box
          position="absolute"
          width="100%"
          height="100%"
          flexDirection="column"
          justifyContent="flex-end"
          alignItems={modalAlign}
          paddingBottom={modalPaddingBottom}
        >
          {topModal.render()}
        </Box>
      )}
    </Box>
  )
}
