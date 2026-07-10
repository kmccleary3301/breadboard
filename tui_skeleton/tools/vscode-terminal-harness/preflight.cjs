#!/usr/bin/env node
const cp = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

function readInt(file) {
  try {
    const value = Number.parseInt(fs.readFileSync(file, 'utf8').trim(), 10)
    return Number.isFinite(value) ? value : null
  } catch {
    return null
  }
}

function commandInfo(command) {
  const which = cp.spawnSync('bash', ['-lc', `command -v ${command} || true`], { encoding: 'utf8' })
  const resolved = (which.stdout || '').trim()
  let version = []
  if (resolved) {
    const result = cp.spawnSync(command, ['--version'], { encoding: 'utf8', timeout: 10000 })
    version = String(result.stdout || result.stderr || '').trim().split(/\r?\n/).filter(Boolean).slice(0, 6)
  }
  return { command, resolved: resolved || null, version }
}

function inotifyInstances() {
  const rows = []
  let total = 0
  for (const pid of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(pid)) continue
    const fdDir = `/proc/${pid}/fd`
    let count = 0
    try {
      for (const fd of fs.readdirSync(fdDir)) {
        try {
          if (fs.readlinkSync(path.join(fdDir, fd)).includes('anon_inode:inotify')) count += 1
        } catch {}
      }
      if (count > 0) {
        total += count
        let cmdline = ''
        try {
          cmdline = fs.readFileSync(`/proc/${pid}/cmdline`).toString('utf8').replace(/\0/g, ' ').slice(0, 240)
        } catch {}
        rows.push({ pid: Number(pid), count, cmdline })
      }
    } catch {}
  }
  rows.sort((a, b) => b.count - a.count || a.pid - b.pid)
  return { totalObserved: total, processesObserved: rows.length, topProcesses: rows.slice(0, 20) }
}

function main() {
  const outDir = process.argv[2] ? path.resolve(process.argv[2]) : null
  const minWatches = Number.parseInt(process.env.BREADBOARD_VSCODE_MIN_USER_WATCHES || '524288', 10)
  const minInstances = Number.parseInt(process.env.BREADBOARD_VSCODE_MIN_USER_INSTANCES || '512', 10)
  const maxQueuedEventsMin = Number.parseInt(process.env.BREADBOARD_VSCODE_MIN_QUEUED_EVENTS || '16384', 10)
  const code = commandInfo(process.env.CODE_BIN || 'code')
  const cursor = commandInfo(process.env.CURSOR_BIN || 'cursor')
  const limits = {
    maxUserWatches: readInt('/proc/sys/fs/inotify/max_user_watches'),
    maxUserInstances: readInt('/proc/sys/fs/inotify/max_user_instances'),
    maxQueuedEvents: readInt('/proc/sys/fs/inotify/max_queued_events'),
  }
  const observed = inotifyInstances()
  const failures = []
  if (!code.resolved) failures.push({ id: 'code-missing', message: '`code` executable was not found.' })
  if (limits.maxUserWatches == null || limits.maxUserWatches < minWatches) {
    failures.push({
      id: 'max-user-watches-low',
      actual: limits.maxUserWatches,
      minimum: minWatches,
      message: `fs.inotify.max_user_watches is below the VSCode harness floor (${minWatches}).`,
    })
  }
  if (limits.maxUserInstances == null || limits.maxUserInstances < minInstances) {
    failures.push({
      id: 'max-user-instances-low',
      actual: limits.maxUserInstances,
      minimum: minInstances,
      message: `fs.inotify.max_user_instances is below the VSCode harness floor (${minInstances}).`,
    })
  }
  if (limits.maxQueuedEvents == null || limits.maxQueuedEvents < maxQueuedEventsMin) {
    failures.push({
      id: 'max-queued-events-low',
      actual: limits.maxQueuedEvents,
      minimum: maxQueuedEventsMin,
      message: `fs.inotify.max_queued_events is below the VSCode harness floor (${maxQueuedEventsMin}).`,
    })
  }
  const verdict = {
    ok: failures.length === 0,
    kind: 'vscode-host-preflight',
    checkedAt: new Date().toISOString(),
    hostname: os.hostname(),
    platform: process.platform,
    arch: process.arch,
    code,
    cursor,
    limits,
    thresholds: { minWatches, minInstances, maxQueuedEventsMin },
    observedInotify: observed,
    failures,
    recommendations: failures.length === 0 ? [] : [
      `sudo sysctl fs.inotify.max_user_instances=${Math.max(minInstances, 1024)}`,
      `sudo sysctl fs.inotify.max_user_watches=${Math.max(minWatches, 1048576)}`,
      'Rerun the VSCode lane after raising limits; missing verdict remains a harness red until verdict.json is produced.',
    ],
  }
  if (outDir) {
    fs.mkdirSync(outDir, { recursive: true })
    fs.writeFileSync(path.join(outDir, 'verdict.json'), `${JSON.stringify(verdict, null, 2)}\n`)
    fs.writeFileSync(path.join(outDir, 'summary.md'), `# VSCode Harness Preflight\n\nStatus: ${verdict.ok ? 'green' : 'red'}\n\nFailures: ${failures.length}\n\n${failures.map((failure) => `- ${failure.id}: ${failure.message}`).join('\n') || '- none'}\n`)
  } else {
    process.stdout.write(`${JSON.stringify(verdict, null, 2)}\n`)
  }
  process.exit(verdict.ok ? 0 : 2)
}

main()
