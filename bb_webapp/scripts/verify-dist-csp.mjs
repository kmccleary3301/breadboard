import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const scriptDir = dirname(fileURLToPath(import.meta.url))
const webappRoot = resolve(scriptDir, "..")
const distIndexPath = resolve(webappRoot, "dist/index.html")

const html = readFileSync(distIndexPath, "utf-8")
const requiredDirectives = [
  "default-src 'self'",
  "script-src 'self'",
  "connect-src 'self' https:",
  "object-src 'none'",
  "frame-ancestors 'none'",
]

const missing = requiredDirectives.filter((directive) => !html.includes(directive))
if (missing.length > 0) {
  console.error("dist CSP verification failed")
  console.error(JSON.stringify({ missing, distIndexPath }, null, 2))
  process.exit(1)
}

if (html.includes("connect-src 'self' http:")) {
  console.error("dist CSP verification failed")
  console.error(JSON.stringify({ reason: "production CSP must not allow generic http connect-src", distIndexPath }, null, 2))
  process.exit(1)
}

console.log(JSON.stringify({ status: "ok", distIndexPath, requiredDirectives }, null, 2))
