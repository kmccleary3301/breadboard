import test from "node:test"
import { spawnSync } from "node:child_process"
import assert from "node:assert/strict"
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { GENERATED_SCHEMAS, GENERATED_SCHEMA_IDS_BY_PACK, GENERATED_SCHEMAS_BY_PACK, PACKS } from "../src/generated/index.js"
import { GENERATED_SCHEMAS as INTERNAL_GENERATED_SCHEMAS } from "../src/generated/internal-registry.js"
import { Ajv2020 } from "ajv/dist/2020.js"
import { kernelValidators } from "../src/index.js"
import type {
  Event, ProviderExchangeV2, ProviderReplay, ProviderReplayBlock, ProviderReplayPayload,
} from "../src/generated/types/bb.provider_exchange.v2.js"

const scalarReplayPayload = {
  encrypted_content: "encrypted",
  signature: "signed",
  redacted_data: "redacted",
} satisfies ProviderReplayPayload

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

const INTERNAL_SCHEMA_FILES: Record<string, true> = {
  "bb.e4.lane_lock.v1.schema.json": true,
  "bb.e4.lane_def.v3.schema.json": true,
  "bb.e4.lane_lock.v2.schema.json": true,
  "bb.e4.lane_manifest.v1.schema.json": true,
  "bb.e4.lane_manifest.v2.schema.json": true,
}


test("generated replay payload accepts schema-valid scalar values", () => {
  assert.equal(scalarReplayPayload.encrypted_content, "encrypted")
})

type NativeToolReplay = Extract<ProviderReplayBlock, { schema_version: "openai.responses.tools.v1" }>
const nativeSearchReplay = {
  type: "provider_replay", provider_id: "openai",
  schema_version: "openai.responses.tools.v1", replay_scope: "same_provider",
  payload: { type: "tool_search_call", call_id: "search", execution: "client", arguments: { query: "tools" } },
} satisfies NativeToolReplay

function completedCallId(event: Event): string | undefined {
  if (event.kind !== "tool_call_end") return undefined
  const callId: string = event.call_id
  if (typeof event.raw_arguments === "string") {
    const raw: string = event.raw_arguments
    return `${callId}:${raw}`
  }
  if (typeof event.arguments_json === "string") return `${callId}:${event.arguments_json}`
  return undefined
}

// These compile-time regressions exercise contracts, not generated source text.
// @ts-expect-error Native search replay requires execution and arguments.
const incompleteSearch: NativeToolReplay["payload"] = { type: "tool_search_call", call_id: "search" }
// @ts-expect-error tool_call_end requires content correlation and an argument variant.
const incompleteEnd: Event = { kind: "tool_call_end", sequence: 0, call_id: "search" }
void incompleteSearch
void incompleteEnd
// @ts-expect-error Raw and canonical arguments are mutually exclusive.
const mixedEnd: Event = { kind: "tool_call_end", sequence: 1, content_index: 0, message_id: "message", call_id: "search", raw_arguments: "lexical", arguments_json: "{}", arguments: {} }
void mixedEnd
// @ts-expect-error Native tags cannot use an unchecked generic replay payload.
const disguisedNativeBlock: ProviderReplayBlock = { type: "provider_replay", provider_id: "openai", schema_version: "openai.responses.tools.v1", replay_scope: "same_provider", payload: { item_id: "x" } }
// @ts-expect-error Terminal replay has the same validated generic boundary.
const disguisedNativeReplay: ProviderReplay = { provider_id: "openai", schema_version: "openai.responses.tools.v1", replay_scope: "same_provider", payload: { item_id: "x" } }
void disguisedNativeBlock
void disguisedNativeReplay

test("provider event and native replay types retain discriminated fields", () => {
  assert.equal(completedCallId({
    kind: "tool_call_end", sequence: 1, content_index: 0, message_id: "message",
    call_id: "search", raw_arguments: "lexical",
  }), "search:lexical")
  assert.equal(nativeSearchReplay.payload.execution, "client")
})

