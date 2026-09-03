import { copyFileSync, mkdirSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const source = resolve(
  packageRoot,
  "../../contracts/kernel/manifests/bb.engine_conformance_manifest.v1.schema.json",
)
const destination = resolve(
  packageRoot,
  "dist/contracts/kernel/manifests/bb.engine_conformance_manifest.v1.schema.json",
)

mkdirSync(dirname(destination), { recursive: true })
copyFileSync(source, destination)
