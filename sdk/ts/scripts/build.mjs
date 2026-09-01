import { rmSync } from "node:fs"
import { resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { spawnSync } from "node:child_process"

const root = fileURLToPath(new URL("..", import.meta.url))
rmSync(resolve(root, "dist"), { recursive: true, force: true })
const result = spawnSync(
  process.execPath,
  [resolve(root, "node_modules/typescript/bin/tsc"), "-p", resolve(root, "tsconfig.json")],
  { cwd: root, stdio: "inherit" },
)
process.exit(result.status ?? 1)
