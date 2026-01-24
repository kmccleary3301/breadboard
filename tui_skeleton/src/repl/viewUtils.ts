import { readFileSync, existsSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { SEMANTIC_COLORS } from "./designSystem.js"

const resolveAsciiHeader = (): string[] => {
  try {
    const current = path.dirname(fileURLToPath(import.meta.url))
    const candidates = [
      path.join(current, "./ascii_header.txt"),
      path.join(current, "../ascii_header.txt"),
      path.join(process.cwd(), "src/ascii_header.txt"),
    ]
    for (const candidate of candidates) {
      if (existsSync(candidate)) {
        const raw = readFileSync(candidate, "utf8").replace(/\r\n/g, "\n").replace(/\n+$/, "")
        return raw.split("\n")
      }
    }
  } catch {
    // ignore — fall back to default header
  }
  return ["BreadBoard"]
}

export const ASCII_HEADER = resolveAsciiHeader()
export const HEADER_COLOR = "#FF4D6D"

export const speakerColor = (speaker: "assistant" | "user" | "system"): string => {
  switch (speaker) {
    case "user":
      return SEMANTIC_COLORS.user
    case "assistant":
      return SEMANTIC_COLORS.assistant
    case "system":
    default:
      return SEMANTIC_COLORS.system
  }
}

export const TOOL_EVENT_COLOR = SEMANTIC_COLORS.tool
