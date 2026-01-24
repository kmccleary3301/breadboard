import { promises as fs } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(here, "..")
const src = path.join(projectRoot, "src", "ascii_header.txt")
const dest = path.join(projectRoot, "dist", "ascii_header.txt")

const main = async () => {
  await fs.mkdir(path.dirname(dest), { recursive: true })
  await fs.copyFile(src, dest)
}

void main()
