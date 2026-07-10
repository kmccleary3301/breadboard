const fs = require('node:fs')
const net = require('node:net')
const pty = require('node-pty')

const socketPath = process.argv[2]
if (!socketPath) {
  console.error('usage: node ptyBridgeServer.cjs <socket-path>')
  process.exit(2)
}

try { fs.unlinkSync(socketPath) } catch {}

let ptyProcess = null
let client = null

function send(message) {
  if (!client || client.destroyed) return
  client.write(`${JSON.stringify(message)}\n`)
}

function startPty(options) {
  if (ptyProcess) return
  ptyProcess = pty.spawn(options.shell || 'bash', options.args || ['--noprofile', '--norc'], {
    name: 'xterm-256color',
    cols: options.cols || 100,
    rows: options.rows || 30,
    cwd: options.cwd || process.cwd(),
    env: { ...process.env, ...(options.env || {}) },
  })
  send({ event: 'started', pid: ptyProcess.pid })
  ptyProcess.onData((data) => send({ event: 'data', data }))
  ptyProcess.onExit(({ exitCode, signal }) => {
    send({ event: 'exit', code: exitCode, signal })
    process.exit(typeof exitCode === 'number' ? exitCode : 0)
  })
}

function handleMessage(message) {
  if (message.kind === 'start') {
    startPty(message)
  } else if (message.kind === 'input') {
    ptyProcess?.write(message.text || '')
  } else if (message.kind === 'resize') {
    ptyProcess?.resize(message.cols, message.rows)
  } else if (message.kind === 'kill') {
    try { ptyProcess?.kill('SIGTERM') } catch {}
    process.exit(0)
  }
}

const server = net.createServer((socket) => {
  client = socket
  send({ event: 'ready' })
  let buffer = ''
  socket.on('data', (chunk) => {
    buffer += chunk.toString('utf8')
    let newline
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline)
      buffer = buffer.slice(newline + 1)
      if (!line.trim()) continue
      try {
        handleMessage(JSON.parse(line))
      } catch (error) {
        send({ event: 'error', message: error.message })
      }
    }
  })
  socket.on('close', () => {
    try { ptyProcess?.kill('SIGTERM') } catch {}
  })
})

server.listen(socketPath, () => {
  process.stdout.write(`ready ${socketPath}\n`)
})

process.on('SIGTERM', () => {
  try { ptyProcess?.kill('SIGTERM') } catch {}
  try { fs.unlinkSync(socketPath) } catch {}
  process.exit(0)
})

process.on('exit', () => {
  try { fs.unlinkSync(socketPath) } catch {}
})
