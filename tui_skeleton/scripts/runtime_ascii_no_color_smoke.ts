process.env.NO_COLOR = "1"
process.env.BREADBOARD_TUI_ASCII_ONLY = "1"
process.env.BREADBOARD_ASCII = "1"

const main = async (): Promise<void> => {
  const { resolveTuiConfig } = await import("../src/tui_config/load.js")
  const { ASCII_ONLY } = await import("../src/repl/components/replView/theme.js")

  const config = await resolveTuiConfig({
    workspace: ".",
    cliStrict: true,
    colorAllowed: true,
  })

  const result = {
    asciiOnlyResolved: config.display.asciiOnly,
    colorModeResolved: config.display.colorMode,
    themeAsciiOnly: ASCII_ONLY,
  }

  if (!result.asciiOnlyResolved) {
    throw new Error("Expected asciiOnly=true under BREADBOARD_TUI_ASCII_ONLY=1.")
  }
  if (result.colorModeResolved !== "none") {
    throw new Error(`Expected colorMode=none under NO_COLOR=1, got ${result.colorModeResolved}.`)
  }
  if (!result.themeAsciiOnly) {
    throw new Error("Expected theme ASCII_ONLY=true in ASCII+NO_COLOR validation.")
  }

  console.log(JSON.stringify({ ok: true, ...result }, null, 2))
}

void main()
