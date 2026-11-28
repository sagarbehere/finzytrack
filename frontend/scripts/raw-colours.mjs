// The raw-colour rule (S0170; ADR-0035 amendment of 2026-11-28): src/app/theme.css is the one place the look is
// defined, so every other file under src uses token names only. One function finds the shapes; ESLint runs it on .vue
// and .ts as `ft/no-raw-colour` (eslint.config.js), and tests/unit/rawColours.test.ts runs it on every .css file.
// Refused: a hex colour; a CSS colour function (rgb, rgba, hsl, hsla, hwb, oklch, oklab, lab, lch, color); a Tailwind
// palette class (`bg-indigo-600`, `text-gray-500`, `text-white`, `bg-black/40`, with any variant or opacity); a faded
// text token (`text-muted-foreground/70`), which would undo the contrast the theme test checks (L9-12).
// Exempt: the theme itself, and the vendored copies in src/components/ui (the CLI's bytes; they draw `text-white` on
// the destructive button).
// Stated limits (EP-30; left to review): the scan reads text, so a colour in a comment is refused too, and a
// three- or four-letter word made of hex letters after `#` (`'#add'`, `'#face'`) is taken for a colour; a colour
// function called as a method (`d3.color(...)`) and a colour built at run time (`'#' + hex`, a template literal
// with a palette name in a variable) are not seen; named CSS colours (`red`, `white` in CSS) are not refused, only
// Tailwind's `-white` and `-black` utilities; a token faded other than as text (`bg-primary/50`) is allowed, since
// surfaces at an alpha are what the vendored components draw and the theme test checks them as drawn.

const HEX = /(?<![&\w])#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})(?![\w-])/gi
const COLOUR_FUNCTION = /(?<![\w.$-])(?:rgba?|hsla?|hwb|oklch|oklab|lab|lch|color)\(/gi
const UTILITIES =
  'bg|text|border(?:-[xytrblse])?|outline|ring(?:-offset)?|inset-ring|divide|fill|stroke|from|via|to|decoration|caret|accent|shadow|inset-shadow|drop-shadow'
const PALETTES =
  'slate|gray|zinc|neutral|stone|mauve|olive|mist|taupe|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose'
const PALETTE_CLASS = new RegExp(
  `(?<![\\w-])(?:${UTILITIES})-(?:(?:${PALETTES})-(?:50|[1-9]00|950)|black|white)(?![\\w-])`,
  'g',
)
const FADED_TEXT = /(?<![\w-])text-(?:[a-z-]+-)?(?:foreground|primary|destructive)\/[\w.[\]%]+/g

/** Every raw colour in a text: its index and the matched text, in order. */
function matches(text) {
  return [HEX, COLOUR_FUNCTION, PALETTE_CLASS, FADED_TEXT]
    .flatMap((pattern) => [...text.matchAll(pattern)].map((m) => ({ index: m.index, text: m[0] })))
    .sort((x, y) => x.index - y.index)
}

/** Every raw colour in a file's text, with its 1-based line (a colour function without its parenthesis). */
export function rawColourProblems(text) {
  return matches(text).map((m) => ({
    line: text.slice(0, m.index).split('\n').length,
    text: m.text.replace(/\($/, ''),
  }))
}

/** Whether a path (relative to frontend/) may hold raw colours: the theme and the vendored copies. */
export function isExempt(path) {
  return path === 'src/app/theme.css' || path.startsWith('src/components/ui/')
}

const MESSAGE =
  'S0170: no raw colour outside src/app/theme.css; use a theme token (bg-primary, text-muted-foreground, ...)'

/** The ESLint plugin: `ft/no-raw-colour` reports every raw colour in the file's whole text. */
export const plugin = {
  rules: {
    'no-raw-colour': {
      meta: { type: 'problem', schema: [], messages: { raw: `${MESSAGE}: {{text}}` } },
      create(context) {
        return {
          Program() {
            const source = context.sourceCode
            for (const m of matches(source.text)) {
              const loc = {
                start: source.getLocFromIndex(m.index),
                end: source.getLocFromIndex(m.index + m.text.length),
              }
              context.report({ loc, messageId: 'raw', data: { text: m.text } })
            }
          },
        }
      },
    },
  },
}
