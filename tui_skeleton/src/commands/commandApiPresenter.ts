import { renderDetailPresentation, type DetailPresentationJsonBlock, type DetailPresentationSection } from "./commandDetailPresentation.js"
import { renderReportDocument, type ReportSection } from "./commandReport.js"
import { printCommandResultEnvelope, type CommandResultEnvelopeMode } from "./commandResultEnvelope.js"

export interface ReportCommandResultOptions<T> {
  readonly mode: CommandResultEnvelopeMode
  readonly title: string
  readonly jsonValue: T
  readonly yamlText?: string | null
  readonly lines?: readonly string[]
  readonly sections?: readonly ReportSection[]
}

export interface DetailCommandResultOptions<T> {
  readonly mode: CommandResultEnvelopeMode
  readonly title: string
  readonly jsonValue: T
  readonly yamlText?: string | null
  readonly lines?: readonly string[]
  readonly sections?: readonly DetailPresentationSection[]
  readonly jsonBlocks?: readonly DetailPresentationJsonBlock[]
  readonly trailingLines?: readonly string[]
}

export const renderReportCommandResultText = (options: {
  readonly title: string
  readonly lines?: readonly string[]
  readonly sections?: readonly ReportSection[]
}): string =>
  renderReportDocument(options.title, {
    lines: options.lines,
    sections: options.sections,
  })

export const renderDetailCommandResultText = (options: {
  readonly title: string
  readonly lines?: readonly string[]
  readonly sections?: readonly DetailPresentationSection[]
  readonly jsonBlocks?: readonly DetailPresentationJsonBlock[]
  readonly trailingLines?: readonly string[]
}): string =>
  renderDetailPresentation(options.title, {
    lines: options.lines,
    sections: options.sections,
    jsonBlocks: options.jsonBlocks,
    trailingLines: options.trailingLines,
  })

export const printReportCommandResult = async <T>(options: ReportCommandResultOptions<T>): Promise<void> => {
  await printCommandResultEnvelope({
    mode: options.mode,
    envelope: {
      jsonValue: options.jsonValue,
      text: renderReportCommandResultText(options),
      yamlText: options.yamlText,
    },
  })
}

export const printDetailCommandResult = async <T>(options: DetailCommandResultOptions<T>): Promise<void> => {
  await printCommandResultEnvelope({
    mode: options.mode,
    envelope: {
      jsonValue: options.jsonValue,
      text: renderDetailCommandResultText(options),
      yamlText: options.yamlText,
    },
  })
}
