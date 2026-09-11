import assert from "node:assert/strict"
import test from "node:test"

import { createBreadboardClient } from "../dist/index.js"

const catalogMethods = [
  "describeSystem", "healthSystem", "schemasSystem", "createHarness", "packageHarness",
  "listHarness", "getHarness", "updateHarness", "validateHarness", "explainHarness",
  "lockHarness", "publishHarness", "getHarnessLock", "listIntegration", "getIntegration", "probeIntegration", "listArtifact",
  "getArtifact", "verifyArtifact", "startSession", "checkpointSession", "adoptSession", "compareResearch", "listSession", "getSessionResult",
  "sendInputSession", "approveSession", "resumeSession", "cancelSession", "eventsSession",
  "artifactsSession",
] as const

test("package-root client exposes exactly the public catalog", () => {
  const client = createBreadboardClient({ baseUrl: "http://fixture.test" })
  assert.deepEqual(
    Object.keys(client).sort(),
    [...catalogMethods, "invokePublicAction"].sort(),
  )
})
