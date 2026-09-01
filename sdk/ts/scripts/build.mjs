import { rmSync } from "node:fs"
import { resolve } from "node:path"
import { spawnSync } from "node:child_process"

const root = resolve(new URL("..", import.meta.url).pathname)
rmSync(resolve(root, "dist"), { recursive: true, force: true })
const result = spawnSync(
  process.execPath,
  [resolve(root, "node_modules/typescript/bin/tsc"), "-p", resolve(root, "tsconfig.json")],
  { cwd: root, stdio: "inherit" },
)
process.exit(result.status ?? 1)
