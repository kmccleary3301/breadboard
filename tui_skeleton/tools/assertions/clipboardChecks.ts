import { promises as fs } from "node:fs"
import path from "node:path"

export interface ClipboardAssertions {
  readonly expectTextChip?: boolean
  readonly expectAttachmentChip?: boolean
  readonly expectImageAttachment?: boolean
  readonly expectedImageMetadata?: {
    readonly bytes?: number
    readonly averageColor?: string
  }
}

const readFileSafe = async (filePath: string) => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const containsPattern = (payload: string, pattern: RegExp) => {
  if (!payload) return false
  return pattern.test(payload)
}

export const runClipboardAssertions = async (caseDir: string, assertions: ClipboardAssertions): Promise<void> => {
  const snapshotsPath = path.join(caseDir, "pty_snapshots.txt")
  const gridFinalPath = path.join(caseDir, "grid_snapshots", "final.txt")
  const manifestPath = path.join(caseDir, "pty_manifest.json")
  const errorPath = path.join(caseDir, "clipboard_errors.json")
  const plainSnapshots = await readFileSafe(snapshotsPath)
  const gridFinal = await readFileSafe(gridFinalPath)
  const haystacks = [plainSnapshots, gridFinal]
  let clipboardMeta: Record<string, unknown> | null = null
  try {
    const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8")) as { clipboard?: Record<string, unknown> }
    clipboardMeta = manifest.clipboard ?? null
  } catch {
    clipboardMeta = null
  }
  const errors: string[] = []

  if (assertions.expectTextChip) {
    const needle = /\[Pasted Content \d+ chars\]/
    if (!haystacks.some((text) => containsPattern(text, needle))) {
      errors.push("expected '[Pasted Content …]' placeholder not found")
    }
  }

  if (assertions.expectAttachmentChip) {
    const needle = /\[Attachment \d+:/
    if (!haystacks.some((text) => containsPattern(text, needle))) {
      errors.push("expected attachment chip '[Attachment N: …]' not found")
    }
  }

  if (assertions.expectImageAttachment) {
    const needle = /\[Attachment \d+:\s+image\//i
    if (!haystacks.some((text) => containsPattern(text, needle))) {
      errors.push("expected image attachment descriptor '[Attachment N: image/... ]' not found")
    }
    if (!clipboardMeta || typeof clipboardMeta.mime !== "string" || !clipboardMeta.mime.startsWith("image/")) {
      errors.push("clipboard metadata missing image mime")
    }
  }

  if (assertions.expectedImageMetadata) {
    if (!clipboardMeta) {
      errors.push("clipboard metadata missing for image attachment")
    } else {
      const bytes = typeof clipboardMeta.bytes === "number" ? clipboardMeta.bytes : null
      const avgColor = typeof clipboardMeta.averageColor === "string" ? clipboardMeta.averageColor : null
      if (assertions.expectedImageMetadata.bytes != null && bytes !== assertions.expectedImageMetadata.bytes) {
        errors.push(`image bytes mismatch (expected ${assertions.expectedImageMetadata.bytes}, saw ${bytes ?? "n/a"})`)
      }
      if (
        assertions.expectedImageMetadata.averageColor &&
        avgColor?.toLowerCase() !== assertions.expectedImageMetadata.averageColor.toLowerCase()
      ) {
        errors.push(
          `image average color mismatch (expected ${assertions.expectedImageMetadata.averageColor}, saw ${avgColor ?? "n/a"})`,
        )
      }
    }
  }

  if (errors.length > 0) {
    const message = errors.join("; ")
    await fs.writeFile(
      errorPath,
      JSON.stringify(
        {
          manifestPath,
          clipboard: clipboardMeta,
          assertions,
          errors,
        },
        null,
        2,
      ),
      "utf8",
    ).catch(() => undefined)
    throw new Error(`[clipboard] ${message}`)
  }

  await fs.rm(errorPath, { force: true }).catch(() => undefined)
}
