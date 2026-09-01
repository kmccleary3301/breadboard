#!/usr/bin/env node

import { createBreadboardClient } from "../../sdk/ts/dist/index.js"

const DEFAULT_BASE_URL = "http://127.0.0.1:9099"
const baseUrl = process.env.BREADBOARD_BASE_URL ?? DEFAULT_BASE_URL
const authToken = process.env.BREADBOARD_API_TOKEN
const client = authToken
  ? createBreadboardClient({ baseUrl, authToken })
  : createBreadboardClient({ baseUrl })
const result = await client.healthSystem()

if (result.ok !== true) {
  throw new Error(`TypeScript SDK health check failed: ${JSON.stringify(result)}`)
}

console.log(`[sdk-typescript] ok (${baseUrl})`)