test("provider schema retains exact native dispatch and argument exclusions", () => {
  const id = "https://breadboard.dev/contracts/kernel/schemas/bb.provider_exchange.v2.schema.json"
  const ajv = new Ajv2020({ strict: false, validateFormats: false })
  ajv.addSchema(GENERATED_SCHEMAS[id].schema)
  const replay = ajv.compile({ $ref: `${id}#/$defs/provider_replay_block` })
  const event = ajv.compile({ $ref: `${id}#/$defs/event` })
  assert.equal(replay(nativeSearchReplay), true)
  assert.equal(replay({ ...nativeSearchReplay, payload: { type: "tool_search_call", call_id: "search" } }), false)
  assert.equal(replay({ ...nativeSearchReplay, replay_scope: "diagnostic" }), false)
  assert.equal(replay({ ...nativeSearchReplay, provider_id: "anthropic" }), false)
  assert.equal(replay({ ...nativeSearchReplay, payload: { ...nativeSearchReplay.payload, extra: true } }), false)
  const end = { kind: "tool_call_end", sequence: 1, content_index: 0, message_id: "message",
    call_id: "search", raw_arguments: "lexical" }
  assert.equal(event(end), true)
  assert.equal(event({ ...end, arguments_json: "{}", arguments: {} }), false)
  assert.equal(event({ ...end, raw_arguments: undefined }), false)
})

test("canonical SDK validation admits and narrows generic replay", () => {
  const fixture: unknown = loadJson("../../contracts/kernel/examples/provider_exchange_v2_done.json")
  if (!kernelValidators.providerExchangeV2(fixture)) {
    assert.fail(JSON.stringify(kernelValidators.providerExchangeV2.errors))
  }
  const exchange: ProviderExchangeV2 = fixture
  const replays: ProviderReplayBlock[] = exchange.request.messages.flatMap(
    (message) => message.content.filter((block): block is ProviderReplayBlock => block.type === "provider_replay"),
  )
  assert.equal(replays.length, 1)
  assert.equal(replays[0].schema_version, "responses.v1")
  const request = {
    ...exchange.request,
    messages: [{
      role: "assistant", content: [{
        type: "provider_replay", provider_id: "openai",
        schema_version: "openai.responses.tools.v1", replay_scope: "same_provider",
        payload: { item_id: "x" },
      }],
    }],
  }
  assert.equal(kernelValidators.providerExchangeV2({ ...exchange, request }), false)
})

test("generated registry and type files cover every kernel schema", () => {
  const schemaFiles = readdirSync(join(process.cwd(), "../../contracts/kernel/schemas"))
    .filter((filename) => filename.endsWith(".schema.json"))
    .sort()
  assert.ok(schemaFiles.length > 0)

  for (const filename of schemaFiles) {
    const schema = loadJson(`../../contracts/kernel/schemas/${filename}`)
    const key = schemaId(schema, filename)
    const registry = INTERNAL_SCHEMA_FILES[filename] === true
      ? INTERNAL_GENERATED_SCHEMAS
      : GENERATED_SCHEMAS
    assert.ok(registry[key], `Missing generated registry entry for ${filename} (${key})`)
    assert.equal(
      GENERATED_SCHEMAS[key] !== undefined,
      INTERNAL_SCHEMA_FILES[filename] !== true,
      `Unexpected public registry disposition for ${filename} (${key})`,
    )
    assert.ok(existsSync(join(process.cwd(), "src", "generated", "types", filename.replace(/\.schema\.json$/, ".ts"))), `Missing generated type file for ${filename}`)
  }
})

