import { describe, it, expect } from 'vitest'
import { renderMarkdown } from '@/utils/markdown'

describe('renderMarkdown', () => {
  it('escapes HTML before applying markdown (safe for v-html)', () => {
    const html = renderMarkdown('a <script>alert(1)</script> b')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;')
  })

  it('renders bold, italic, and inline code', () => {
    const html = renderMarkdown('**bold** and *italic* and `code`')
    expect(html).toContain('<strong>bold</strong>')
    expect(html).toContain('<em>italic</em>')
    expect(html).toContain('<code')
    expect(html).toContain('>code</code>')
  })

  it('renders https links with a safe target/rel', () => {
    const html = renderMarkdown('see [docs](https://docs.finzytrack.com/x/)')
    expect(html).toContain('href="https://docs.finzytrack.com/x/"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).toContain('>docs</a>')
  })

  it('does not linkify non-http(s) schemes', () => {
    const html = renderMarkdown('[x](javascript:alert(1))')
    expect(html).not.toContain('<a ')
    expect(html).toContain('[x](javascript:alert(1))')
  })

  it('groups blank-line-separated text into paragraphs', () => {
    const html = renderMarkdown('para one\n\npara two')
    expect(html).toBe('<p>para one</p><p>para two</p>')
  })

  it('turns single newlines into line breaks within a paragraph', () => {
    const html = renderMarkdown('line one\nline two')
    expect(html).toBe('<p>line one<br/>line two</p>')
  })

  it('renders "- "/"* " runs as an unordered list', () => {
    const html = renderMarkdown('- first\n- second')
    expect(html).toContain('<ul')
    expect(html).toContain('<li>first</li>')
    expect(html).toContain('<li>second</li>')
    expect(html).not.toContain('<p>')
  })

  it('separates a paragraph from a following list', () => {
    const html = renderMarkdown('intro\n\n- a\n- b')
    expect(html).toBe('<p>intro</p><ul class="list-disc space-y-1 pl-5"><li>a</li><li>b</li></ul>')
  })

  it('returns an empty string for empty input', () => {
    expect(renderMarkdown('')).toBe('')
  })
})
