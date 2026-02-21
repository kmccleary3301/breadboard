import type React from "react"
import type { ToolLogEntry } from "../../../types.js"

export interface ToolRendererDispatchContext {
  readonly entry: ToolLogEntry
  readonly key?: string
  readonly fallbackRender: (entry: ToolLogEntry, key?: string) => React.ReactNode
  readonly fallbackMeasure: (entry: ToolLogEntry) => number
}

export interface ToolRendererHandler {
  readonly id: string
  readonly match: (entry: ToolLogEntry) => boolean
  readonly render?: (ctx: ToolRendererDispatchContext) => React.ReactNode
  readonly measure?: (ctx: ToolRendererDispatchContext) => number
}

export interface ToolRendererRegistry {
  resolve: (entry: ToolLogEntry) => ToolRendererHandler | null
}

export const createToolRendererRegistry = (handlers: readonly ToolRendererHandler[]): ToolRendererRegistry => {
  const normalized = [...handlers]
  return {
    resolve(entry: ToolLogEntry): ToolRendererHandler | null {
      for (const handler of normalized) {
        if (handler.match(entry)) return handler
      }
      return null
    },
  }
}

export const DEFAULT_TOOL_RENDERER_REGISTRY = createToolRendererRegistry([
  {
    id: "legacy-default",
    match: () => true,
    render: ({ entry, key, fallbackRender }) => fallbackRender(entry, key),
    measure: ({ entry, fallbackMeasure }) => fallbackMeasure(entry),
  },
])
