import { Console, Effect } from "effect"
import { ApiError } from "../api/client.js"
import { CliProviders } from "../providers/cliProviders.js"

const errorMessage = (error: unknown): string => {
  if (error instanceof Error && error.message.trim().length > 0) {
    return error.message
  }
  return String(error)
}

export const getCliSdk = () => CliProviders.sdk

export const getCliApi = () => getCliSdk().api()

export const getCliAppConfig = () => CliProviders.args.config

export const formatApiCommandError = (action: string, error: unknown): readonly string[] => {
  if (error instanceof ApiError) {
    const lines = [`Failed to ${action} (status ${error.status})`]
    if (error.body !== undefined) {
      lines.push(JSON.stringify(error.body))
    }
    return lines
  }
  return [errorMessage(error)]
}

export const formatApiFailure = (label: string, error: unknown): readonly string[] => {
  if (error instanceof ApiError) {
    const lines = [`${label} (${error.status}).`]
    if (error.body !== undefined) {
      lines.push(JSON.stringify(error.body))
    }
    return lines
  }
  return [`${label}: ${errorMessage(error)}`]
}

export const formatRequestFailure = (error: unknown): readonly string[] => {
  if (error instanceof ApiError) {
    const lines = [`Request failed (status ${error.status})`]
    if (error.body !== undefined) {
      lines.push(JSON.stringify(error.body))
    }
    return lines
  }
  return [errorMessage(error)]
}

export const formatCommandError = (label: string, error: unknown): readonly string[] => [
  `${label}: ${errorMessage(error)}`,
]

export const formatCommandWarning = (label: string, error: unknown): readonly string[] =>
  formatApiFailure(`Warning: ${label}`, error)


export const reportApiCommandError = async (action: string, error: unknown): Promise<void> => {
  for (const line of formatApiCommandError(action, error)) {
    await Console.error(line)
  }
}

export const reportApiFailure = async (label: string, error: unknown): Promise<void> => {
  for (const line of formatApiFailure(label, error)) {
    await Console.error(line)
  }
}

export const reportRequestFailure = async (error: unknown): Promise<void> => {
  for (const line of formatRequestFailure(error)) {
    await Console.error(line)
  }
}

export const reportCommandError = async (label: string, error: unknown): Promise<void> => {
  for (const line of formatCommandError(label, error)) {
    await Console.error(line)
  }
}

export const reportCommandWarning = async (label: string, error: unknown): Promise<void> => {
  for (const line of formatCommandWarning(label, error)) {
    await Console.error(line)
  }
}

export const reportApiCommandErrorEffect = (action: string, error: unknown) =>
  Effect.gen(function* () {
    for (const line of formatApiCommandError(action, error)) {
      yield* Console.error(line)
    }
  })

export const reportApiFailureEffect = (label: string, error: unknown) =>
  Effect.gen(function* () {
    for (const line of formatApiFailure(label, error)) {
      yield* Console.error(line)
    }
  })

export const reportRequestFailureEffect = (error: unknown) =>
  Effect.gen(function* () {
    for (const line of formatRequestFailure(error)) {
      yield* Console.error(line)
    }
  })

export const reportCommandWarningEffect = (label: string, error: unknown) =>
  Effect.gen(function* () {
    for (const line of formatCommandWarning(label, error)) {
      yield* Console.error(line)
    }
  })

export const reportCommandErrorEffect = (label: string, error: unknown) =>
  Effect.gen(function* () {
    for (const line of formatCommandError(label, error)) {
      yield* Console.error(line)
    }
  })
