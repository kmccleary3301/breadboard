import { createHash } from "node:crypto"
import { mkdirSync, writeFileSync, existsSync } from "node:fs"
import path from "node:path"
import type { ToolArtifactRef } from "../../repl/types.js"

const parseBoundedInt = (value: string | undefined, fallback: number, min: number, max: number): number => {
  if (!value?.trim()) return fallback
  const parsed = Number.parseInt(value.trim(), 10)
  if (!Number.isFinite(parsed)) return fallback
  if (parsed < min) return min
  if (parsed > max) return max
  return parsed
}

const INLINE_MAX_BYTES = parseBoundedInt(process.env.BREADBOARD_TOOL_ARTIFACT_INLINE_MAX_BYTES, 12_000, 256, 5_000_000)
const DIFF_INLINE_MAX_BYTES = parseBoundedInt(
  process.env.BREADBOARD_TOOL_ARTIFACT_DIFF_INLINE_MAX_BYTES,
  16_000,
  256,
  5_000_000,
)
const OUTPUT_ROOT = process.env.BREADBOARD_TOOL_ARTIFACT_OUTPUT_ROOT?.trim() || "docs_tmp/tui_tool_artifacts"

const normalizeLines = (value: string): string[] =>
  value
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.trimEnd())

const extForMime = (mime: string): string => {
  if (mime.includes("diff")) return "diff"
  if (mime.includes("json")) return "json"
  if (mime.includes("markdown")) return "md"
  return "txt"
}

const toPosixRelative = (value: string): string => value.split(path.sep).join("/")

export const shouldMaterializeToolArtifact = (content: string, kind: ToolArtifactRef["kind"]): boolean => {
  const size = Buffer.byteLength(content, "utf8")
  const maxBytes = kind === "tool_diff" ? DIFF_INLINE_MAX_BYTES : INLINE_MAX_BYTES
  return size > maxBytes
}

export const materializeToolArtifactRef = (input: {
  readonly kind: ToolArtifactRef["kind"]
  readonly mime: string
  readonly content: string
  readonly previewMaxLines?: number
  readonly note?: string | null
}): ToolArtifactRef => {
  const content = input.content ?? ""
  const sha256 = createHash("sha256").update(content, "utf8").digest("hex")
  const id = `artifact-${sha256.slice(0, 16)}`
  const ext = extForMime(input.mime)
  const rootAbs = path.isAbsolute(OUTPUT_ROOT) ? OUTPUT_ROOT : path.join(process.cwd(), OUTPUT_ROOT)
  const kindDir = path.join(rootAbs, input.kind)
  mkdirSync(kindDir, { recursive: true })
  const fileName = `${id}.${ext}`
  const filePathAbs = path.join(kindDir, fileName)
  if (!existsSync(filePathAbs)) {
    writeFileSync(filePathAbs, content, "utf8")
  }
  const relativePath = toPosixRelative(path.relative(process.cwd(), filePathAbs))
  const lines = normalizeLines(content)
  const previewMax = Math.max(1, Math.floor(input.previewMaxLines ?? 10))
  const previewLines = lines.slice(0, previewMax)
  return {
    schema_version: "artifact_ref_v1",
    id,
    kind: input.kind,
    mime: input.mime,
    size_bytes: Buffer.byteLength(content, "utf8"),
    sha256,
    storage: "workspace_file",
    path: relativePath,
    preview: {
      lines: previewLines,
      omitted_lines: Math.max(0, lines.length - previewLines.length),
      note: input.note ?? null,
    },
  }
}

export const normalizeArtifactRef = (value: unknown): ToolArtifactRef | null => {
  if (!value || typeof value !== "object") return null
  const record = value as Record<string, unknown>
  const schemaVersion = String(record.schema_version ?? "")
  if (schemaVersion !== "artifact_ref_v1") return null
  const id = typeof record.id === "string" ? record.id : null
  const kind = typeof record.kind === "string" ? record.kind : null
  const mime = typeof record.mime === "string" ? record.mime : null
  const sizeBytes = typeof record.size_bytes === "number" ? record.size_bytes : null
  const sha256 = typeof record.sha256 === "string" ? record.sha256 : null
  const storage = typeof record.storage === "string" ? record.storage : null
  const artifactPath = typeof record.path === "string" ? record.path : null
  if (!id || !kind || !mime || sizeBytes == null || !sha256 || !storage || !artifactPath) return null
  if (kind !== "tool_output" && kind !== "tool_diff" && kind !== "tool_result") return null
  if (storage !== "workspace_file") return null
  const previewRaw = record.preview
  const preview =
    previewRaw && typeof previewRaw === "object"
      ? {
          lines: Array.isArray((previewRaw as Record<string, unknown>).lines)
            ? ((previewRaw as Record<string, unknown>).lines as unknown[])
                .map((line) => (typeof line === "string" ? line : ""))
                .filter((line) => line.length > 0)
            : null,
          omitted_lines:
            typeof (previewRaw as Record<string, unknown>).omitted_lines === "number"
              ? ((previewRaw as Record<string, unknown>).omitted_lines as number)
              : null,
          note:
            typeof (previewRaw as Record<string, unknown>).note === "string"
              ? ((previewRaw as Record<string, unknown>).note as string)
              : null,
        }
      : null
  return {
    schema_version: "artifact_ref_v1",
    id,
    kind,
    mime,
    size_bytes: Math.max(0, Math.floor(sizeBytes)),
    sha256,
    storage: "workspace_file",
    path: artifactPath,
    preview,
  }
}
