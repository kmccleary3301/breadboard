import { renderOptionalJsonBlock, renderSectionBlock } from "./commandText.js"

export interface DetailPresentationSection {
  readonly title: string
  readonly lines: readonly string[]
  readonly emptyText?: string
}

export interface DetailPresentationJsonBlock {
  readonly label: string
  readonly value: unknown | null | undefined
}

export const renderDetailPresentation = (
  title: string,
  options: {
    readonly lines?: readonly string[]
    readonly sections?: readonly DetailPresentationSection[]
    readonly jsonBlocks?: readonly DetailPresentationJsonBlock[]
    readonly trailingLines?: readonly string[]
  } = {},
): string => {
  const blocks: string[] = []

  if ((options.lines ?? []).length > 0) {
    blocks.push((options.lines ?? []).join("\n"))
  }

  for (const section of options.sections ?? []) {
    const block = renderSectionBlock(section.title, [...section.lines], section.emptyText)
    if (block.length > 0) {
      blocks.push(block)
    }
  }

  for (const jsonBlock of options.jsonBlocks ?? []) {
    const block = renderOptionalJsonBlock(jsonBlock.label, jsonBlock.value)
    if (block) {
      blocks.push(block)
    }
  }

  if ((options.trailingLines ?? []).length > 0) {
    blocks.push((options.trailingLines ?? []).join("\n"))
  }

  return [title, ...blocks].join("\n\n")
}
