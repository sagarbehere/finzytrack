// @vitest-environment node
// The design gallery (S0170) is a dev-server page only: it must not reach the shipped app. main.ts mounts it only
// when `import.meta.env.DEV` is true, which Vite makes false in a production build, so the branch and its chunk are
// dropped. Proved on the real config: `vite build` exactly as `pnpm build` runs it (NODE_ENV unset) emits no gallery
// code, and the same build with NODE_ENV=development (the must-not-pass control) does, so the search can see it.
import { execFile } from 'node:child_process'
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

import { afterAll, beforeAll, describe, expect, it } from 'vitest'

const root = fileURLToPath(new URL('../../..', import.meta.url))
const out = mkdtempSync(join(tmpdir(), 'ft-gallery-build-'))
// The gallery's own heading, which survives minification as a string.
const MARKER = 'Design gallery'

async function viteBuild(name: string, nodeEnv: string | undefined): Promise<string[]> {
  const env: NodeJS.ProcessEnv = { ...process.env }
  delete env['NODE_ENV']
  delete env['VITEST']
  if (nodeEnv) env['NODE_ENV'] = nodeEnv
  const dir = join(out, name)
  await promisify(execFile)(
    process.execPath,
    [join(root, 'node_modules/vite/bin/vite.js'), 'build', '--outDir', dir, '--logLevel', 'silent'],
    {
      cwd: root,
      env,
    },
  )
  return readdirSync(dir, { recursive: true, encoding: 'utf8' }).map((f) => join(dir, f))
}

const containsGallery = (files: string[]) =>
  files.some(
    (f) =>
      /gallery/i.test(f.slice(out.length)) || (/\.(js|css|html)$/.test(f) && readFileSync(f, 'utf8').includes(MARKER)),
  )

let production: string[] = []
let development: string[] = []
// Two real builds run here, side by side; the hook's time is the builds' own.
beforeAll(async () => {
  ;[production, development] = await Promise.all([
    viteBuild('production', undefined),
    viteBuild('development', 'development'),
  ])
}, 120_000)
afterAll(() => {
  rmSync(out, { recursive: true, force: true })
})

describe('[S0170] the design gallery is left out of the production build', () => {
  it('emits no gallery chunk and no gallery text in a production build', () => {
    expect(production.some((f) => f.endsWith('index.html'))).toBe(true)
    expect(containsGallery(production)).toBe(false)
  })

  it('does emit the gallery in a development build (the search can see it)', () => {
    expect(containsGallery(development)).toBe(true)
  })
})
