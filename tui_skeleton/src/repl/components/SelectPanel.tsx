import React, { type ComponentProps } from "react"
import { Box, Text } from "ink"
import { BRAND_COLORS, NEUTRAL_COLORS, SEMANTIC_COLORS } from "../designSystem.js"

export type SelectPanelRow =
  | { readonly kind: "header"; readonly text: string; readonly color?: string }
  | {
      readonly kind: "item"
      readonly text: string
      readonly secondaryText?: string
      readonly wrap?: ComponentProps<typeof Text>["wrap"]
      readonly isActive?: boolean
      readonly color?: string
      readonly activeColor?: string
      readonly activeBackground?: string
      readonly secondaryColor?: string
      readonly secondaryActiveColor?: string
    }
  | { readonly kind: "empty"; readonly text: string; readonly color?: string }

export interface SelectPanelLine {
  readonly text: string
  readonly color?: string
  readonly wrap?: ComponentProps<typeof Text>["wrap"]
}

export interface SelectPanelProps {
  readonly width: number
  readonly borderColor?: string
  readonly borderStyle?: ComponentProps<typeof Box>["borderStyle"]
  readonly showBorder?: boolean
  readonly paddingX?: number
  readonly paddingY?: number
  readonly alignSelf?: ComponentProps<typeof Box>["alignSelf"]
  readonly marginTop?: ComponentProps<typeof Box>["marginTop"]
  readonly titleLines?: ReadonlyArray<SelectPanelLine>
  readonly hintLines?: ReadonlyArray<SelectPanelLine>
  readonly rows: ReadonlyArray<SelectPanelRow>
  readonly footerLines?: ReadonlyArray<SelectPanelLine>
  readonly rowsMarginTop?: number
  readonly footerMarginTop?: number
  readonly titleHintSpacer?: boolean
}

const renderLine = (line: SelectPanelLine, index: number) => (
  <Text key={`line-${index}`} color={line.color} wrap={line.wrap ?? "truncate-end"}>
    {line.text}
  </Text>
)

export const SelectPanel = ({
  width,
  borderColor = SEMANTIC_COLORS.info,
  borderStyle = "round",
  showBorder = true,
  paddingX = 2,
  paddingY = 1,
  alignSelf = "center",
  marginTop = 2,
  titleLines = [],
  hintLines = [],
  rows,
  footerLines = [],
  rowsMarginTop = 1,
  footerMarginTop = 1,
  titleHintSpacer = true,
}: SelectPanelProps) => {
  const borderProps = showBorder ? { borderStyle, borderColor } : {}
  return (
    <Box
      flexDirection="column"
      {...borderProps}
      paddingX={paddingX}
      paddingY={paddingY}
      width={width}
      alignSelf={alignSelf}
      marginTop={marginTop}
    >
      {titleLines.map(renderLine)}
      {titleHintSpacer && titleLines.length > 0 && hintLines.length > 0 && <Text wrap="truncate-end"> </Text>}
      {hintLines.map(renderLine)}
      <Box flexDirection="column" marginTop={rowsMarginTop}>
        {rows.map((row, idx) => {
          if (row.kind === "header" || row.kind === "empty") {
            return (
              <Text key={`row-${idx}`} color={row.color ?? "dim"} wrap="truncate-end">
                {row.text}
              </Text>
            )
          }
          const isActive = Boolean(row.isActive)
          return (
            <Box key={`row-${idx}`} flexDirection="column">
              <Text
                color={isActive ? row.activeColor ?? NEUTRAL_COLORS.nearBlack : row.color}
                backgroundColor={isActive ? row.activeBackground ?? BRAND_COLORS.duneOrange : undefined}
                wrap={row.wrap ?? "truncate-end"}
              >
                {row.text}
              </Text>
              {row.secondaryText && (
                <Text
                  color={
                    isActive
                      ? row.secondaryActiveColor ?? row.activeColor ?? NEUTRAL_COLORS.nearBlack
                      : row.secondaryColor ?? row.color ?? "dim"
                  }
                  backgroundColor={isActive ? row.activeBackground ?? BRAND_COLORS.duneOrange : undefined}
                  wrap="truncate-end"
                >
                  {row.secondaryText}
                </Text>
              )}
            </Box>
          )
        })}
      </Box>
      {footerLines.length > 0 && (
        <Box flexDirection="column" marginTop={footerMarginTop}>
          {footerLines.map(renderLine)}
        </Box>
      )}
    </Box>
  )
}
