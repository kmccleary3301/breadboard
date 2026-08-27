import assert from "node:assert/strict"
import test from "node:test"

import { createBreadboardClient } from "../dist/index.js"

const catalogMethods = [
  "describeSystem", "healthSystem", "schemasSystem", "createHarness", "listHarness",
  "getHarness", "updateHarness", "validateHarness", "explainHarness", "lockHarness",
  "getHarnessLock", "listIntegration", "getIntegration", "probeIntegration", "listArtifact",
  "getArtifact", "verifyArtifact", "startSession", "listSession", "getSession",
  "sendInputSession", "approveSession", "resumeSession", "cancelSession", "eventsSession",
  "artifactsSession",
] as const

test("catalog client methods are present", () => {
  const client = createBreadboardClient({ baseUrl: "http://fixture.test" })
  for (const method of catalogMethods) assert.equal(typeof client[method], "function", method)
})
