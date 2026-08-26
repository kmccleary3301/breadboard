import type { ErrorObject, ValidateFunction } from "ajv"

interface SemanticIssue {
  instancePath: string
  message: string
}

interface JsonObject {
  [key: string]: unknown
}

const PROVIDER_OUTPUT_EVENT_KINDS = new Set([
  "text_delta",
  "thinking_delta",
  "tool_call_start",
  "tool_call_delta",
  "tool_call_end",
])
const REPLAY_VALUE_FIELDS = new Set(["encrypted_content", "signature", "redacted_data"])

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function compareCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left, (value) => value.codePointAt(0) ?? 0)
  const rightPoints = Array.from(right, (value) => value.codePointAt(0) ?? 0)
  const count = Math.min(leftPoints.length, rightPoints.length)
  for (let index = 0; index < count; index += 1) {
    if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] - rightPoints[index]
  }
  return leftPoints.length - rightPoints.length
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value)
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("non-finite JSON number")
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`
  }
  if (isObject(value)) {
    return `{${Object.keys(value)
      .sort(compareCodePoints)
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`
  }
  throw new Error("unsupported JSON value")
}

function pythonFloatJson(value: number): string {
  if (!Number.isFinite(value)) throw new Error("non-finite JSON number")
  if (Object.is(value, -0)) return "-0.0"
  if (value === 0) return "0.0"
  const sign = value < 0 ? "-" : ""
  const source = Math.abs(value).toString().toLowerCase()
  let digits: string
  let exponent: number
  if (source.includes("e")) {
    const [coefficient, rawExponent] = source.split("e")
    digits = coefficient.replace(".", "")
    exponent = Number(rawExponent)
  } else {
    const point = source.indexOf(".")
    const integerLength = point === -1 ? source.length : point
    const compact = source.replace(".", "")
    const firstSignificant = compact.search(/[1-9]/)
    digits = compact.slice(firstSignificant).replace(/0+$/, "") || "0"
    exponent =
      integerLength > 1 || (integerLength === 1 && source[0] !== "0")
        ? integerLength - 1
        : -(firstSignificant - integerLength + 1)
  }
  if (exponent < -4 || exponent >= 16) {
    const fraction = digits.slice(1)
    const mantissa = fraction ? `${digits[0]}.${fraction}` : digits[0]
    const exponentSign = exponent >= 0 ? "+" : "-"
    return `${sign}${mantissa}e${exponentSign}${Math.abs(exponent).toString().padStart(2, "0")}`
  }
  const decimalPoint = exponent + 1
  if (decimalPoint <= 0) return `${sign}0.${"0".repeat(-decimalPoint)}${digits}`
  if (decimalPoint >= digits.length) {
    return `${sign}${digits}${"0".repeat(decimalPoint - digits.length)}.0`
  }
  return `${sign}${digits.slice(0, decimalPoint)}.${digits.slice(decimalPoint)}`
}

function canonicalizeJsonText(source: string): string {
  let offset = 0
  const parseString = (): { canonical: string; value: string } => {
    const start = offset
    offset += 1
    let escaped = false
    while (offset < source.length) {
      const character = source[offset]
      offset += 1
      if (escaped) {
        escaped = false
      } else if (character === "\\\\") {
        escaped = true
      } else if (character === "\"") {
        const token = source.slice(start, offset)
        const value: unknown = JSON.parse(token)
        if (typeof value !== "string" || JSON.stringify(value) !== token) {
          throw new Error("noncanonical JSON string")
        }
        return { canonical: token, value }
      }
    }
    throw new Error("unterminated JSON string")
  }
  const parseValue = (): { canonical: string; value: unknown } => {
    const character = source[offset]
    if (character === "\"") return parseString()
    if (character === "[") {
      offset += 1
      const values: unknown[] = []
      const encoded: string[] = []
      if (source[offset] === "]") {
        offset += 1
        return { canonical: "[]", value: values }
      }
      while (true) {
        const item = parseValue()
        values.push(item.value)
        encoded.push(item.canonical)
        if (source[offset] === "]") {
          offset += 1
          return { canonical: `[${encoded.join(",")}]`, value: values }
        }
        if (source[offset] !== ",") throw new Error("invalid JSON array")
        offset += 1
      }
    }
    if (character === "{") {
      offset += 1
      const value: JsonObject = {}
      const encoded: string[] = []
      let priorKey: string | null = null
      if (source[offset] === "}") {
        offset += 1
        return { canonical: "{}", value }
      }
      while (true) {
        if (source[offset] !== "\"") throw new Error("invalid JSON object key")
        const key = parseString()
        if (priorKey !== null && compareCodePoints(priorKey, key.value) >= 0) {
          throw new Error("JSON object keys must be unique and sorted")
        }
        priorKey = key.value
        if (source[offset] !== ":") throw new Error("invalid JSON object")
        offset += 1
        const item = parseValue()
        value[key.value] = item.value
        encoded.push(`${key.canonical}:${item.canonical}`)
        if (source[offset] === "}") {
          offset += 1
          return { canonical: `{${encoded.join(",")}}`, value }
        }
        if (source[offset] !== ",") throw new Error("invalid JSON object")
        offset += 1
      }
    }
    for (const [token, value] of [["null", null], ["true", true], ["false", false]] as const) {
      if (source.startsWith(token, offset)) {
        offset += token.length
        return { canonical: token, value }
      }
    }
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(source.slice(offset))
    if (!match) throw new Error("invalid JSON value")
    const token = match[0]
    offset += token.length
    const value = Number(token)
    const canonical = /[.eE]/.test(token)
      ? pythonFloatJson(value)
      : Object.is(value, -0)
        ? "0"
        : token
    if (canonical !== token) throw new Error("noncanonical JSON number")
    return { canonical, value }
  }

  const parsed = parseValue()
  if (offset !== source.length || parsed.canonical !== source) {
    throw new Error("noncanonical JSON text")
  }
  return parsed.canonical
}

function jsonEqual(left: unknown, right: unknown): boolean {
  if (left === right) return true
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((item, index) => jsonEqual(item, right[index]))
  }
  if (isObject(left) && isObject(right)) {
    const leftKeys = Object.keys(left).sort(compareCodePoints)
    const rightKeys = Object.keys(right).sort(compareCodePoints)
    return leftKeys.length === rightKeys.length
      && leftKeys.every((key, index) => key === rightKeys[index] && jsonEqual(left[key], right[key]))
  }
  return false
}

function interoperableNumberIssue(
  value: unknown,
  instancePath: string,
  active = new Set<object>(),
): SemanticIssue | null {
  if (typeof value === "number") {
    return Number.isInteger(value) && !Number.isSafeInteger(value)
      ? { instancePath, message: "contains an integer outside the interoperable JSON range" }
      : null
  }
  if (value === null || typeof value === "boolean" || typeof value === "string") return null
  if (!Array.isArray(value) && !isObject(value)) return null
  if (active.has(value)) return { instancePath, message: "contains a cycle" }
  active.add(value)
  try {
    if (Array.isArray(value)) {
      for (let index = 0; index < value.length; index += 1) {
        const issue = interoperableNumberIssue(value[index], `${instancePath}/${index}`, active)
        if (issue) return issue
      }
    } else {
      for (const [key, item] of Object.entries(value)) {
        const issue = interoperableNumberIssue(item, `${instancePath}/${key}`, active)
        if (issue) return issue
      }
    }
  } finally {
    active.delete(value)
  }
  return null
}

function canonicalArgumentsIssue(
  argumentsJson: unknown,
  parsedArguments: unknown,
  instancePath: string,
): SemanticIssue | null {
  if (typeof argumentsJson !== "string") {
    return { instancePath: `${instancePath}/arguments_json`, message: "must be canonical JSON text" }
  }
  let parsed: unknown
  try {
    canonicalizeJsonText(argumentsJson)
    parsed = JSON.parse(argumentsJson)
  } catch {
    return { instancePath: `${instancePath}/arguments_json`, message: "must use finite canonical JSON spelling" }
  }
  const encodedNumberIssue = interoperableNumberIssue(
    parsed,
    `${instancePath}/arguments_json`,
  )
  if (encodedNumberIssue) return encodedNumberIssue
  const decodedNumberIssue = interoperableNumberIssue(
    parsedArguments,
    `${instancePath}/arguments`,
  )
  if (decodedNumberIssue) return decodedNumberIssue
  if (!jsonEqual(parsedArguments, parsed)) {
    return { instancePath: `${instancePath}/arguments`, message: "must equal arguments_json" }
  }
  return null
}

function boundedJsonIssue(
  value: unknown,
  instancePath: string,
  options: { maxBytes: number; maxDepth: number; maxItems: number },
): SemanticIssue | null {
  const active = new Set<object>()
  const visit = (item: unknown, depth: number, path: string): SemanticIssue | null => {
    if (depth > options.maxDepth) return { instancePath: path, message: "exceeds maximum depth" }
    if (item === null || typeof item === "boolean" || typeof item === "number") return null
    if (typeof item === "string") {
      return Array.from(item).length <= 4096 ? null : { instancePath: path, message: "contains an oversized string" }
    }
    if (!Array.isArray(item) && !isObject(item)) {
      return { instancePath: path, message: "contains an unsupported JSON value" }
    }
    if (active.has(item)) return { instancePath: path, message: "contains a cycle" }
    active.add(item)
    try {
      if (Array.isArray(item)) {
        if (item.length > options.maxItems) return { instancePath: path, message: "contains an oversized array" }
        for (let index = 0; index < item.length; index += 1) {
          const issue = visit(item[index], depth + 1, `${path}/${index}`)
          if (issue) return issue
        }
      } else {
        const keys = Object.keys(item)
        if (keys.length > options.maxItems) return { instancePath: path, message: "contains an oversized object" }
        for (const key of keys) {
          const keyLength = Array.from(key).length
          if (keyLength === 0 || keyLength > 128) {
            return { instancePath: path, message: "contains an invalid object key" }
          }
          const issue = visit(item[key], depth + 1, `${path}/${key}`)
          if (issue) return issue
        }
      }
    } finally {
      active.delete(item)
    }
    return null
  }

  const issue = visit(value, 0, instancePath)
  if (issue) return issue
  try {
    if (new TextEncoder().encode(canonicalJson(value)).length > options.maxBytes) {
      return { instancePath, message: "exceeds maximum encoded size" }
    }
  } catch {
    return { instancePath, message: "must be finite canonical JSON" }
  }
  return null
}

function replayIssue(value: unknown, instancePath: string): SemanticIssue | null {
  if (!isObject(value) || !isObject(value.payload)) return null
  const entries = Object.entries(value.payload)
  if (entries.length === 0 || entries.every(([, item]) => item === null)) {
    return { instancePath: `${instancePath}/payload`, message: "must contain retained replay data" }
  }
  for (const [key, item] of entries) {
    if (item === null) return { instancePath: `${instancePath}/payload/${key}`, message: "null replay values must be omitted" }
    if (!REPLAY_VALUE_FIELDS.has(key)) continue
    const issue = boundedJsonIssue(item, `${instancePath}/payload/${key}`, {
      maxBytes: 4096,
      maxDepth: 4,
      maxItems: 32,
    })
    if (issue) return issue
  }
  return null
}

function contentIssue(value: unknown, instancePath: string): SemanticIssue | null {
  if (!Array.isArray(value)) return null
  for (let index = 0; index < value.length; index += 1) {
    const block = value[index]
    if (!isObject(block)) continue
    const path = `${instancePath}/${index}`
    if (block.type === "tool_call") {
      const issue = canonicalArgumentsIssue(block.arguments_json, block.arguments, path)
      if (issue) return issue
    } else if (block.type === "provider_replay") {
      const issue = replayIssue(block, path)
      if (issue) return issue
    }
  }
  return null
}

function exchangeContentIssue(exchange: JsonObject): SemanticIssue | null {
  const request = exchange.request
  if (isObject(request) && Array.isArray(request.messages)) {
    for (let index = 0; index < request.messages.length; index += 1) {
      const message = request.messages[index]
      if (!isObject(message)) continue
      const issue = contentIssue(message.content, `/request/messages/${index}/content`)
      if (issue) return issue
    }
  }
  const terminal = exchange.terminal
  if (!isObject(terminal)) return null
  if (Array.isArray(terminal.assistant_messages)) {
    for (let index = 0; index < terminal.assistant_messages.length; index += 1) {
      const message = terminal.assistant_messages[index]
      if (!isObject(message)) continue
      const issue = contentIssue(message.content, `/terminal/assistant_messages/${index}/content`)
      if (issue) return issue
    }
  }
  if (Array.isArray(terminal.provider_replay)) {
    for (let index = 0; index < terminal.provider_replay.length; index += 1) {
      const issue = replayIssue(terminal.provider_replay[index], `/terminal/provider_replay/${index}`)
      if (issue) return issue
    }
  }
  if (isObject(terminal.usage) && isObject(terminal.usage.extensions)) {
    const issue = boundedJsonIssue(terminal.usage.extensions, "/terminal/usage/extensions", {
      maxBytes: 16384,
      maxDepth: 8,
      maxItems: 64,
    })
    if (issue) return issue
  }
  return null
}

function eventLifecycleIssue(exchange: JsonObject): SemanticIssue | null {
  if (!Array.isArray(exchange.events) || !isObject(exchange.terminal)) return null
  const events = exchange.events
  const terminal = exchange.terminal
  if (events.length === 0) {
    return terminal.kind === "done"
      ? { instancePath: "/events", message: "done exchanges require response_start" }
      : null
  }
  const first = events[0]
  if (!isObject(first) || first.kind !== "response_start") {
    return { instancePath: "/events/0/kind", message: "events must begin with response_start" }
  }
  const open = new Map<string, { family: string; callId: unknown }>()
  const closed = new Set<string>()
  let observedOutput = false
  for (let index = 0; index < events.length; index += 1) {
    const event = events[index]
    if (!isObject(event)) continue
    if (event.sequence !== index) {
      return { instancePath: `/events/${index}/sequence`, message: "must be contiguous and zero-based" }
    }
    if (index === 0) continue
    if (event.kind === "response_start") {
      return { instancePath: `/events/${index}/kind`, message: "response_start may appear only once" }
    }
    const kind = String(event.kind)
    if (PROVIDER_OUTPUT_EVENT_KINDS.has(kind)) observedOutput = true
    const separator = kind.lastIndexOf("_")
    const family = kind.slice(0, separator)
    const phase = kind.slice(separator + 1)
    const key = `${String(event.content_index)}\u0000${String(event.message_id)}`
    const callId = family === "tool_call" ? event.call_id : null
    if (phase === "start") {
      if (open.has(key) || closed.has(key)) {
        return { instancePath: `/events/${index}`, message: "contains a duplicate content start" }
      }
      open.set(key, { family, callId })
      continue
    }
    const active = open.get(key)
    if (!active || active.family !== family || active.callId !== callId) {
      return { instancePath: `/events/${index}`, message: "content lifecycle is incomplete or mismatched" }
    }
    if (phase === "end") {
      if (kind === "tool_call_end") {
        const issue = canonicalArgumentsIssue(event.arguments_json, event.arguments, `/events/${index}`)
        if (issue) return issue
      }
      open.delete(key)
      closed.add(key)
    }
  }
  if (terminal.kind === "done" && open.size > 0) {
    return { instancePath: "/events", message: "done exchange contains unclosed content" }
  }
  if (terminal.kind === "done") {
    observedOutput = observedOutput || (Array.isArray(terminal.assistant_messages) && terminal.assistant_messages.length > 0)
    if (terminal.output_emitted !== observedOutput) {
      return { instancePath: "/terminal/output_emitted", message: "disagrees with recorded output" }
    }
  } else if (observedOutput && terminal.output_emitted !== true) {
    return { instancePath: "/terminal/output_emitted", message: "cannot deny recorded output" }
  }
  return null
}

export function validateProviderExchangeV2Semantics(value: unknown): SemanticIssue | null {
  if (!isObject(value)) return { instancePath: "/", message: "must be an object" }
  return exchangeContentIssue(value) ?? eventLifecycleIssue(value)
}

export function withProviderExchangeV2Semantics(structural: ValidateFunction): ValidateFunction {
  const validate = ((value: unknown): boolean => {
    if (!structural(value)) {
      validate.errors = structural.errors
      return false
    }
    const issue = validateProviderExchangeV2Semantics(value)
    validate.errors = issue
      ? ([{
          instancePath: issue.instancePath,
          schemaPath: "#/x-breadboard-provider-exchange-semantics",
          keyword: "x-breadboard-provider-exchange-semantics",
          params: {},
          message: issue.message,
        }] satisfies ErrorObject[])
      : null
    return issue === null
  }) as ValidateFunction
  Object.assign(validate, structural)
  validate.errors = null
  return validate
}
