import test from "node:test"
import assert from "node:assert/strict"
import { existsSync, readFileSync, readdirSync } from "node:fs"
import { join } from "node:path"

import { GENERATED_SCHEMAS, GENERATED_SCHEMA_IDS_BY_PACK, GENERATED_SCHEMAS_BY_PACK, PACKS } from "../src/generated/index.js"

function loadJson(relPath: string): Record<string, unknown> {
  return JSON.parse(readFileSync(join(process.cwd(), relPath), "utf8")) as Record<string, unknown>
}

function schemaVersion(schema: Record<string, unknown>): string | null {
  const properties = schema.properties as Record<string, unknown> | undefined
  const schemaVersionProperty = (properties?.schema_version ?? properties?.schemaVersion) as Record<string, unknown> | undefined
  return typeof schemaVersionProperty?.const === "string" ? schemaVersionProperty.const : null
}

function schemaId(schema: Record<string, unknown>, filename: string): string {
  return typeof schema.$id === "string" && schema.$id.length > 0 ? schema.$id : filename
}

function schemaKeyForExample(exampleFile: string, example: Record<string, unknown>, schemasByVersion: Map<string, string>): string {
  const version = typeof example.schema_version === "string" ? example.schema_version : typeof example.schemaVersion === "string" ? example.schemaVersion : null
  if (version !== null) {
    const key = schemasByVersion.get(version)
    assert.ok(key, `No generated schema for ${exampleFile} schema_version ${version}`)
    return key
  }
  if (exampleFile === "agent_config_surface_minimal.json") {
    return "https://breadboard.dev/contracts/kernel/schemas/bb.agent_config_surface.v1.schema.json"
  }
  if (exampleFile === "environment_selector_minimal.json") {
    return "https://breadboard.dev/contracts/kernel/schemas/bb.environment_selector.v1.schema.json"
  }
  if (exampleFile === "e4_common_usage.json") {
    return "https://breadboard.dev/contracts/kernel/schemas/bb.e4.common.v1.schema.json"
  }
  if (exampleFile === "kernel_common_usage.json") {
    return "https://breadboard.dev/contracts/kernel/schemas/bb.kernel.common.v1.schema.json"
  }
  throw new Error(`Example ${exampleFile} does not declare schema_version and has no explicit generated-schema pairing`)
}

test("generated registry and type files cover every kernel schema", () => {
  const schemaFiles = readdirSync(join(process.cwd(), "../../contracts/kernel/schemas"))
    .filter((filename) => filename.endsWith(".schema.json"))
    .sort()
  assert.ok(schemaFiles.length > 0)

  for (const filename of schemaFiles) {
    const schema = loadJson(`../../contracts/kernel/schemas/${filename}`)
    const key = schemaId(schema, filename)
    assert.ok(GENERATED_SCHEMAS[key], `Missing generated registry entry for ${filename} (${key})`)
    assert.ok(existsSync(join(process.cwd(), "src", "generated", "types", filename.replace(/\.schema\.json$/, ".ts"))), `Missing generated type file for ${filename}`)
  }
})

test("generated pack surfaces match the tracked packs manifest", () => {
  const manifest = loadJson("../../contracts/kernel/packs.v1.json") as {
    entries: Array<{ id: string; metadata: { schemas: string[] } }>
  }
  const packsById = PACKS as Record<string, { schemas: readonly string[] }>
  const schemaIdsByPack = GENERATED_SCHEMA_IDS_BY_PACK as Record<string, readonly string[]>
  const schemasByPack = GENERATED_SCHEMAS_BY_PACK as Record<string, Record<string, unknown>>
  const schemaFiles = readdirSync(join(process.cwd(), "../../contracts/kernel/schemas"))
    .filter((filename) => filename.endsWith(".schema.json"))
    .sort()
  const schemaIdByFile = new Map(
    schemaFiles.map((filename) => {
      const schema = loadJson(`../../contracts/kernel/schemas/${filename}`)
      return [filename, schemaId(schema, filename)]
    }),
  )

  const assigned = new Set<string>()
  for (const entry of manifest.entries) {
    assert.ok(packsById[entry.id], `Missing generated pack ${entry.id}`)
    const expectedIds = entry.metadata.schemas
      .map((filename) => schemaIdByFile.get(filename))
      .filter((schemaId): schemaId is string => schemaId !== undefined)
      .sort()
    assert.equal(expectedIds.length, entry.metadata.schemas.length)
    assert.deepEqual([...schemaIdsByPack[entry.id]].sort(), expectedIds)
    assert.deepEqual(Object.keys(schemasByPack[entry.id]).sort(), expectedIds)
    for (const schemaId of expectedIds) {
      assert.equal(schemasByPack[entry.id][schemaId], GENERATED_SCHEMAS[schemaId])
      assigned.add(schemaId)
    }
  }

  assert.deepEqual([...assigned].sort(), [...Object.keys(GENERATED_SCHEMAS)].sort())
})

test("generated validators accept tracked kernel examples", () => {
  const schemaFiles = readdirSync(join(process.cwd(), "../../contracts/kernel/schemas")).filter((filename) => filename.endsWith(".schema.json"))
  const schemasByVersion = new Map<string, string>()
  for (const filename of schemaFiles) {
    const schema = loadJson(`../../contracts/kernel/schemas/${filename}`)
    const version = schemaVersion(schema)
    if (version !== null) {
      schemasByVersion.set(version, schemaId(schema, filename))
    }
  }

  const exampleFiles = readdirSync(join(process.cwd(), "../../contracts/kernel/examples"))
    .filter((filename) => filename.endsWith(".json") && filename !== "examples_manifest.json")
    .sort()
  assert.ok(exampleFiles.length > 0)

  for (const filename of exampleFiles) {
    const example = loadJson(`../../contracts/kernel/examples/${filename}`)
    const key = schemaKeyForExample(filename, example, schemasByVersion)
    const validator = GENERATED_SCHEMAS[key]?.validate
    assert.ok(validator, `Missing validator for ${filename}: ${key}`)
    assert.equal(validator(example), true, `${filename} failed ${key}: ${JSON.stringify(validator.errors)}`)
  }
})

test("public kernel-contracts index does not re-declare generated schema contracts", () => {
  const source = readFileSync(join(process.cwd(), "src", "index.ts"), "utf8")
  const handwrittenExports = [...source.matchAll(/^export interface (\w+)/gm)].map((match) => match[1]).sort()
  assert.deepEqual(handwrittenExports, [
    "EngineConformanceManifestV1",
    "EngineConformanceManifestV1Row",
  ])
  assert.match(source, /export \* from "\.\/generated\/index\.js"/)
})
