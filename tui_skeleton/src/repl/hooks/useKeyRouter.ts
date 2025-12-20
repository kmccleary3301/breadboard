import { useInput, type Key } from "ink"
import { useEffect, useRef } from "react"

export type KeyHandler = (input: string, key: Key) => boolean

export type LayerName = "modal" | "palette" | "editor" | "global"

const layerOrder = (top: LayerName): LayerName[] => {
  switch (top) {
    case "modal":
      return ["modal", "palette", "editor", "global"]
    case "palette":
      return ["palette", "editor", "global"]
    case "editor":
      return ["editor", "global"]
    default:
      return ["global"]
  }
}

export const useKeyRouter = (
  getTopLayer: () => LayerName,
  handlers: Partial<Record<LayerName, KeyHandler>>,
): void => {
  const handlerRef = useRef(handlers)
  const layerRef = useRef(getTopLayer)

  useEffect(() => {
    handlerRef.current = handlers
  }, [handlers])

  useEffect(() => {
    layerRef.current = getTopLayer
  }, [getTopLayer])

  useInput((input, key) => {
    const topLayer = layerRef.current()
    const order = layerOrder(topLayer)
    for (const layer of order) {
      const handler = handlerRef.current[layer]
      if (handler && handler(input, key)) {
        return
      }
    }
  })
}
