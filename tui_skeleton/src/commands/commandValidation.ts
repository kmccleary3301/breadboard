import { Console, Effect } from "effect"

export const validationError = (message: string): Error => new Error(message)

export const reportValidationError = async (message: string): Promise<void> => {
  await Console.error(message)
}

export const reportValidationErrorEffect = (message: string) =>
  Effect.gen(function* () {
    yield* Console.error(message)
    return yield* Effect.fail(validationError(message))
  })
