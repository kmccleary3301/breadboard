import { buildConformanceSummary } from "../dist/ts-kernel-core/src/index.js"

const summary = buildConformanceSummary(process.env.BREADBOARD_REPO_ROOT)
process.stdout.write(`${JSON.stringify(summary)}\n`)
