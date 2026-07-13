import * as Ajv2020Module from "ajv/dist/2020.js"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import type { Ajv2020 as Ajv2020Instance, AnySchema, Options as Ajv2020Options } from "ajv/dist/2020.js"
import { GENERATED_SCHEMAS, GENERATED_SCHEMA_OBJECTS } from "./generated/index.js"
import type { ValidateFunction } from "ajv"
import type { SessionTranscriptV1 } from "./generated/types/bb.session_transcript.v1.js"

export * from "./generated/index.js"
export type SessionTranscriptV1Item = SessionTranscriptV1["items"][number]


export interface EngineConformanceManifestV1Row {
  engineFamily: string
  engineRef: string
  scenarioId: string
  supportTier: "draft-shape" | "draft-semantic" | "reference-engine"
  comparatorClass:
    | "shape-equal"
    | "normalized-trace-equal"
    | "model-visible-equal"
    | "workspace-side-effects-equal"
    | "projection-equal"
  evidence: string[]
  exemptions?: string[]
  notes?: string
}

export interface EngineConformanceManifestV1 {
  schemaVersion: "bb.engine_conformance_manifest.v1"
  contractVersion: string
  generatedAt?: string
  rows: EngineConformanceManifestV1Row[]
}

const MODULE_DIR = dirname(fileURLToPath(import.meta.url))

function loadManifestSchema(): AnySchema {
  const candidates = [
    join(MODULE_DIR, "../../../contracts/kernel/manifests/bb.engine_conformance_manifest.v1.schema.json"),
    join(MODULE_DIR, "../../../../contracts/kernel/manifests/bb.engine_conformance_manifest.v1.schema.json"),
  ]
  for (const candidate of candidates) {
    try {
      const parsed: unknown = JSON.parse(readFileSync(candidate, "utf8"))
      if ((parsed !== null && typeof parsed === "object") || typeof parsed === "boolean") {
        return parsed as AnySchema
      }
    } catch {
      // Try the next candidate.
    }
  }
  throw new Error("Unable to load tracked kernel manifest schema: bb.engine_conformance_manifest.v1.schema.json")
}

const Ajv2020Candidate: unknown =
  Ajv2020Module && typeof Ajv2020Module === "object" && "default" in Ajv2020Module ? Ajv2020Module.default : Ajv2020Module
const Ajv2020 = Ajv2020Candidate as new (opts?: Ajv2020Options) => Ajv2020Instance
const manifestAjv = new Ajv2020({ allErrors: true, validateFormats: false })
const engineConformanceManifestSchema = loadManifestSchema()
const engineConformanceManifestValidator = manifestAjv.compile(engineConformanceManifestSchema)

