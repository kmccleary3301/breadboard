import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { readFile } from "node:fs/promises"
import { dirname, join } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, "../../..")
const RECORD_PATH = join(ROOT, "docs/conformance/p30/bb-omp/harbor-license-provenance.v1.tsv")
const MANIFEST_PATH = join(ROOT, "docs/conformance/p30/bb-omp/harbor-license-provenance.v1.manifest.json")
const HEADER = [
  "matrix_sha256",
  "row_id",
  "disposition",
  "harbor_rule",
  "replacement_owner",
  "destination_paths",
  "source_basis",
  "license_treatment",
  "evidence_refs",
  "result",
]
const MATRIX_SHA256 = "e6bae987ffc0c6bbc5fad384a3eab858cc287b1e482ad188379d762e2aaad87d"
const OWNER_PROJECTION_SHA256 = "7e8518ff5e1a744530094008225bd0da5051cb9632211c2d170dfb0493404c5a"
const COMPLETED_REWRITES = new Set(["ADP-RT-005", "ADP-RT-006", "ADP-RT-007", "ADP-RT-008", "ADP-RT-018", "ADP-RT-025", "ADP-RT-027"])
const COMPLETED_DISCARDS = new Set(["ADP-RT-009", "ADP-RT-019", "ADP-RT-022", "ADP-RT-023"])
const COMPLETED = new Set([...COMPLETED_REWRITES, ...COMPLETED_DISCARDS])

const sha256 = (value) => createHash("sha256").update(value).digest("hex")
const loadRecord = async () => {
  const bytes = await readFile(RECORD_PATH)
  const lines = bytes.toString("utf8").split("\n")
  assert.equal(lines.pop(), "", "record must end with exactly one newline")
  const header = lines.shift().split("\t")
  const rows = lines.map((line) => {
    const cells = line.split("\t")
    assert.equal(cells.length, HEADER.length, `row ${cells[1] ?? "unknown"} must have ten fields`)
    return Object.fromEntries(HEADER.map((name, index) => [name, cells[index]]))
  })
  return { bytes, header, rows }
}

test("versioned harbor manifest binds the immutable 104-row template", async () => {
  const { bytes, header, rows } = await loadRecord()
  const manifest = JSON.parse(await readFile(MANIFEST_PATH, "utf8"))
  assert.deepEqual(header, HEADER)
  assert.equal(manifest.schema_version, "p30.harbor_license_provenance.v1")
  assert.equal(manifest.matrix_sha256, MATRIX_SHA256)
  assert.equal(manifest.owner_projection_schema, "p30-p24-salvage-owner-v1")
  assert.equal(manifest.owner_projection_sha256, OWNER_PROJECTION_SHA256)
  assert.equal(manifest.record_sha256, `sha256:${sha256(bytes)}`)
  assert.equal(rows.length, 104)
  assert.equal(manifest.row_count, rows.length)

  const ids = rows.map((row) => row.row_id)
  assert.equal(new Set(ids).size, 104)
  assert.equal(manifest.row_id_set_sha256, `sha256:${sha256(`${ids.join("\n")}\n`)}`)
  assert.deepEqual(
    Object.fromEntries(["port", "rewrite", "discard"].map((disposition) => [disposition, rows.filter((row) => row.disposition === disposition).length])),
    { port: 2, rewrite: 38, discard: 64 },
  )
  assert.deepEqual(
    Object.fromEntries(["BB-NEW", "REWRITE-NO-COPY", "DISCARD-NO-HARBOR", "OMP-MIT-CLEAN"].map((rule) => [rule, rows.filter((row) => row.harbor_rule === rule).length])),
    { "BB-NEW": 2, "REWRITE-NO-COPY": 38, "DISCARD-NO-HARBOR": 36, "OMP-MIT-CLEAN": 28 },
  )
  assert.deepEqual(manifest.disposition_counts, { port: 2, rewrite: 38, discard: 64 })
  assert.deepEqual(manifest.harbor_counts, { "BB-NEW": 2, "REWRITE-NO-COPY": 38, "DISCARD-NO-HARBOR": 36, "OMP-MIT-CLEAN": 28 })
  assert.equal(manifest.completed_ticket, "bb-89n.15")
  assert.equal(manifest.record_state, "template-with-bb-89n.15-subset-complete")
  assert.equal(rows.every((row) => row.matrix_sha256 === MATRIX_SHA256), true)
})

test("the eleven bb-89n.15 rows are complete and all other rows remain explicit template entries", async () => {
  const { rows } = await loadRecord()
  const manifest = JSON.parse(await readFile(MANIFEST_PATH, "utf8"))
  assert.deepEqual(manifest.completed_rows, [...COMPLETED].sort())
  assert.equal(manifest.completed_row_count, 11)
  assert.equal(manifest.pending_row_count, 93)

  for (const row of rows) {
    assert.notEqual(row.replacement_owner, "")
    assert.notEqual(row.source_basis, "")
    assert.notEqual(row.license_treatment, "")
    assert.notEqual(row.evidence_refs, "")
    assert.notEqual(row.result, "")

    if (COMPLETED_REWRITES.has(row.row_id)) {
      assert.equal(row.disposition, "rewrite")
      assert.equal(row.harbor_rule, "REWRITE-NO-COPY")
      assert.equal(row.destination_paths, "sdk/ts/src/session-evidence.ts")
      assert.equal(row.source_basis.includes("bb-89n.13 canonical E4 runtime types and bb-89n.14 runtime capture"), true)
      assert.equal(row.evidence_refs.includes("session-evidence.test.mjs"), true)
      assert.equal(row.result, "R-REDACTION-PASS")
      assert.equal([row.destination_paths, row.source_basis, row.license_treatment, row.evidence_refs, row.result].some((value) => value.startsWith("PENDING:")), false)
    } else if (COMPLETED_DISCARDS.has(row.row_id)) {
      assert.equal(row.disposition, "discard")
      assert.equal(row.harbor_rule, "DISCARD-NO-HARBOR")
      assert.equal(row.destination_paths, "")
      assert.equal(row.result, "ABSENT-NO-DEPENDENCY")
      assert.equal(row.source_basis.includes("no P24 source or fixture input"), true)
      assert.equal(row.license_treatment, "No harbor; no P24 material enters the candidate")
    } else {
      assert.equal(row.result, "PENDING")
    }
  }
})

test("record carries no prohibited ancestry, donor ownership, or discarded-schema claim", async () => {
  const { bytes } = await loadRecord()
  const text = bytes.toString("utf8")
  for (const prohibited of [
    "derived from P24",
    "P24 commit",
    "16.2.6",
    "donor-delta ownership",
    "conformance_payload",
    "readiness_claim",
    "p24_schema_id",
    "p24_source_hash",
  ]) {
    assert.equal(text.includes(prohibited), false, `prohibited record text: ${prohibited}`)
  }
})
