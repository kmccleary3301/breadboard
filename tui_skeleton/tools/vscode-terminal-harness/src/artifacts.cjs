const fs = require('node:fs')
const path = require('node:path')

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true })
}

function writeJson(file, value) {
  ensureDir(path.dirname(file))
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`)
}

function appendJsonl(file, value) {
  ensureDir(path.dirname(file))
  fs.appendFileSync(file, `${JSON.stringify(value)}\n`)
}

function writeText(file, value) {
  ensureDir(path.dirname(file))
  fs.writeFileSync(file, value)
}

module.exports = { ensureDir, writeJson, appendJsonl, writeText }
