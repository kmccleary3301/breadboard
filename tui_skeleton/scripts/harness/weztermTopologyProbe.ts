import { spawn, type ChildProcess } from 'node:child_process'
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const execCapture = async (
  cmd: string,
  args: string[],
  env: NodeJS.ProcessEnv,
  cwd?: string,
  timeoutMs = 10_000,
): Promise<string> => {
  return await new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] })
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => {
      child.kill('SIGTERM')
      reject(new Error(`Command timed out: ${cmd} ${args.join(' ')}`))
    }, timeoutMs)
    child.stdout?.on('data', (chunk) => (stdout += String(chunk)))
    child.stderr?.on('data', (chunk) => (stderr += String(chunk)))
    child.on('error', (error) => {
      clearTimeout(timer)
      reject(error)
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      if (code === 0) resolve(stdout)
      else reject(new Error(`${cmd} exited with code ${code}: ${stderr.trim() || stdout.trim()}`))
    })
  })
}

const spawnWithLogs = (cmd: string, args: string[], env: NodeJS.ProcessEnv, cwd?: string): ChildProcess => {
  return spawn(cmd, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] })
}

const startXvfb = async (runtimeDir: string, width: number, height: number) => {
  const child = spawnWithLogs(
    'Xvfb',
    ['-displayfd', '1', '-screen', '0', `${width}x${height}x24`, '-nolisten', 'tcp', '-ac', '-noreset'],
    process.env,
    runtimeDir,
  )
  let stdout = ''
  let stderr = ''
  child.stdout?.on('data', (chunk) => (stdout += String(chunk)))
  child.stderr?.on('data', (chunk) => (stderr += String(chunk)))
  const display = await new Promise<string>((resolve, reject) => {
    const started = Date.now()
    const poll = () => {
      const line = stdout.split(/\r?\n/).find((entry) => /^\d+$/.test(entry.trim()))
      if (line) {
        resolve(`:${line.trim()}`)
        return
      }
      if (child.exitCode != null) {
        reject(new Error(`Xvfb exited before reporting display: ${stderr || stdout}`))
        return
      }
      if (Date.now() - started > 15_000) {
        reject(new Error(`Timed out waiting for Xvfb display: ${stderr || stdout}`))
        return
      }
      setTimeout(poll, 50)
    }
    poll()
  })
  return { child, display }
}

const writeConfig = async (configPath: string, socketPath: string, cols: number, rows: number) => {
  const content = [
    'return {',
    '  enable_wayland = false,',
    '  audible_bell = "Disabled",',
    '  check_for_updates = false,',
    '  automatically_reload_config = false,',
    '  adjust_window_size_when_changing_font_size = false,',
    `  initial_cols = ${cols},`,
    `  initial_rows = ${rows},`,
    '  window_padding = { left = 0, right = 0, top = 0, bottom = 0 },',
    '  window_close_confirmation = "NeverPrompt",',
    `  unix_domains = { { name = "localsock", socket_path = ${JSON.stringify(socketPath)} } },`,
    '}',
  ].join('\n')
  await fs.writeFile(configPath, `${content}\n`, 'utf8')
}

const waitForClient = async (env: NodeJS.ProcessEnv, gui: ChildProcess, guiLogPath: string) => {
  const started = Date.now()
  while (Date.now() - started < 15_000) {
    if (gui.exitCode != null) {
      const guiLog = await fs.readFile(guiLogPath, 'utf8').catch(() => '')
      throw new Error(`WezTerm GUI exited early with code ${gui.exitCode}: ${guiLog.trim() || '(no gui log)'}`)
    }
    try {
      const output = await execCapture('wezterm', ['cli', '--no-auto-start', '--prefer-mux', 'list-clients', '--format', 'json'], env, undefined, 3_000)
      const parsed = JSON.parse(output) as unknown[]
      if (Array.isArray(parsed) && parsed.length > 0) return
    } catch {}
    await sleep(100)
  }
  throw new Error('Timed out waiting for WezTerm GUI client connection')
}

