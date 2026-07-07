/**
 * Minimal, safe markdown → HTML for short, author-controlled copy (upgrade
 * summaries, startup notices). Intentionally tiny and dependency-free — NOT a
 * general markdown engine.
 *
 * Safety: the input is HTML-escaped BEFORE any markdown transforms, so the
 * result is safe to bind with `v-html` even if a summary ever contains literal
 * `<`, `>`, or `&`. Only the small, fixed set of tags this module emits can
 * appear in the output.
 *
 * Supported subset: **bold**, *italic*, `inline code`, [links](https://…),
 * `- `/`* ` unordered lists, blank-line-separated paragraphs, single-newline
 * line breaks. Anything else renders as its literal (escaped) text.
 */

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** Inline transforms, applied to already-escaped text. */
function renderInline(text: string): string {
  return text
    .replace(
      /`([^`]+)`/g,
      '<code class="rounded bg-gray-100 px-1 py-0.5 text-xs font-mono dark:bg-gray-400/10">$1</code>',
    )
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer" class="font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-400">$1</a>',
    )
}

export function renderMarkdown(md: string): string {
  const lines = escapeHtml(md).split('\n')
  const blocks: string[] = []
  let para: string[] = []
  let list: string[] = []

  const flushPara = () => {
    if (para.length) {
      blocks.push(`<p>${renderInline(para.join('\n')).replace(/\n/g, '<br/>')}</p>`)
      para = []
    }
  }
  const flushList = () => {
    if (list.length) {
      const items = list.map((li) => `<li>${renderInline(li)}</li>`).join('')
      blocks.push(`<ul class="list-disc space-y-1 pl-5">${items}</ul>`)
      list = []
    }
  }

  for (const line of lines) {
    const listItem = /^\s*[-*]\s+(.*)$/.exec(line)
    if (listItem) {
      flushPara()
      list.push(listItem[1])
    } else if (line.trim() === '') {
      flushPara()
      flushList()
    } else {
      flushList()
      para.push(line)
    }
  }
  flushPara()
  flushList()
  return blocks.join('')
}
