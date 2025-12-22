#!/usr/bin/env node
import { access, constants, mkdir, writeFile } from "node:fs/promises"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
const projectRoot = resolve(here, "..")
const distDir = resolve(projectRoot, "dist")
const legacyMain = resolve(distDir, "legacy/main.js")
const esmOut = resolve(distDir, "old_breadboard_repl.mjs")
const cjsOut = resolve(distDir, "old_breadboard_repl.cjs")

await mkdir(distDir, { recursive: true })

try {
  await access(legacyMain, constants.F_OK)
} catch {
  throw new Error("Legacy build missing. Run `npm run build` before generating the classic REPL wrapper.")
}

const esmWrapper = `#!/usr/bin/env node
await import('./legacy/main.js');
`
const cjsWrapper = `#!/usr/bin/env node
import('./old_breadboard_repl.mjs').catch((error) => {
  console.error(error)
  process.exit(1)
});
`

await writeFile(esmOut, esmWrapper, "utf8")
await writeFile(cjsOut, cjsWrapper, "utf8")
