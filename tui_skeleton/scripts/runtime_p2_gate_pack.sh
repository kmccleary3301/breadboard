#!/usr/bin/env bash
set -euo pipefail

npm run test -- \
  src/repl/markdown/__tests__/stableBoundaryScanner.test.ts \
  src/repl/markdown/__tests__/jitterMetrics.test.ts \
  src/repl/markdown/__tests__/jitterGate.test.ts \
  src/repl/markdown/__tests__/streamingStressGate.test.ts \
  src/commands/repl/__tests__/providerCapabilityResolution.test.ts \
  src/commands/repl/__tests__/runtimeTransitionGate.test.ts

npm run runtime:gates:strict
npm run typecheck
