import { promises as fs } from "node:fs"
import path from "node:path"
import { ApiClient, ApiError } from "../api/client.js"
import type { ModelCatalogEntry } from "../api/types.js"
import { DEFAULT_MODEL_ID } from "../config/appConfig.js"

export interface ProviderModel {
  readonly id: string
  readonly provider: string
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
const MODEL_CATALOG_PATH = process.env.BREADBOARD_MODEL_CATALOG_PATH?.trim()

let cache: { timestamp: number; payload: ModelCatalogPayload } | null = null

export interface ModelCatalogPayload {
  readonly models: ProviderModel[]
  readonly defaultModel?: string | null
}

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

const resolveCatalogPath = (input: string): string => (path.isAbsolute(input) ? input : path.join(process.cwd(), input))

const normalizeFixtureEntry = (value: unknown): ProviderModel | null => {
  if (!value || typeof value !== "object") return null
  const record = value as Record<string, unknown>
  const id = typeof record.id === "string" ? record.id : typeof record.value === "string" ? record.value : null
  if (!id) return null
  const provider = typeof record.provider === "string" ? record.provider : "custom"
  const name = typeof record.name === "string" ? record.name : typeof record.label === "string" ? record.label : id
  const contextLength =
    typeof record.contextLength === "number"
      ? record.contextLength
      : typeof record.context_length === "number"
        ? record.context_length
        : undefined
  const pricing = typeof record.pricing === "string" ? record.pricing : undefined
  const priceInPerM = typeof record.priceInPerM === "number" ? record.priceInPerM : null
  const priceOutPerM = typeof record.priceOutPerM === "number" ? record.priceOutPerM : null
  const tags = Array.isArray(record.tags) ? (record.tags.filter((tag) => typeof tag === "string") as string[]) : undefined
  return {
    id,
    provider,
    name,
    contextLength,
    pricing,
    priceInPerM,
    priceOutPerM,
    tags,
  }
}

const loadFixtureCatalog = async (): Promise<ProviderModel[] | null> => {
  if (!MODEL_CATALOG_PATH) return null
  const resolved = resolveCatalogPath(MODEL_CATALOG_PATH)
  try {
    const raw = await fs.readFile(resolved, "utf8")
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.map(normalizeFixtureEntry).filter((entry): entry is ProviderModel => entry !== null)
  } catch (error) {
    if (process.env.DEBUG_CLI_MODELS === "1") {
      console.error("[models] fixture load failed", error)
    }
    return []
  }
}

const inferProvider = (modelId: string, provider?: string | null, adapter?: string | null): string => {
  if (provider && provider.trim()) return provider.trim()
  if (modelId.includes("/")) return modelId.split("/", 1)[0]
  if (adapter && adapter.trim()) return adapter.trim()
  return "custom"
}

const normalizeEngineEntry = (entry: ModelCatalogEntry): ProviderModel | null => {
  const id = typeof entry.id === "string" ? entry.id : ""
  if (!id) return null
  const provider = inferProvider(id, entry.provider ?? undefined, entry.adapter ?? undefined)
  const name = entry.name && entry.name.trim().length > 0 ? entry.name : id
  const contextLength = typeof entry.context_length === "number" ? entry.context_length : undefined
  return {
    id,
    provider,
    name,
    contextLength,
    pricing: undefined,
    priceInPerM: null,
    priceOutPerM: null,
    tags: undefined,
  }
}

const fetchEngineCatalog = async (configPath: string): Promise<ModelCatalogPayload | null> => {
  if (!configPath || !configPath.trim()) return null
  try {
    const response = await ApiClient.getModelCatalog(configPath)
    const models = response.models
      .map(normalizeEngineEntry)
      .filter((entry): entry is ProviderModel => entry !== null)
    return {
      models,
      defaultModel: response.default_model ?? null,
    }
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 501)) {
      return null
    }
    if (process.env.DEBUG_CLI_MODELS === "1") {
      console.error("[models] engine catalog failed", error)
    }
    return null
  }
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

export const getModelCatalog = async (options: { configPath?: string } = {}): Promise<ModelCatalogPayload> => {
  const fixture = await loadFixtureCatalog()
  if (fixture) {
    const payload = { models: fixture, defaultModel: DEFAULT_MODEL_ID }
    cache = { timestamp: Date.now(), payload }
    return payload
  }
  if (cache && Date.now() - cache.timestamp < CACHE_TTL_MS) {
    return cache.payload
  }
  const engine = await fetchEngineCatalog(options.configPath ?? "")
  if (engine && engine.models.length > 0) {
    cache = { timestamp: Date.now(), payload: engine }
    return engine
  }
  const [openRouter, openAI] = await Promise.all([fetchOpenRouterModels(), fetchOpenAIModels()])
  const combined = [...openRouter, ...openAI]
  combined.sort((a, b) => {
    if (a.id === DEFAULT_MODEL_ID && b.id !== DEFAULT_MODEL_ID) return -1
    if (a.id !== DEFAULT_MODEL_ID && b.id === DEFAULT_MODEL_ID) return 1
    if (a.provider !== b.provider) return a.provider.localeCompare(b.provider)
    return a.name.localeCompare(b.name)
  })
  const payload = { models: combined, defaultModel: DEFAULT_MODEL_ID }
  cache = { timestamp: Date.now(), payload }
  return payload
}
