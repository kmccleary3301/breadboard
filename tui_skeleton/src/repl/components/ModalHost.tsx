import React from "react"
import { Box, Text, useStdout } from "ink"

export interface ModalDescriptor {
  readonly id: string
  readonly render: () => React.ReactNode
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
          alignItems="center"
          paddingBottom={1}
        >
          {topModal.render()}
        </Box>
      )}
    </Box>
  )
}
