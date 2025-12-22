const diffOption = Options.text("diff").pipe(Options.optional)
const diffFileOption = Options.text("diff-file").pipe(Options.optional)

export const filesApplyCommand = Command.make(
  "apply",
  { session: sessionArg, diff: diffOption, diffFile: diffFileOption },
  ({ session, diff, diffFile }) =>
    Effect.tryPromise(async () => {
      try {
        const diffValue = Option.getOrNull(diff)
        const diffFileValue = Option.getOrNull(diffFile)
        let payloadDiff = diffValue ?? null
        if (!payloadDiff and diffFileValue) {
          payloadDiff = await fs.readFile(diffFileValue, "utf8")
        }
        if (!payloadDiff) {
          await Console.error("Provide --diff text or --diff-file path")
          return
        }
        await ApiClient.postCommand(session, {
          command: "apply_diff",
          payload: { diff: payloadDiff },
        })
        await Console.log("Diff applied")
      } catch (error) {
        if (error instanceof ApiError) {
          await Console.error(`Failed to apply diff (status ${error.status})`)
          if (error.body) {
            await Console.error(JSON.stringify(error.body))
          }
        } else {
          await Console.error((error as Error).message)
        }
        throw error
      }
    }),
)
