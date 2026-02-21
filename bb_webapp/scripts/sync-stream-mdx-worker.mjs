import { mkdirSync, copyFileSync, existsSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = join(__dirname, "..")

const source = join(root, "node_modules", "@stream-mdx", "worker", "dist", "hosted", "markdown-worker.js")
const targetDir = join(root, "public", "workers")
const target = join(targetDir, "markdown-worker.js")

if (!existsSync(source)) {
  throw new Error(`stream-mdx worker bundle not found: ${source}`)
}

mkdirSync(targetDir, { recursive: true })
copyFileSync(source, target)

console.log(`synced worker: ${target}`)