const schemaKeyByValidatorName = {
  kernelEvent: "https://breadboard.dev/contracts/kernel/schemas/bb.kernel_event.v1.schema.json",
  kernelEventV2: "https://breadboard.dev/contracts/kernel/schemas/bb.kernel_event.v2.schema.json",
  sessionTranscript: "https://breadboard.dev/contracts/kernel/schemas/bb.session_transcript.v1.schema.json",
  sessionTranscriptV2: "https://breadboard.dev/contracts/kernel/schemas/bb.session_transcript.v2.schema.json",
  toolCall: "https://breadboard.dev/contracts/kernel/schemas/bb.tool_call.v1.schema.json",
  toolCallV2: "https://breadboard.dev/contracts/kernel/schemas/bb.tool_call.v2.schema.json",
  toolSpec: "https://breadboard.dev/contracts/kernel/schemas/bb.tool_spec.v1.schema.json",
  toolSpecV2: "https://breadboard.dev/contracts/kernel/schemas/bb.tool_spec.v2.schema.json",
  toolExecutionOutcome: "https://breadboard.dev/contracts/kernel/schemas/bb.tool_execution_outcome.v1.schema.json",
  toolExecutionOutcomeV2: "https://breadboard.dev/contracts/kernel/schemas/bb.tool_execution_outcome.v2.schema.json",
  toolModelRender: "https://breadboard.dev/contracts/kernel/schemas/bb.tool_model_render.v1.schema.json",
  toolModelRenderV2: "https://breadboard.dev/contracts/kernel/schemas/bb.tool_model_render.v2.schema.json",
  coordinationSliceV2: "https://breadboard.dev/contracts/kernel/schemas/bb.coordination_slice.v2.schema.json",
  runRequest: "https://breadboard.dev/contracts/kernel/schemas/bb.run_request.v1.schema.json",
  runContext: "https://breadboard.dev/contracts/kernel/schemas/bb.run_context.v1.schema.json",
  providerExchange: "https://breadboard.dev/contracts/kernel/schemas/bb.provider_exchange.v1.schema.json",
  permission: "https://breadboard.dev/contracts/kernel/schemas/bb.permission.v1.schema.json",
  executionCapability: "https://breadboard.dev/contracts/kernel/schemas/bb.execution_capability.v1.schema.json",
  executionPlacement: "https://breadboard.dev/contracts/kernel/schemas/bb.execution_placement.v1.schema.json",
  sandboxRequest: "https://breadboard.dev/contracts/kernel/schemas/bb.sandbox_request.v1.schema.json",
  sandboxResult: "https://breadboard.dev/contracts/kernel/schemas/bb.sandbox_result.v1.schema.json",
  signal: "https://breadboard.dev/contracts/kernel/schemas/bb.signal.v1.schema.json",
  reviewVerdict: "https://breadboard.dev/contracts/kernel/schemas/bb.review_verdict.v1.schema.json",
  directive: "https://breadboard.dev/contracts/kernel/schemas/bb.directive.v1.schema.json",
  coordinationVerificationResult: "https://breadboard.dev/contracts/kernel/schemas/bb.coordination_verification_result.v1.schema.json",
  wakeSubscription: "https://breadboard.dev/contracts/kernel/schemas/bb.wake_subscription.v1.schema.json",
  distributedTaskDescriptor: "https://breadboard.dev/contracts/kernel/schemas/bb.distributed_task_descriptor.v1.schema.json",
  transcriptContinuationPatch: "https://breadboard.dev/contracts/kernel/schemas/bb.transcript_continuation_patch.v1.schema.json",
  unsupportedCase: "https://breadboard.dev/contracts/kernel/schemas/bb.unsupported_case.v1.schema.json",
  terminalSessionDescriptor: "https://breadboard.dev/contracts/kernel/schemas/bb.terminal_session_descriptor.v1.schema.json",
  terminalOutputDelta: "https://breadboard.dev/contracts/kernel/schemas/bb.terminal_output_delta.v1.schema.json",
  terminalInteraction: "https://breadboard.dev/contracts/kernel/schemas/bb.terminal_interaction.v1.schema.json",
  terminalSessionEnd: "https://breadboard.dev/contracts/kernel/schemas/bb.terminal_session_end.v1.schema.json",
  terminalRegistrySnapshot: "https://breadboard.dev/contracts/kernel/schemas/bb.terminal_registry_snapshot.v1.schema.json",
  terminalCleanupResult: "https://breadboard.dev/contracts/kernel/schemas/bb.terminal_cleanup_result.v1.schema.json",
  environmentSelector: "https://breadboard.dev/contracts/kernel/schemas/bb.environment_selector.v1.schema.json",
  environmentSelectorV2: "https://breadboard.dev/contracts/kernel/schemas/bb.environment_selector.v2.schema.json",
  toolBinding: "https://breadboard.dev/contracts/kernel/schemas/bb.tool_binding.v1.schema.json",
  toolSupportClaim: "https://breadboard.dev/contracts/kernel/schemas/bb.tool_support_claim.v1.schema.json",
  effectiveToolSurface: "https://breadboard.dev/contracts/kernel/schemas/bb.effective_tool_surface.v1.schema.json",
  replaySession: "https://breadboard.dev/contracts/kernel/schemas/bb.replay_session.v1.schema.json",
  task: "https://breadboard.dev/contracts/kernel/schemas/bb.task.v1.schema.json",
  checkpointMetadata: "https://breadboard.dev/contracts/kernel/schemas/bb.checkpoint_metadata.v1.schema.json",
} as const

type GeneratedValidatorName = keyof typeof schemaKeyByValidatorName

type KernelValidatorName = GeneratedValidatorName | "engineConformanceManifest"

type ValidatorMap = Record<GeneratedValidatorName, ValidateFunction> & {
  engineConformanceManifest: ValidateFunction
}

function generatedValidator(name: GeneratedValidatorName): ValidateFunction {
  const schemaKey = schemaKeyByValidatorName[name]
  const entry = GENERATED_SCHEMAS[schemaKey]
  if (!entry) {
    throw new Error(`Generated kernel schema missing for ${name}: ${schemaKey}`)
  }
  return entry.validate
}

export const kernelValidators = Object.fromEntries(
  (Object.keys(schemaKeyByValidatorName) as GeneratedValidatorName[]).map((name) => [name, generatedValidator(name)]),
) as ValidatorMap
kernelValidators.engineConformanceManifest = engineConformanceManifestValidator

export const kernelSchemas = {
  ...Object.fromEntries(
    (Object.keys(schemaKeyByValidatorName) as GeneratedValidatorName[]).map((name) => [name, GENERATED_SCHEMA_OBJECTS[schemaKeyByValidatorName[name]]]),
  ),
  engineConformanceManifest: engineConformanceManifestSchema,
} as Record<KernelValidatorName, object | boolean>

export function assertValid<T>(name: KernelValidatorName, value: unknown): T {
  const validate = kernelValidators[name]
  if (!validate(value)) {
    const text = (validate.errors || []).map((err: { instancePath?: string; message?: string }) => `${err.instancePath || "/"}: ${err.message}`).join("; ")
    throw new Error(`Kernel contract validation failed for ${name}: ${text}`)
  }
  return value as T
}