const xwininfoRootTree = async (display: string) =>
  await execCapture('xwininfo', ['-display', display, '-root', '-tree'], process.env, undefined, 5_000)

const parseWeztermWindows = (tree: string) =>
  tree
    .split(/\r?\n/)
    .filter((line) => line.includes('org.wezfurlong.wezterm'))
    .map((line) => {
      const idMatch = /\b(0x[0-9a-fA-F]+)\b/.exec(line)
      return { line, x11WindowId: idMatch?.[1] ?? null }
    })

const buildCliEnv = (display: string, runtimeDir: string, socketPath: string): NodeJS.ProcessEnv => ({
  ...process.env,
  DISPLAY: display,
  XDG_RUNTIME_DIR: runtimeDir,
  WEZTERM_UNIX_SOCKET: socketPath,
})

const listPanes = async (env: NodeJS.ProcessEnv) => {
  const output = await execCapture('wezterm', ['cli', '--no-auto-start', '--prefer-mux', 'list', '--format', 'json'], env, undefined, 5_000)
  return JSON.parse(output) as unknown
}

const resizeX11Window = async (display: string, x11WindowId: string, width: number, height: number, helperPath: string) => {
  await execCapture('python', [helperPath, display, x11WindowId, String(width), String(height)], process.env, undefined, 5_000)
}

