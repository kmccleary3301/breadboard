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
  const lastCtrlCRef = useRef<number>(0)

  const dispatch = (input: string, key: Key) => {
    const topLayer = layerRef.current()
    const order = layerOrder(topLayer)
    for (const layer of order) {
      const handler = handlerRef.current[layer]
      if (handler && handler(input, key)) {
        return
      }
    }
  }

  useEffect(() => {
    handlerRef.current = handlers
  }, [handlers])

  useEffect(() => {
    layerRef.current = getTopLayer
  }, [getTopLayer])

  useInput((input, key) => {
    if (key.ctrl && ((key as { name?: string }).name ?? "").toLowerCase() === "c") {
      lastCtrlCRef.current = Date.now()
    }
    dispatch(input, key)
  })

  useEffect(() => {
    const sigintHandler = () => {
      const now = Date.now()
      if (now - lastCtrlCRef.current < 80) {
        return
      }
      lastCtrlCRef.current = now
      const key = {
        ctrl: true,
        meta: false,
        shift: false,
        name: "c",
        sequence: "\u0003",
      } as Key
      dispatch("\u0003", key)
    }
    process.on("SIGINT", sigintHandler)
    return () => {
      process.off("SIGINT", sigintHandler)
    }
  }, [])
}