test("lane manifest and lock types stay off the public package surface", () => {
  const packageJson = loadJson("package.json") as { exports: Record<string, unknown> }
  const consumerRoot = mkdtempSync(join(tmpdir(), "kernel-contracts-consumer-"))
  const packageScope = join(consumerRoot, "node_modules", "@breadboard")
  mkdirSync(packageScope, { recursive: true })
  symlinkSync(process.cwd(), join(packageScope, "kernel-contracts"), "dir")
  writeFileSync(join(consumerRoot, "package.json"), '{"type":"module"}\n', "utf8")

  try {
    for (const [typeName, filename, schemaId] of [
      [
        "E4LaneDefV3",
        "bb.e4.lane_def.v3.ts",
        "https://breadboard.dev/contracts/kernel/schemas/bb.e4.lane_def.v3.schema.json",
      ],
      [
        "E4LaneLockV2",
        "bb.e4.lane_lock.v2.ts",
        "https://breadboard.dev/contracts/kernel/schemas/bb.e4.lane_lock.v2.schema.json",
      ],
      [
        "E4LaneManifestV2",
        "bb.e4.lane_manifest.v2.ts",
        "https://breadboard.dev/contracts/kernel/schemas/bb.e4.lane_manifest.v2.schema.json",
      ],
      [
        "E4LaneLockV1",
        "bb.e4.lane_lock.v1.ts",
        "https://breadboard.dev/contracts/kernel/schemas/bb.e4.lane_lock.v1.schema.json",
      ],
      [
        "E4LaneManifestV1",
        "bb.e4.lane_manifest.v1.ts",
        "https://breadboard.dev/contracts/kernel/schemas/bb.e4.lane_manifest.v1.schema.json",
      ],
    ] as const) {
      assert.ok(existsSync(join(process.cwd(), "src", "generated", "types", filename)))
      assert.equal(GENERATED_SCHEMAS[schemaId], undefined, `Internal schema leaked from public registry: ${schemaId}`)
      assert.ok(INTERNAL_GENERATED_SCHEMAS[schemaId], `Missing internal conformance schema ${schemaId}`)
      assert.equal(
        Object.keys(packageJson.exports).some((exportPath) => exportPath.includes(filename.replace(/\.ts$/, ""))),
        false,
      )

      for (const packageSpecifier of ["@breadboard/kernel-contracts", "@breadboard/kernel-contracts/generated"]) {
        const consumerPath = join(consumerRoot, "consumer.ts")
        writeFileSync(
          consumerPath,
          `import type { ${typeName} } from ${JSON.stringify(packageSpecifier)}\nexport type PublicType = ${typeName}\n`,
          "utf8",
        )
        const compilation = spawnSync(
          process.execPath,
          [
            join(process.cwd(), "node_modules", "typescript", "bin", "tsc"),
            "--noEmit",
            "--strict",
            "--skipLibCheck",
            "--module",
            "NodeNext",
            "--moduleResolution",
            "NodeNext",
            "--target",
            "ES2022",
            consumerPath,
          ],
          { cwd: consumerRoot, encoding: "utf8" },
        )
        const diagnostics = `${compilation.stdout}${compilation.stderr}`
        assert.notEqual(
          compilation.status,
          0,
          `${typeName} unexpectedly compiled from public barrel ${packageSpecifier}`,
        )
        assert.match(
          diagnostics,
          new RegExp(`TS(?:2305|2724):.*${typeName}`),
          `Expected a missing-export diagnostic for ${typeName} from ${packageSpecifier}, got:\n${diagnostics}`,
        )
      }
    }
  } finally {
    rmSync(consumerRoot, { recursive: true, force: true })
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
    const publicSchemaFiles = entry.metadata.schemas.filter(
      (filename) => INTERNAL_SCHEMA_FILES[filename] !== true,
    )
    const expectedIds = publicSchemaFiles
      .map((filename) => schemaIdByFile.get(filename))
      .filter((schemaId): schemaId is string => schemaId !== undefined)
      .sort()
    assert.equal(expectedIds.length, publicSchemaFiles.length)
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
    const validator = (GENERATED_SCHEMAS[key] ?? INTERNAL_GENERATED_SCHEMAS[key])?.validate
    assert.ok(validator, `Missing validator for ${filename}: ${key}`)
    assert.equal(validator(example), true, `${filename} failed ${key}: ${JSON.stringify(validator.errors)}`)
  }
})