const main = async () => {
  const outDir = process.argv.includes('--out-dir') ? process.argv[process.argv.indexOf('--out-dir') + 1] : undefined
  if (!outDir) throw new Error('missing --out-dir')
  const launchMode = process.argv.includes('--mode') ? process.argv[process.argv.indexOf('--mode') + 1] : 'same-window'
  if (!['same-window', 'new-window'].includes(launchMode)) throw new Error('invalid --mode')

  const runtimeDir = path.resolve(outDir)
  await fs.mkdir(runtimeDir, { recursive: true })
  const observerRuntimeDir = path.join(runtimeDir, 'observer_runtime')
  await fs.mkdir(observerRuntimeDir, { recursive: true })
  const xdgRuntimeDir = await fs.mkdtemp(path.join(os.tmpdir(), 'bb-wezterm-probe-'))
  const socketPath = path.join(xdgRuntimeDir, 'wezterm.sock')
  const configPath = path.join(observerRuntimeDir, 'wezterm.lua')
  const guiLogPath = path.join(observerRuntimeDir, 'wezterm-gui.log')
  const muxLogPath = path.join(observerRuntimeDir, 'wezterm-mux.log')
  const helperPath = path.join(path.dirname(new URL(import.meta.url).pathname), 'x11ResizeWindow.py')
  const launchScriptPath = path.join(observerRuntimeDir, 'probe-shell.sh')
  await fs.writeFile(launchScriptPath, '#!/usr/bin/env bash\nset -euo pipefail\necho PROBE_READY\nexec /bin/bash\n', { mode: 0o755, encoding: 'utf8' })
  await writeConfig(configPath, socketPath, 120, 36)

  const report: Record<string, unknown> = { mode: launchMode, timestamps: { startedAt: Date.now() } }
  let xvfb: ChildProcess | null = null
  let gui: ChildProcess | null = null
  let mux: ChildProcess | null = null

  try {
    const xvfbInfo = await startXvfb(runtimeDir, 1600, 1000)
    xvfb = xvfbInfo.child
    const display = xvfbInfo.display
    report.display = display
    const baseEnv = buildCliEnv(display, xdgRuntimeDir, socketPath)

    const muxLog = await fs.open(muxLogPath, 'a')
    const guiLog = await fs.open(guiLogPath, 'a')
    mux = spawn('wezterm-mux-server', ['--config-file', configPath, '--daemonize'], {
      cwd: observerRuntimeDir,
      env: baseEnv,
      stdio: ['ignore', muxLog.fd, muxLog.fd],
    })
    gui = spawn('wezterm', ['--config-file', configPath, 'connect', 'localsock'], {
      cwd: observerRuntimeDir,
      env: baseEnv,
      stdio: ['ignore', guiLog.fd, guiLog.fd],
    })

    await waitForClient(baseEnv, gui, guiLogPath)
    await sleep(500)

    const beforeTree = await xwininfoRootTree(display)
    const beforeWindows = parseWeztermWindows(beforeTree)
    const beforeList = await listPanes(baseEnv).catch((error) => ({ error: String(error) }))
    report.before = { xwininfoTree: beforeTree, weztermWindows: beforeWindows, paneList: beforeList }

    let targetWindowId: string | null = null
    if (launchMode === 'same-window') {
      const paneEntries = Array.isArray(beforeList) ? (beforeList as Array<Record<string, unknown>>) : []
      const firstWindow = paneEntries[0]?.window_id
      if (typeof firstWindow !== 'number') throw new Error('No initial WezTerm window_id available for same-window spawn')
      targetWindowId = String(firstWindow)
    }

    const spawnArgs = ['cli', '--no-auto-start', '--prefer-mux', 'spawn']
    if (launchMode === 'new-window') {
      spawnArgs.push('--new-window', '--workspace', 'qc-emulator')
    } else {
      spawnArgs.push('--window-id', String(targetWindowId))
    }
    spawnArgs.push('--cwd', process.cwd(), '--', '/bin/bash', launchScriptPath)
    const spawnedPaneId = (await execCapture('wezterm', spawnArgs, baseEnv, undefined, 10_000)).trim()
    report.spawnedPaneId = spawnedPaneId
    report.spawnArgs = spawnArgs

    await sleep(750)
    const afterSpawnTree = await xwininfoRootTree(display)
    const afterSpawnWindows = parseWeztermWindows(afterSpawnTree)
    const afterSpawnList = await listPanes(baseEnv).catch((error) => ({ error: String(error) }))
    report.afterSpawn = { xwininfoTree: afterSpawnTree, weztermWindows: afterSpawnWindows, paneList: afterSpawnList }

    const x11WindowId = afterSpawnWindows[0]?.x11WindowId ?? beforeWindows[0]?.x11WindowId ?? null
    report.resolvedX11WindowId = x11WindowId
    if (x11WindowId) {
      await resizeX11Window(display, x11WindowId, 900, 550, helperPath)
      await sleep(750)
      const afterResizeTree = await xwininfoRootTree(display)
      const afterResizeWindows = parseWeztermWindows(afterResizeTree)
      const afterResizeList = await listPanes(baseEnv).catch((error) => ({ error: String(error) }))
      report.afterResize = { xwininfoTree: afterResizeTree, weztermWindows: afterResizeWindows, paneList: afterResizeList }
    } else {
      report.afterResize = { skipped: true, reason: 'no-x11-window-id' }
    }

    await fs.writeFile(path.join(runtimeDir, 'wezterm_topology_report.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8')
    await muxLog.close().catch(() => undefined)
    await guiLog.close().catch(() => undefined)
  } finally {
    report.timestamps = { ...(report.timestamps as Record<string, unknown>), finishedAt: Date.now() }
    await fs.writeFile(path.join(runtimeDir, 'wezterm_topology_report.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8').catch(() => undefined)
    for (const child of [gui, xvfb]) {
      if (child && child.exitCode == null) {
        try { child.kill('SIGTERM') } catch {}
      }
    }
    await sleep(200)
    for (const child of [gui, xvfb]) {
      if (child && child.exitCode == null) {
        try { child.kill('SIGKILL') } catch {}
      }
    }
    await fs.rm(xdgRuntimeDir, { recursive: true, force: true }).catch(() => undefined)
  }
}

void main().catch((error) => {
  console.error((error as Error).stack || String(error))
  process.exit(1)
})
