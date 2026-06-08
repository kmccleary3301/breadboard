import { describe, expect, test } from 'vitest'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const require = createRequire(import.meta.url)
const { analyzeV6Run, analyzeV6Runs, discoverRunDirs } = require('../scripts/v6_meta_run_auditor.cjs')

const makeRun = (files: Record<string, string>, scenario: Record<string, unknown> = {}) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-v6-auditor-'))
  const artifactDir = path.join(dir, 'artifacts')
  fs.mkdirSync(path.join(artifactDir, 'screenshots'), { recursive: true })
  fs.mkdirSync(path.join(artifactDir, 'breadboard_artifacts'), { recursive: true })
  fs.writeFileSync(path.join(artifactDir, 'scenario.json'), JSON.stringify({ id: 'synthetic', steps: [{ kind: 'write', text: 'bb\r' }], ...scenario }))
  fs.writeFileSync(path.join(artifactDir, 'pty_raw.ansi'), files['pty_raw.ansi'] ?? '')
  fs.writeFileSync(path.join(artifactDir, 'pty_plain.txt'), files['pty_plain.txt'] ?? '')
  fs.writeFileSync(path.join(artifactDir, 'scrollback_final.txt'), files['scrollback_final.txt'] ?? '')
  fs.writeFileSync(path.join(artifactDir, 'viewport_final.txt'), files['viewport_final.txt'] ?? '❯   Type your request…')
  fs.writeFileSync(path.join(artifactDir, 'input_log.ndjson'), files['input_log.ndjson'] ?? '')
  fs.writeFileSync(path.join(artifactDir, 'breadboard_artifacts', 'repl_state.ndjson'), files['repl_state.ndjson'] ?? '')
  fs.writeFileSync(path.join(artifactDir, 'verdict.json'), files['verdict.json'] ?? JSON.stringify({ ok: true, error: null }))
  for (const [name, content] of Object.entries(files)) {
    if (name.startsWith('screenshots/')) {
      const file = path.join(artifactDir, name)
      fs.mkdirSync(path.dirname(file), { recursive: true })
      fs.writeFileSync(file, content)
    }
  }
  return dir
}

const kinds = (dir: string) => new Set(analyzeV6Run(dir).findings.map((finding: { kind: string }) => finding.kind))

describe('P14 V6 meta-run auditor', () => {
  test('discovers nested VSCode harness run directories from a root', () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-v6-root-'))
    const run = makeRun({}, { id: 'nested' })
    const nested = path.join(root, 'runs', 'vscode_harness_smoke_nested')
    fs.mkdirSync(path.dirname(nested), { recursive: true })
    fs.cpSync(run, nested, { recursive: true })
    expect(discoverRunDirs([root])).toContain(nested)
  })

  test('fails harness-red verdicts even when legacy artifact detector is green', () => {
    const dir = makeRun({ 'verdict.json': JSON.stringify({ ok: false, error: 'Timed out waiting for stable viewport frame after 15000ms' }) })
    const findingKinds = kinds(dir)
    expect(findingKinds).toContain('harness-verdict-red')
    expect(findingKinds).toContain('stable-viewport-timeout')
  })

  test('counts provider warning churn in raw and visible artifacts', () => {
    const warning = 'Provider openrouter (openai_responses) rejected streaming: {"reason":"Expected to have received `response.created` before `response.keep_alive`"}. Falling back to non-streaming.'
    const dir = makeRun({
      'pty_raw.ansi': `${warning}\n${warning}\n`,
      'screenshots/01-live.txt': warning,
    })
    const result = analyzeV6Run(dir)
    expect(result.providerWarningCount).toBeGreaterThan(0)
    expect(new Set(result.findings.map((finding: { kind: string }) => finding.kind))).toContain('provider-warning-churn')
  })

  test('enforces scenario forbidden visible text metadata', () => {
    const dir = makeRun(
      {
        'viewport_final.txt': 'Provider openrouter leaked in visible viewport\n❯   Type your request…',
      },
      { v6: { expectedFinalMode: 'repl', forbiddenVisibleText: ['Provider openrouter'] } },
    )
    expect(kinds(dir)).toContain('scenario-forbidden-visible-text')
  })

  test('fails expected final REPL mode when transcript viewer is still active', () => {
    const dir = makeRun(
      {
        'viewport_final.txt': 'breadboard transcript viewer\nNo transcript entries.',
        'input_log.ndjson': '{"text":"/copy\\r"}\n{"text":"/clear\\r"}\n',
      },
      { v6: { expectedFinalMode: 'repl' } },
    )
    const findingKinds = kinds(dir)
    expect(findingKinds).toContain('unexpected-final-ui-mode')
    expect(findingKinds).toContain('viewer-command-routing-ambiguous')
  })

  test('downgrades intentional deferred command scenarios to calibration findings', () => {
    const dir = makeRun(
      {
        'viewport_final.txt': 'Permission command surface is deferred\n❯   Type your request…',
        'screenshots/01-deferred.txt': 'Permission command surface is deferred\n',
      },
      { id: 'r2_edge_autocomplete_deferred_resize_sweep' },
    )
    const result = analyzeV6Run(dir)
    const findingKinds = new Set(result.findings.map((finding: { kind: string }) => finding.kind))
    expect(findingKinds).toContain('detector-false-positive-candidate')
    expect(result.ok).toBe(true)
  })

  test('flags scenario waits that can false-pass on setup sentinels', () => {
    const dir = makeRun(
      {},
      {
        id: 'sentinel-risk',
        steps: [
          { kind: 'write', text: 'echo SETUP_ONLY_READY\r' },
          { kind: 'waitForText', text: 'SETUP_ONLY_READY' },
        ],
      },
    )
    expect(kinds(dir)).toContain('scenario-setup-sentinel-wait-risk')
  })

  test('aggregates multiple runs and returns red when any blocking finding exists', () => {
    const red = makeRun({ 'verdict.json': JSON.stringify({ ok: false, error: 'boom' }) })
    const green = makeRun({}, { v6: { expectedFinalMode: 'repl' } })
    const report = analyzeV6Runs([red, green])
    expect(report.runCount).toBe(2)
    expect(report.ok).toBe(false)
    expect(report.byKind['harness-verdict-red']).toBe(1)
  })
})
