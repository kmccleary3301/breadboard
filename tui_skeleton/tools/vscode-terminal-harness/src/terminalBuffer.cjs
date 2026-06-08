const { Terminal } = require('@xterm/headless')

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

class TerminalBufferMirror {
  constructor(cols, rows) {
    this.cols = cols
    this.rows = rows
    this.writeChain = Promise.resolve()
    this.terminal = new Terminal({
      cols,
      rows,
      disableStdin: true,
      allowProposedApi: true,
      scrollback: 10000,
    })
  }

  write(data) {
    this.writeChain = this.writeChain.then(
      () =>
        new Promise((resolve) => {
          this.terminal.write(data, () => resolve())
        }),
    )
  }

  resize(cols, rows) {
    this.cols = cols
    this.rows = rows
    this.terminal.resize(cols, rows)
  }

  async flush() {
    await this.writeChain
    await new Promise((resolve) => {
      this.terminal.write('', () => resolve())
    })
    await sleep(0)
  }

  viewportText() {
    const lines = []
    const buffer = this.terminal.buffer.active
    for (let i = 0; i < this.rows; i += 1) {
      const line = buffer.getLine(buffer.viewportY + i)
      lines.push(line ? line.translateToString(true) : '')
    }
    return lines.join('\n')
  }

  bufferText(maxLines) {
    const lines = []
    const buffer = this.terminal.buffer.active
    const start = typeof maxLines === 'number' && maxLines > 0 ? Math.max(0, buffer.length - maxLines) : 0
    for (let i = start; i < buffer.length; i += 1) {
      const line = buffer.getLine(i)
      lines.push(line ? line.translateToString(true) : '')
    }
    return lines.join('\n')
  }

  state() {
    const buffer = this.terminal.buffer.active
    return {
      cols: this.cols,
      rows: this.rows,
      cursorX: buffer.cursorX,
      cursorY: buffer.cursorY,
      viewportY: buffer.viewportY,
      baseY: buffer.baseY,
      length: buffer.length,
    }
  }
}

module.exports = { TerminalBufferMirror }
