import { execFile } from "node:child_process"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { promisify } from "node:util"
import clipboardy from "clipboardy"

const execFileAsync = promisify(execFile)
const COMMAND_TIMEOUT_MS = 2_500
const MAX_BUFFER = 10 * 1024 * 1024
const FAKE_ENV = "BREADBOARD_FAKE_CLIPBOARD"

export interface ClipboardImage {
  readonly kind: "image"
  readonly mime: string
  readonly base64: string
  readonly size: number
}

export interface ClipboardText {
  readonly kind: "text"
  readonly text: string
}

export type ClipboardContent = ClipboardImage | ClipboardText

const debugLog = (payload: Record<string, unknown>) => {
  if (process.env.BREADBOARD_INPUT_DEBUG === "1") {
    console.error(JSON.stringify(payload))
  }
}

const runCommandBuffer = async (command: string, args: string[]): Promise<Buffer | undefined> => {
  try {
    const { stdout } = await execFileAsync(command, args, {
      encoding: "buffer",
      timeout: COMMAND_TIMEOUT_MS,
      maxBuffer: MAX_BUFFER,
    })
    if (Buffer.isBuffer(stdout) && stdout.length > 0) {
      return stdout
    }
  } catch (error) {
    debugLog({ clipboardCommandError: String(error), command, args })
  }
  return undefined
}

const runCommandText = async (command: string, args: string[]): Promise<string | undefined> => {
  try {
    const { stdout } = await execFileAsync(command, args, {
      encoding: "utf8",
      timeout: COMMAND_TIMEOUT_MS,
      maxBuffer: MAX_BUFFER,
    })
    if (typeof stdout === "string" && stdout.length > 0) {
      return stdout
    }
  } catch (error) {
    debugLog({ clipboardCommandError: String(error), command, args })
  }
  return undefined
}

const parseFakeClipboard = (value: string): ClipboardContent => {
  const dataUriPrefix = /^data:(?<mime>[^;]+);base64,(?<payload>[A-Za-z0-9+/=\n\r]+)$/
  const match = value.match(dataUriPrefix)
  if (match && match.groups?.mime && match.groups?.payload) {
    const base64 = match.groups.payload.replace(/\s+/g, "")
    return {
      kind: "image",
      mime: match.groups.mime,
      base64,
      size: Buffer.from(base64, "base64").length,
    }
  }
  return { kind: "text", text: value }
}

const readMacImage = async (): Promise<ClipboardImage | undefined> => {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "breadboard-clip-"))
  const target = path.join(tempDir, "clip.png")
  try {
    const script = [
      "set imageData to the clipboard as «class PNGf»",
      `set fileRef to open for access POSIX file \"${target}\" with write permission`,
      "set eof fileRef to 0",
      "write imageData to fileRef",
      "close access fileRef",
    ]
    await runCommandText("osascript", script.flatMap((line) => ["-e", line]))
    const data = await fs.readFile(target)
    if (data.length > 0) {
      return { kind: "image", mime: "image/png", base64: data.toString("base64"), size: data.length }
    }
  } catch (error) {
    debugLog({ macClipboardError: String(error) })
  } finally {
    await fs.rm(target, { force: true }).catch(() => {})
  }
  return undefined
}

const readLinuxImage = async (): Promise<ClipboardImage | undefined> => {
  const candidates: Array<[string, string[]]> = []
  if (process.env.WAYLAND_DISPLAY) {
    candidates.push(["wl-paste", ["-t", "image/png"]])
  }
  candidates.push(["xclip", ["-selection", "clipboard", "-t", "image/png", "-o"]])
  candidates.push(["xsel", ["--clipboard", "--output"]])

  for (const [command, args] of candidates) {
    const buffer = await runCommandBuffer(command, args)
    if (buffer && buffer.length > 0) {
      return { kind: "image", mime: "image/png", base64: buffer.toString("base64"), size: buffer.length }
    }
  }
  return undefined
}

const readWindowsImage = async (): Promise<ClipboardImage | undefined> => {
  const script =
    "Add-Type -AssemblyName System.Windows.Forms; $img = [System.Windows.Forms.Clipboard]::GetImage(); if ($img) { $ms = New-Object System.IO.MemoryStream; $img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); [System.Convert]::ToBase64String($ms.ToArray()) }"
  const output = await runCommandText("powershell", ["-NoProfile", "-Command", script])
  if (output && output.trim().length > 0) {
    const trimmed = output.trim()
    return { kind: "image", mime: "image/png", base64: trimmed, size: Buffer.from(trimmed, "base64").length }
  }
  return undefined
}

const readPlatformImage = async (): Promise<ClipboardImage | undefined> => {
  const platform = os.platform()
  if (platform === "darwin") return readMacImage()
  if (platform === "linux") return readLinuxImage()
  if (platform === "win32") return readWindowsImage()
  return undefined
}

const readPlatformText = async (): Promise<string | undefined> => {
  const platform = os.platform()
  if (platform === "darwin") {
    const pb = await runCommandText("pbpaste", [])
    if (pb !== undefined) return pb
  }
  if (platform === "linux") {
    if (process.env.WAYLAND_DISPLAY) {
      const wayland = await runCommandText("wl-paste", ["-n"])
      if (wayland) return wayland
    }
    const xclip = await runCommandText("xclip", ["-selection", "clipboard", "-o"])
    if (xclip) return xclip
    const xsel = await runCommandText("xsel", ["--clipboard", "--output"])
    if (xsel) return xsel
  }
  if (platform === "win32") {
    const text = await runCommandText("powershell", ["-NoProfile", "-Command", "Get-Clipboard"])
    if (text) return text.replace(/\r\n$/, "\n")
  }
  return undefined
}

export const readClipboardContent = async (): Promise<ClipboardContent | undefined> => {
  const fakeValue = process.env[FAKE_ENV]
  if (fakeValue !== undefined) {
    return parseFakeClipboard(fakeValue)
  }

  const image = await readPlatformImage()
  if (image) return image

  const text = (await readPlatformText()) ?? (await clipboardy.read().catch(() => undefined))
  if (text !== undefined) {
    return { kind: "text", text }
  }
  return undefined
}
