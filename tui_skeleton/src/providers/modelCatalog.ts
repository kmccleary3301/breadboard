import { DEFAULT_MODEL_ID } from "../config/appConfig.js"

export interface ProviderModel {
  readonly id: string
  readonly provider: "openrouter" | "openai"
  readonly name: string
  readonly contextLength?: number
  readonly pricing?: string
  readonly priceInPerM?: number | null
  readonly priceOutPerM?: number | null
  readonly tags?: string[]
}

interface OpenRouterResponse {
  readonly data?: Array<{
    readonly id: string
    readonly name?: string
    readonly context_length?: number
    readonly pricing?: Record<string, unknown>
    readonly description?: string
  }>
}

interface OpenAIResponse {
  readonly data?: Array<{
    readonly id: string
    readonly owned_by?: string
  }>
}

const OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/models"
const OPENAI_ENDPOINT = "https://api.openai.com/v1/models"
const CACHE_TTL_MS = 5 * 60 * 1000

let cache: { timestamp: number; models: ProviderModel[] } | null = null

interface PricingSummary {
  readonly detail?: string
  readonly priceInPerM?: number | null
  readonly priceOutPerM?: number | null
}

const parsePricePerThousand = (value: unknown): number | null => {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null
  }
  if (typeof value === "string") {
    const cleaned = value.replace(/[^0-9.]/g, "")
    if (!cleaned) return null
    const numeric = Number(cleaned)
    return Number.isFinite(numeric) ? numeric : null
  }
  return null
}

const toPerMillion = (perThousand: number | null): number | null => {
  if (perThousand == null) return null
  const perMillion = perThousand * 1000
  return Math.round(perMillion * 100) / 100
}

const normalizePricing = (pricing: Record<string, unknown> | undefined): PricingSummary => {
  if (!pricing) return {}
  const prompt = pricing.prompt ?? pricing.input ?? pricing.default
  const completion = pricing.completion ?? pricing.output
  const priceInPerM = toPerMillion(parsePricePerThousand(prompt))
  const priceOutPerM = toPerMillion(parsePricePerThousand(completion))
  const parts: string[] = []
  if (priceInPerM != null) parts.push(`in $${priceInPerM.toFixed(2)}`)
  if (priceOutPerM != null) parts.push(`out $${priceOutPerM.toFixed(2)}`)
  return {
    detail: parts.length > 0 ? parts.join(", ") : undefined,
    priceInPerM,
    priceOutPerM,
  }
}

const fetchOpenRouterModels = async (): Promise<ProviderModel[]> => {
  const apiKey = process.env.OPENROUTER_API_KEY?.trim()
  if (!apiKey) return []
  try {
    const response = await fetch(OPENROUTER_ENDPOINT, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        Accept: "application/json",
        "HTTP-Referer": "https://cli.breadboard.dev",
        "X-Title": "BreadBoard CLI",
      },
    })
    if (!response.ok) {
      return []
    }
    const payload = (await response.json()) as OpenRouterResponse
    const models = payload.data ?? []
    return models
      .filter((model) => typeof model.id === "string" && model.id.length > 0)
      .map((model) => {
        const pricingSummary = normalizePricing(model.pricing as Record<string, unknown> | undefined)
        return {
          id: model.id,
          provider: "openrouter" as const,
          name: model.name ?? model.id,
          contextLength: typeof model.context_length === "number" ? model.context_length : undefined,
          pricing: pricingSummary.detail,
          priceInPerM: pricingSummary.priceInPerM,
          priceOutPerM: pricingSummary.priceOutPerM,
          tags: model.description ? [model.description] : undefined,
        }
      })
  } catch (error) {
    if (process.env.DEBUG_CLI_MODELS === "1") {
      console.error("[models] OpenRouter fetch failed", error)
    }
    return []
  }
}

const fetchOpenAIModels = async (): Promise<ProviderModel[]> => {
  const apiKey = process.env.OPENAI_API_KEY?.trim()
  if (!apiKey) return []
  try {
    const response = await fetch(OPENAI_ENDPOINT, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        Accept: "application/json",
      },
    })
    if (!response.ok) {
      return []
    }
    const payload = (await response.json()) as OpenAIResponse
    const models = payload.data ?? []
    return models
      .filter((model) => typeof model.id === "string" && (model.id.startsWith("gpt") || model.id.startsWith("o")))
      .map((model) => ({
        id: model.id,
        provider: "openai" as const,
        name: model.id,
        contextLength: undefined,
        pricing: undefined,
        priceInPerM: null,
        priceOutPerM: null,
        tags: model.owned_by ? [`owned by ${model.owned_by}`] : undefined,
      }))
  } catch (error) {
    if (process.env.DEBUG_CLI_MODELS === "1") {
      console.error("[models] OpenAI fetch failed", error)
    }
    return []
  }
}

export const getModelCatalog = async (): Promise<ProviderModel[]> => {
  if (cache && Date.now() - cache.timestamp < CACHE_TTL_MS) {
    return cache.models
  }
  const [openRouter, openAI] = await Promise.all([fetchOpenRouterModels(), fetchOpenAIModels()])
  const combined = [...openRouter, ...openAI]
  // Ensure default model is up front if available.
  combined.sort((a, b) => {
    if (a.id === DEFAULT_MODEL_ID && b.id !== DEFAULT_MODEL_ID) return -1
    if (a.id !== DEFAULT_MODEL_ID && b.id === DEFAULT_MODEL_ID) return 1
    if (a.provider !== b.provider) return a.provider.localeCompare(b.provider)
    return a.name.localeCompare(b.name)
  })
  cache = { timestamp: Date.now(), models: combined }
  return combined
}
