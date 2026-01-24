#!/usr/bin/env tsx
import fs from "node:fs/promises"
import path from "node:path"
import { PNG } from "pngjs"

type Options = {
  left: string
  right: string
  out?: string
  diff?: string
  padding: number
  bg: [number, number, number, number]
}

const parseHex = (value: string): [number, number, number, number] => {
  const hex = value.replace(/^#/, "").trim()
  if (hex.length !== 6) throw new Error("Expected hex color like 0f172a or #0f172a")
  const r = parseInt(hex.slice(0, 2), 16)
  const g = parseInt(hex.slice(2, 4), 16)
  const b = parseInt(hex.slice(4, 6), 16)
  return [r, g, b, 255]
}

const parseArgs = (argv: string[]): Options => {
  const args = argv.slice(2)
  let left = ""
  let right = ""
  let out: string | undefined
  let diff: string | undefined
  let padding = 16
  let bg: [number, number, number, number] = [15, 23, 42, 255]

  for (let i = 0; i < args.length; i += 1) {
    const token = args[i] ?? ""
    if (token === "--out") {
      out = args[++i]
      continue
    }
    if (token === "--diff") {
      diff = args[++i]
      continue
    }
    if (token === "--padding") {
      padding = Number(args[++i] ?? "16")
      continue
    }
    if (token === "--bg") {
      bg = parseHex(args[++i] ?? "0f172a")
      continue
    }
    if (!left) {
      left = token
      continue
    }
    if (!right) {
      right = token
      continue
    }
  }

  if (!left || !right) {
    throw new Error("Usage: tsx scripts/compare_pngs.ts <left.png> <right.png> [--out out.png] [--diff diff.png]")
  }

  return { left, right, out, diff, padding, bg }
}

const readPng = async (filePath: string): Promise<PNG> => {
  const data = await fs.readFile(filePath)
  return PNG.sync.read(data)
}

const blit = (src: PNG, dest: PNG, offsetX: number, offsetY: number) => {
  for (let y = 0; y < src.height; y += 1) {
    for (let x = 0; x < src.width; x += 1) {
      const srcIdx = (src.width * y + x) << 2
      const destIdx = (dest.width * (y + offsetY) + (x + offsetX)) << 2
      dest.data[destIdx] = src.data[srcIdx]
      dest.data[destIdx + 1] = src.data[srcIdx + 1]
      dest.data[destIdx + 2] = src.data[srcIdx + 2]
      dest.data[destIdx + 3] = src.data[srcIdx + 3]
    }
  }
}

const makeCanvas = (width: number, height: number, bg: [number, number, number, number]) => {
  const canvas = new PNG({ width, height })
  for (let i = 0; i < canvas.data.length; i += 4) {
    canvas.data[i] = bg[0]
    canvas.data[i + 1] = bg[1]
    canvas.data[i + 2] = bg[2]
    canvas.data[i + 3] = bg[3]
  }
  return canvas
}

const buildDiff = (left: PNG, right: PNG, bg: [number, number, number, number]) => {
  const width = Math.max(left.width, right.width)
  const height = Math.max(left.height, right.height)
  const diff = makeCanvas(width, height, bg)
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const leftIdx = x < left.width && y < left.height ? (left.width * y + x) << 2 : -1
      const rightIdx = x < right.width && y < right.height ? (right.width * y + x) << 2 : -1
      const l = leftIdx >= 0 ? left.data.slice(leftIdx, leftIdx + 4) : bg
      const r = rightIdx >= 0 ? right.data.slice(rightIdx, rightIdx + 4) : bg
      const dr = Math.abs(l[0] - r[0])
      const dg = Math.abs(l[1] - r[1])
      const db = Math.abs(l[2] - r[2])
      const diffIdx = (width * y + x) << 2
      diff.data[diffIdx] = dr
      diff.data[diffIdx + 1] = dg
      diff.data[diffIdx + 2] = db
      diff.data[diffIdx + 3] = 255
    }
  }
  return diff
}

const main = async () => {
  const options = parseArgs(process.argv)
  const left = await readPng(options.left)
  const right = await readPng(options.right)

  const height = Math.max(left.height, right.height)
  const width = left.width + options.padding + right.width
  const outPath =
    options.out ??
    path.join(
      path.dirname(options.left),
      `side_by_side_${path.basename(options.left, ".png")}_${path.basename(options.right)}`,
    )

  const canvas = makeCanvas(width, height, options.bg)
  blit(left, canvas, 0, 0)
  blit(right, canvas, left.width + options.padding, 0)

  await fs.writeFile(outPath, PNG.sync.write(canvas))
  process.stdout.write(`[compare_pngs] wrote ${outPath}\n`)

  if (options.diff) {
    const diff = buildDiff(left, right, options.bg)
    await fs.writeFile(options.diff, PNG.sync.write(diff))
    process.stdout.write(`[compare_pngs] wrote ${options.diff}\n`)
  }
}

main().catch((error) => {
  console.error(`[compare_pngs] failed: ${(error as Error).message}`)
  process.exitCode = 1
})
