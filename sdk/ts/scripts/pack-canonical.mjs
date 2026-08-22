import { createHash } from "node:crypto"
import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { join, resolve } from "node:path"
import { spawnSync } from "node:child_process"

const root = resolve(new URL("..", import.meta.url).pathname)
const out = resolve(process.argv[2] ?? join(root, "artifacts", "sdk-0.3.0"))
mkdirSync(out, { recursive: true })
const build = spawnSync("npm", ["run", "build"], { cwd: root, encoding: "utf8", stdio: "inherit" })
if (build.status !== 0) process.exit(build.status ?? 1)
const pack = spawnSync("npm", ["pack", "--json", "--ignore-scripts", "--pack-destination", out], { cwd: root, encoding: "utf8" })
if (pack.status !== 0) { process.stderr.write(pack.stderr); process.exit(pack.status ?? 1) }
const packed = JSON.parse(pack.stdout)[0]
const tarball = join(out, packed.filename)
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex")
const files = packed.files
  .map(({ path }) => ({ path, sha256: sha256(readFileSync(join(root, path))) }))
  .sort((a, b) => a.path.localeCompare(b.path))
const archiveHash = sha256(readFileSync(tarball))
writeFileSync(`${tarball}.sha256`, `${archiveHash}  ${packed.filename}\n`)
writeFileSync(`${tarball}.installed-files.json`, JSON.stringify({ package: "@breadboard/sdk", version: "0.3.0", files }, null, 2) + "\n")
writeFileSync(`${tarball}.engine-api-range`, ">=0.1.0 <0.4.0\n")
console.log(JSON.stringify({ tarball, sha256: archiveHash, installed_files: files.length, engine_api_range: ">=0.1.0 <0.4.0" }, null, 2))
