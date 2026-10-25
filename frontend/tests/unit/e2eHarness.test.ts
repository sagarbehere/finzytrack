// The acceptance harness's browser-side rules (S0030; review:27 §1(d); INV-24, BL-0024): which requests a page under
// test may make, and which launcher handshakes the worker accepts. Each rule sits beside the cases that must not pass.
import { describe, expect, it } from 'vitest'

import { OWNER_PORT, parseReady, requestAllowed } from '../../e2e/harness/policy'

const ours = ['http://localhost:4173', 'http://127.0.0.1:51234']

describe('requests a page may make', () => {
  it('allows the preview server, the harness sidecar and in-page URLs', () => {
    for (const url of [
      'http://localhost:4173/',
      'http://localhost:4173/assets/index-abc.js',
      'http://127.0.0.1:51234/api/boot',
      'data:image/png;base64,AAAA',
      'blob:http://localhost:4173/1234',
      'about:blank',
    ]) {
      expect(requestAllowed(url, ours), url).toBe(true)
    }
  })

  it("refuses the owner's app, other ports, other hosts and other schemes", () => {
    for (const url of [
      `http://127.0.0.1:${String(OWNER_PORT)}/api/transactions`,
      `http://localhost:${String(OWNER_PORT)}/`,
      'http://127.0.0.1:51235/api/boot',
      'http://localhost:51234/api/boot', // the sidecar's port under another host name is not the sidecar's origin
      'https://localhost:4173/',
      'http://example.com/',
      'https://api.openai.com/v1/chat/completions',
      'ws://127.0.0.1:51234/',
      'blob:http://example.com/1234', // a blob URL carries its creator's origin
      'file:///etc/passwd',
      ['http://localhost:4173', 'evil.example/'].join('@'), // userinfo-shaped: the host is evil.example
      'not a url',
    ]) {
      expect(requestAllowed(url, ours), url).toBe(false)
    }
  })

  it("never allows the owner's port, even when it is listed as ours", () => {
    expect(requestAllowed(`http://127.0.0.1:${String(OWNER_PORT)}/`, [`http://127.0.0.1:${String(OWNER_PORT)}`])).toBe(
      false,
    )
  })
})

const ready = {
  backend: 'http://127.0.0.1:51234',
  llm: 'http://127.0.0.1:51300/v1',
  root: '/tmp/ft-acceptance-abc',
  workspace: '/tmp/ft-acceptance-abc/workspace',
  tz: 'America/Los_Angeles',
  frozenAt: '2026-03-31T21:30:00-07:00',
  today: '2026-03-31',
  version: '0.4.0.dev0',
}

describe('the launcher handshake', () => {
  it('accepts a run on loopback ports of its own', () => {
    expect(parseReady(`FT_HARNESS_READY ${JSON.stringify(ready)}`)).toEqual(ready)
  })

  it("refuses the owner's port, non-loopback hosts, a workspace outside the root and malformed lines", () => {
    const bad: Record<string, unknown>[] = [
      { ...ready, backend: `http://127.0.0.1:${String(OWNER_PORT)}` },
      { ...ready, llm: `http://127.0.0.1:${String(OWNER_PORT)}/v1` },
      { ...ready, backend: 'http://0.0.0.0:51234' },
      { ...ready, backend: 'http://192.168.1.5:51234' },
      { ...ready, backend: 'http://localhost:51234' },
      { ...ready, backend: 'http://127.0.0.1:80' },
      { ...ready, backend: 'http://127.0.0.1:51234/api' },
      { ...ready, workspace: '/elsewhere/workspace' },
      { ...ready, workspace: '/tmp/ft-acceptance-abc/../workspace' },
      { ...ready, root: '/tmp/somewhere' },
      { ...ready, frozenAt: '2026-03-31T21:30:00' },
      { ...ready, tz: 'UTC' },
      { ...ready, version: 3 },
    ]
    for (const run of bad) {
      expect(() => parseReady(`FT_HARNESS_READY ${JSON.stringify(run)}`), JSON.stringify(run)).toThrow()
    }
    for (const line of ['', 'FT_HARNESS_READY', 'FT_HARNESS_READY {', `READY ${JSON.stringify(ready)}`]) {
      expect(() => parseReady(line), line).toThrow()
    }
  })
})
