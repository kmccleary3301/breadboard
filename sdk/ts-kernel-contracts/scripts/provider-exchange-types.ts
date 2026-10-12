/** Compile-only projection of constraints unsupported by json-schema-to-typescript.
 * Runtime registries always consume the original, unchanged validation schema.
 */
interface Schema {
  $ref?: string
  type?: string
  properties?: Record<string, Schema>
  required?: string[]
  oneOf?: Schema[]
  if?: Schema
  then?: Schema
  else?: Schema
  $defs?: Record<string, Schema>
  [keyword: string]: unknown
}

function mergeObject(base: Schema, branch: Schema): Schema {
  return {
    ...base,
    ...branch,
    properties: { ...base.properties, ...branch.properties },
    required: [...new Set([...(base.required ?? []), ...(branch.required ?? [])])],
  }
}

function replayBranches(dispatch: Schema, base: Schema): Schema[] {
  if (!dispatch.if || !dispatch.then || !dispatch.else) {
    // TypeScript cannot exclude finite native tags from arbitrary strings.
    // Generic envelopes must therefore enter through the canonical validator,
    // not through unchecked object literals that can impersonate native replay.
    const blockType = base.properties?.type ? 'type: "provider_replay";' : ""
    return [{
      tsType: `{
        ${blockType}
        provider_id: string;
        schema_version: string;
        replay_scope: "same_provider" | "diagnostic";
        payload: ProviderReplayPayload;
        readonly [validatedGenericProviderReplay]: true;
      }`,
    }]
  }
  const native = mergeObject(mergeObject(base, dispatch.if), dispatch.then)
  return [native, ...replayBranches(dispatch.else, base)]
}

function argumentBranches(contract: Schema): Schema[] {
  const { oneOf, ...base } = contract
  if (!oneOf) throw new Error("tool arguments require canonical and raw variants")
  return oneOf.map((variant) => {
    const { not: _excluded, ...argumentsVariant } = variant
    const merged = mergeObject(base, argumentsVariant)
    const properties = { ...merged.properties }
    // Optional never fields preserve the schema's forbidden opposite keys.
    if (variant.required?.includes("raw_arguments")) {
      properties.arguments = { tsType: "never" }
      properties.arguments_json = { tsType: "never" }
    } else {
      properties.raw_arguments = { tsType: "never" }
    }
    return { ...merged, properties }
  })
}

export function providerExchangeTypeSchema(schema: Record<string, unknown>): Record<string, unknown> {
  // This projection is only called for the tracked provider-exchange schema.
  const projected: Schema = structuredClone(schema)
  const defs = projected.$defs
  if (!defs?.event?.oneOf || !defs.provider_replay_payload_dispatch) {
    throw new Error("provider exchange type projection requires event and replay definitions")
  }
  defs.event.oneOf = defs.event.oneOf.flatMap((event) => {
    if (event.properties?.kind?.const !== "tool_call_end") return [event]
    return argumentBranches(event)
  })
  if (!defs.tool_call_block) throw new Error("provider exchange requires tool_call_block")
  defs.tool_call_block = { oneOf: argumentBranches(defs.tool_call_block) }
  for (const name of ["provider_replay", "provider_replay_block"]) {
    const envelope = defs[name]
    if (!envelope) throw new Error(`provider exchange is missing ${name}`)
    const { $ref: _dispatchRef, ...base } = envelope
    defs[name] = { oneOf: replayBranches(defs.provider_replay_payload_dispatch, base) }
  }
  delete defs.provider_replay_payload_dispatch
  return projected
}
