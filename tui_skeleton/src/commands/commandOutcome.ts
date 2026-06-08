export const runCommandWithReportedError = async <T>(options: {
  readonly run: () => Promise<T>
  readonly report: (error: unknown) => Promise<void>
}): Promise<T> => {
  try {
    return await options.run()
  } catch (error) {
    await options.report(error)
    throw error
  }
}
