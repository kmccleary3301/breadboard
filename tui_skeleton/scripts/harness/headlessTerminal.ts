import xterm from "@xterm/headless"

type XtermTerminal = InstanceType<typeof xterm.Terminal>

/**
 * Mirrors PTY output into a real terminal parser so tests can snapshot the
 * visible viewport instead of guessing from raw PTY history.
 */
export class HeadlessTerminalMirror {
  private readonly terminal: XtermTerminal
  private writeChain: Promise<void> = Promise.resolve()
  private rows: number

  constructor(cols: number, rows: number) {
    this.rows = rows
    this.terminal = new xterm.Terminal({
      cols,
      rows,
      scrollback: 100_000,
      disableStdin: true,
      allowProposedApi: true,
    })
  }

  write(data: string) {
    this.writeChain = this.writeChain.then(
      () =>
        new Promise<void>((resolve) => {
          this.terminal.write(data, () => resolve())
        }),
    )
  }

  resize(cols: number, rows: number) {
    this.rows = rows
    this.terminal.resize(cols, rows)
  }

  async flush() {
    await this.writeChain
    await new Promise<void>((resolve) => {
      this.terminal.write("", () => resolve())
    })
  }

  getViewportText(): string {
    const lines: string[] = []
    const buffer = this.terminal.buffer.active
    for (let i = 0; i < this.rows; i += 1) {
      const line = buffer.getLine(buffer.viewportY + i)
      lines.push(line ? line.translateToString(true) : "")
    }
    return lines.join("\n")
  }

  getBufferText(maxLines?: number): string {
    const lines: string[] = []
    const buffer = this.terminal.buffer.active
    const length = buffer.length
    const start =
      typeof maxLines === "number" && maxLines > 0
        ? Math.max(0, length - Math.floor(maxLines))
        : 0
    for (let i = start; i < length; i += 1) {
      const line = buffer.getLine(i)
      lines.push(line ? line.translateToString(true) : "")
    }
    return lines.join("\n")
  }
}
