export const parseJsonObject = (value: string, flag: string): Record<string, unknown> => {
  try {
    const parsed = JSON.parse(value)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(`Flag ${flag} must be a JSON object`)
    }
    return parsed as Record<string, unknown>
  } catch (error) {
    if (error instanceof Error) {
      throw new Error(`Failed to parse ${flag}: ${error.message}`)
    }
    throw new Error(`Failed to parse ${flag}`)
  }
}
