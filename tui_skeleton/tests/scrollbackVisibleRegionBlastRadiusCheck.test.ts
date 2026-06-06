import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { evaluateScrollbackVisibleRegionBlastRadius } from '../tools/assertions/scrollbackVisibleRegionBlastRadiusCheck.js'

const makeCase = async (files: Record<string, string>): Promise<string> => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'bb-wezterm-visible-check-'))
  const observer = path.join(dir, 'observer_text')
  const terminalText = path.join(dir, 'terminal_text')
  await mkdir(observer, { recursive: true })
  await mkdir(terminalText, { recursive: true })
  await writeFile(path.join(dir, 'app_start_anchor.txt'), '{"mode":"preserved-scrollback","landingLifecycleState":"fresh-visible","landingLifecycleReason":"compact-landing-fits-active-region"}\n', 'utf8')
  await writeFile(
    path.join(dir, 'surface_model.ndjson'),
    '{"event":"surface_model","landingLifecycleState":"fresh-visible","landingLifecycleReason":"compact-landing-fits-active-region","landingLifecycleVisibleInline":true,"landingLifecycleCommittedSnapshot":false,"pendingResponse":false,"transcriptCommittedCount":0,"transcriptTailCount":0}\n',
    'utf8',
  )
  await writeFile(
    path.join(observer, 'startup.txt'),
    'BreadBoard v0.0.0\nTips for getting started\nUsing Config\n❯ Try "refactor <filepath>"\n• [ready]\n',
    'utf8',
  )
  for (const [name, body] of Object.entries(files)) {
    const target = name.startsWith('terminal_text/')
      ? path.join(dir, name)
      : path.join(observer, name)
    await writeFile(target, body, 'utf8')
  }
  return dir
}

describe('scrollbackVisibleRegionBlastRadiusCheck', () => {
  it('passes when visible pane and final scrollback have single turn copies', async () => {
    const dir = await makeCase({
      'turn1-settled.txt': 'hello from wezterm turn one\nProceed to build and test.\n',
      'after-width-shrink.txt': 'hello from wezterm turn one\nProceed to build and test.\nready\nline4\nline5\nline6\nline7\nline8\n',
      'turn2-settled.txt': 'hello from wezterm turn one\nhello from wezterm turn two\n',
      'terminal_text/scrollback_final.txt': 'hello from wezterm turn one\nhello from wezterm turn two\n',
    })

    await expect(evaluateScrollbackVisibleRegionBlastRadius(dir)).resolves.toEqual([])
  })

  it('separates visible duplicates from scrollback-history duplicates', async () => {
    const dir = await makeCase({
      'turn1-settled.txt': 'hello from wezterm turn one\nProceed to build and test.\n',
      'after-width-shrink.txt': 'hello from wezterm turn one\nhello from wezterm turn one\nready\nline4\nline5\nline6\nline7\nline8\n',
      'turn2-settled.txt': 'hello from wezterm turn two\n',
      'terminal_text/scrollback_final.txt': 'hello from wezterm turn one\nhello from wezterm turn two\nhello from wezterm turn two\n',
    })

    const anomalies = await evaluateScrollbackVisibleRegionBlastRadius(dir)
    expect(anomalies.map((entry) => entry.id)).toEqual([
      'after-shrink-visible-duplicate-hello-from-wezterm-turn-one',
      'after-shrink-scrollback-duplicate-hello-from-wezterm-turn-two',
    ])
  })
})
