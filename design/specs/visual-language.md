# The visual language: one theme, the shell and the parts every screen is drawn with

## Orientation
- **What this is:** the look of v0.4, chosen by the owner on 2026-11-28 from a design canvas (the board "v0.3 · 4 Synthesis": v0.3's sidebar and top bar, refined, with the table of option "v0.3 · 3"). Rules for presentation only: which token draws what, how the shell, tables, filters, badges, buttons, links and fields look, density, dark mode and the voice of short texts. What a screen shows (its information elements) is each vertical's design step (§2).
- **Where the code is:** the tokens in `frontend/src/app/theme.css` (the only place a colour, size or shadow value is written); the raw-colour lint `frontend/scripts/raw-colours.mjs` (ESLint rule `ft/no-raw-colour`); the destructive button `frontend/src/components/app/DestructiveButton.vue`; the vendored copies in `frontend/src/components/ui/` (button, input, checkbox); the dev-only gallery `frontend/src/app/gallery/` (§13).
- **Not built yet:** the theme toggle and following an OS switch live (CAP-UI-THEME's later slice); a compact density (§10 says how it would be added); the primitives the gallery only shows as specimens (§13's table names each one's slice); the Inter font file until the owner installs it (S0170's hand-off); how screens pass HTML attributes to vendored components under the type gate (BL-0326, an owner question).
- **Decisions it applies:** ADR-0035 and its amendments (two layers; recorded patches; the look, one theme file, no user theming: amendment of 2026-11-28, S0170), ADR-0043 (same capabilities, screens may be re-imagined), ADR-0040 (the CSP: no inline styles, fonts served by the app), ADR-0024 (account types come from the backend, never from a name).
- **Invariants:** INV-18 (WCAG 2.2 AA: every pair the look draws is checked in both modes), INV-12 (presentation in components, rules elsewhere), INV-23 (the gallery's figures are invented).
- **Sources:** the owner's words are quoted in `design/journal/S0170-design-foundations.md` and ADR-0035's amendment.
- **Traps:** a raw colour anywhere but `theme.css` fails lint, and so does a faded text token (`text-muted-foreground/70`), which would undo the contrast the tests check. A new token needs a value in both `:root` and `.dark`, a `--color-*` line in `@theme inline`, and its contrast pair in `tests/unit/app/theme.test.ts`. Tailwind builds only the classes it finds written in full in the source, so a class name is never assembled from parts (`bg-badge-${type}`). The vendored destructive button's focus ring is too faint; use `DestructiveButton` (BL-0325).
- **Main tests:** `frontend/tests/unit/app/theme.test.ts` (tokens, contrast, type and density), `tests/unit/rawColours.test.ts` (the lint), `tests/unit/app/gallery.test.ts` and `galleryBuild.test.ts` (the gallery and its absence from the build), `tests/unit/components/app.test.ts` (the destructive ring).

## 1. One theme, no user theming
- `theme.css` defines the whole look as CSS variables: colours for light (`:root`) and dark (`.dark`), the type scale, spacing for the density, radii and shadows. Components use token names only (`bg-card`, `text-muted-foreground`, `shadow-card`). [ADR-0035 amendment of 2026-11-28]
- The user chooses light, dark or system; nothing else is themable: no theme files and no editor (owner: "it's a finance app after all, for serious users"). Dashboard colour themes (CAP-DASH-THEMES) colour charts only and are not this.
- The names are shadcn-vue's where it has one, so a vendored component draws with our values unchanged.

## 2. Visual language and information design are separate
- This spec is the look. It changes how things are drawn, never what a screen shows: no information element v0.3 shows is dropped without the owner (owner: "I'll need all the information elements").
- **Each vertical's brief starts with a design step** (roadmap §3.3): the owner sees two versions of its screens on a canvas, "faithful" (v0.3's information in this look) and "re-imagined" (the same capabilities, ADR-0043), and picks per screen.

## 3. Colour roles
| Role | Token | Light | Dark |
|---|---|---|---|
| Page | `background` | cool off-white | near black, blue cast |
| Text | `foreground` | gray-900 | light gray |
| Card, top bar, active segment | `card` | white | a step above the page |
| Sidebar | `sidebar` | white | between page and card |
| Current page in the sidebar | `sidebar-accent` with `sidebar-accent-foreground` | pale indigo, deep indigo text | dark indigo, light indigo text |
| Soft surface: top-bar search, segment track | `secondary`, `muted` | light slate | dark slate |
| Helper text, table headers, inactive segments | `muted-foreground` | slate gray | light slate |
| Accent: primary button, link, hovered balance link | `primary` | indigo-700 | indigo-300 |
| Error text, destructive button | `destructive` | red-700 | red-500 |
| Field edge | `input` | mid gray, 3:1 or more | mid gray, 3:1 or more |
| Focus ring (drawn at 50%) | `ring` | deep indigo | pale indigo |
| Separators (decorative) | `border`, `sidebar-border`, `hairline`, `card-outline` | light grays | dark grays |
| A balance link's dots (decorative) | `link-underline` | gray | gray |
| Account-type badges | `badge-<type>`, `badge-<type>-foreground` | pale tone, dark text | dark tone, pale text |

Text reaches 4.5:1 on every surface it is drawn on, and the field edge and the focus ring 3:1, in both modes (INV-18; the test lists each pair).

## 4. Type
- The face is **Inter** (variable), served by the app itself: no font is fetched at run time (the CSP and offline use). Until the font file is installed the system font stands in (`--font-sans`: `'Inter Variable', Inter, system-ui, …`).
- Figures are tabular everywhere, so amounts line up.
- Sizes: body 15 px; table 14 px; table header 12.5 px; secondary cells 13 px; badges 12 px; page title 26 px bold, tracking -0.02 em; the app name 20 px bold. Buttons and controls 14 px (the vendored `text-sm`).

## 5. The shell
- **Sidebar** (left, 240 px): the app name, then one link per area with a Lucide icon (20 px, stroke 1.7): Dashboards, Import, Transactions, Accounts, Budgets, Investments, Query, AI Assistant. The current area has `aria-current="page"` and the `sidebar-accent` pill; the others are plain text with an `accent` hover.
- **Top bar** (on `card`, a `border` line below): Back and Forward icon buttons, the global search on the soft surface (no border), and on the right Notifications and Theme icon buttons. Every icon-only button has an accessible name (L9-05).
- **Page header:** the page title (h1) with one line of helper text under it, and the page's main action on the right (a primary button with an icon).

## 6. Page templates
- **List page:** page header, a filter row, then one card holding the table. Accounts, Transactions and the import history use it.
- **Detail page:** page header with Back, then cards stacked with the section gap (20 px), each with a short heading.
- **Dashboard:** page header and filter row, then a grid of cards.
- **Dialogs and sheets** follow the card's look (radius 12 px, `card` surface); their shells are ADR-0035's `DialogShell` and `SheetShell`.

## 7. Tables
- A table sits in a **card defined by a faint outline**: `rounded-xl bg-card shadow-card` (a 1 px `card-outline` ring and a hint of lift), no heavy border. It scrolls sideways inside its card, never the page.
- **Headers in sentence case** ("Account", not "ACCOUNT"), `muted-foreground`, 12.5 px, medium weight.
- Rows are separated by a **hairline** (`border-t border-hairline`); cells have 11 px vertical padding (comfortable), 22 px at the table's outer edges.
- **Amounts** are right-aligned, tabular, and never wrap; a multi-currency balance shows **one line per currency**, each its own link.
- A tree indents 22 px per level, a parent has a chevron, a root row is semibold and carries its type badge.
- **Balances are links to their transactions** (owner: "the balances ARE links"). They are **neutral**: the text's own colour with a dotted `link-underline`, turning `primary` with a solid underline on hover (owner: "Balance links should be neutral"). The dots are the cue that it is a link, so the link does not rely on colour (WCAG 1.4.1).

## 8. Filters
- **Segmented period control:** a `muted` track (8 px radius, 3 px padding) with buttons; the chosen one sits on `card` with a small shadow (`shadow-segment`) and `foreground` text, the others `muted-foreground`. Each segment is a toggle button with `aria-pressed`; the group has a name ("Period"). The last segment opens more periods. The chosen range is shown beside it in helper text ("1 Jan – 28 Nov 2026").
- **Filter field:** compact (240 px), a search icon inside, on `card` with the `input` edge.
- **Selects:** labelled ("Type"), 36 px, `input` edge, on `card`. One select family is L9-08.
- **Toggles:** a checkbox with its label to the right ("Show closed").

## 9. Badges, buttons, links, fields
- **Badges:** a pill (12 px semibold) in the tone of the account's Beancount type: assets green, liabilities red, equity violet, income blue, expenses amber. The type comes from the backend (ADR-0024), never from an account's name; the badge's text is the type's name as the ledger spells it.
- **Buttons:** primary (`primary`, the page's one main action), secondary, outline, ghost (icon buttons, segments), link, destructive (`DestructiveButton`: red, with the app's focus ring, BL-0325). Controls are 8 px radius; the page's main action is 40 px high and semibold.
- **Links in text:** `primary`, underline on hover.
- **Fields:** a visible label above (14 px medium), the field, then helper text in `muted-foreground` or an error in `destructive`, linked by `aria-describedby`; an invalid field has `aria-invalid` and a red edge (L9-06).

## 10. Density
Comfortable (owner: "Density comfortable"): body 15 px, table 14 px, page padding 28 px × 32 px, section gap 20 px, cell padding 11 px. They are the `--density-*` tokens; a compact density would redefine only those (the board's compact: 14, 13.5, 20 × 28, 16, 6 px), with no component change. No compact mode is offered yet.

## 11. Dark mode
Full support from day one (owner: "We want full support for Dark mode from Day 1"). Every colour token has a dark value; nothing hard-codes a light colour. `.dark` is set before the first paint (L9-11); `color-scheme` follows, so scrollbars and native controls match.

## 12. Voice: help, empty and error text
- Short sentences in plain words, for readers whose first language is not English. Say what happened, then what to do: "The ledger could not be read. Check the file, then try again."
- Empty states say why it is empty and the next step: "No accounts match this filter. Clear the filter to see all accounts."
- Helper text is one line under its field or title. No jargon where a plain word works; a finance term gets a short example in the docs, not in the label.
- Sentence case everywhere: titles, buttons, headers, menu items.

## 13. Parts and the gallery
The gallery (`pnpm --dir frontend dev`, then `http://localhost:5173/dev/gallery`; `?theme=light` or `?theme=dark` forces a theme on that page) draws every part below with the tokens. It is left out of the production build (`galleryBuild.test.ts`). Its pieces are specimens until the slice in the last column builds the primitive.

| Part | In the gallery | Primitive and its slice |
|---|---|---|
| Sidebar, top bar | `GalleryPage.vue` | the app shell, M2 shell slice |
| Page header | `GalleryPage.vue` | `PageHeader` (ADR-0035), M2 |
| Icon button | vendored `Button size="icon"` | `IconButton`, label required (L9-05) |
| Accounts table | `AccountRow.vue` | `DataTable` (L9-15) and the accounts vertical (M4) |
| Type badge | `TypeBadge.vue` | the accounts vertical (M4) |
| Segmented period | vendored `Button` with `aria-pressed` | the first screen with a period filter (`DateRangeField`'s presets, ADR-0035) |
| Select | native `<select>` | L9-08 |
| Toggle | vendored `Checkbox` | built (S0170) |
| Destructive button | `DestructiveButton` | built (BL-0325, S0170) |
| Fields with help and error | vendored `Input` | `Field` + `useFieldErrors` (ADR-0035, L9-06) |
| Empty and error text | plain text | `EmptyState`/`ErrorState` (L9-14) |

## 14. Where the theme differs from the board
The board's values are kept except where WCAG 2.2 AA needs more:
- Field edge: the board's `#9aa1ad` (2.4:1 on the page) and dark `#596075` (2.8:1 on a card) are darkened and lightened to 3.5:1 and 3.6:1.
- Destructive: the board has none; red-700 in light (error text 6:1 on the page) and red-500 in dark.
- Focus ring: the board draws none; the app's ring is deep indigo (light) and pale indigo (dark) at 50%, 3:1 or more on every surface.
- Hovered balance link in dark: the board's `#b4c0fd` is replaced by `primary` (`#a5b4fc`), one token less, both over 8:1.
