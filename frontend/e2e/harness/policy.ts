// The acceptance harness's browser-side rules (S0030; review:27 §1(d); INV-24, BL-0024). Pure, so vitest pins them
// (tests/unit/e2eHarness.test.ts); fixtures.ts applies them to every page and every launcher handshake.
import path from 'node:path'

/** The owner's running app (.claude/hooks/guards.py OWNER_PORT). A page under test never reaches it. */
export const OWNER_PORT = 8001

/** Zones with a frozen-clock profile in scripts/acceptance/launcher.py: west of UTC by default, Kolkata east. */
const ZONES = ['America/Los_Angeles', 'Asia/Kolkata'] as const

export interface Run {
  backend: string
  llm: string
  root: string
  workspace: string
  tz: string
  frozenAt: string
  today: string
  version: string
}

const READY = 'FT_HARNESS_READY '
const KEYS = ['backend', 'frozenAt', 'llm', 'root', 'today', 'tz', 'version', 'workspace']

function effectivePort(u: URL): number {
  if (u.port) return Number(u.port)
  return u.protocol === 'https:' ? 443 : 80
}

/** Whether a page may request *url*: only our own origins (the preview server, the harness sidecar) and in-page
 * URLs. Any other origin, and the owner's port whatever the list says, is refused. */
export function requestAllowed(url: string, ourOrigins: readonly string[]): boolean {
  let u: URL
  try {
    u = new URL(url)
  } catch {
    return false
  }
  if (u.protocol === 'data:' || u.href === 'about:blank') return true
  if (u.protocol === 'blob:') return ourOrigins.some((o) => new URL(o).origin === u.origin)
  if (effectivePort(u) === OWNER_PORT) return false
  return ourOrigins.some((o) => new URL(o).origin === u.origin)
}

function loopbackPort(url: unknown, suffix: string, what: string): void {
  const m = typeof url === 'string' ? new RegExp(`^http://127\\.0\\.0\\.1:(\\d+)${suffix}$`).exec(url) : null
  const port = m ? Number(m[1]) : NaN
  if (!(port > 1024 && port <= 65535) || port === OWNER_PORT) {
    throw new Error(`harness: ${what} ${String(url)} is not a loopback URL on an unprivileged port of its own`)
  }
}

/** The run a launcher announced, or an error: the worker refuses anything but the launcher's own contract. */
export function parseReady(line: string): Run {
  if (!line.startsWith(READY)) throw new Error(`harness: not a ready line: ${line.slice(0, 120)}`)
  const raw: unknown = JSON.parse(line.slice(READY.length))
  if (typeof raw !== 'object' || raw === null) throw new Error('harness: ready line is not an object')
  const run = raw as Record<string, unknown>
  if (Object.keys(run).sort().join() !== KEYS.join() || !Object.values(run).every((v) => typeof v === 'string')) {
    throw new Error(`harness: ready line must hold exactly ${KEYS.join(', ')} as strings`)
  }
  const r = run as unknown as Run
  loopbackPort(r.backend, '', 'backend')
  loopbackPort(r.llm, '/v1', 'LLM stub')
  if (
    !path.isAbsolute(r.root) ||
    path.normalize(r.root) !== r.root ||
    !path.basename(r.root).startsWith('ft-acceptance-')
  ) {
    throw new Error(`harness: ${r.root} is not a harness temp root`)
  }
  if (r.workspace !== path.join(r.root, 'workspace')) throw new Error(`harness: ${r.workspace} is not in the root`)
  if (!(ZONES as readonly string[]).includes(r.tz)) throw new Error(`harness: no profile for zone ${r.tz}`)
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/.test(r.frozenAt)) {
    throw new Error(`harness: ${r.frozenAt} is not an instant with an offset`)
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(r.today)) throw new Error(`harness: ${r.today} is not a date`)
  return r
}
