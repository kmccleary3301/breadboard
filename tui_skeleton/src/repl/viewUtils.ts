import { readFileSync, existsSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

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
  return ["Kyle's Secret Coder"]
}

export const ASCII_HEADER = resolveAsciiHeader()

export const speakerColor = (speaker: "assistant" | "user" | "system"): string => {
  switch (speaker) {
    case "user":
      return "#00D38D"
    case "assistant":
      return "#B36BFF"
    case "system":
    default:
      return "#FACC15"
  }
}

export const TOOL_EVENT_COLOR = "#FBBF24"
