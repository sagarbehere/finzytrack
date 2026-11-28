# The privacy scan refuses the CSP's script hash in tauri.conf.json: excuse CSP hashes in the scan, or load the pre-paint script from a file?
- Asked: 2026-11-28 by the implementer of S0169-m2-ui-primitives
- blocks: S0169-m2-ui-primitives (its hand-off commit; everything else is built and green)
- Rubric item: 4 (option 2 changes ADR-0040) and "loosening any gate" (option 1 narrows the INV-23 privacy scan)
- ADRs checked: ADR-0040 and its 2026-10-25 amendment (an inline pre-paint script allowed by its hash), ADR-0035 (the pre-paint script), the privacy scan's rules (`scripts/privacy_scan.py`, INV-23). None says how a committed hash that looks random passes the scan.

## Context (plain language, a concrete example first)
The CSP now reads `script-src 'self' 'sha256-MDdh…jkdI='`. That long code is a fingerprint of the small theme script in `index.html`: the browser runs the script only if its fingerprint matches. It is public by design, since anyone can compute it from the committed script.

The pre-commit privacy scan (detect-secrets, rule `secret-entropy`) sees a 44-character random-looking string and treats it as a possible password, so the commit is refused. Elsewhere such a line carries a visible `privacy-scan: allow secret-entropy` comment; JSON has no comments, so `tauri.conf.json` cannot carry one. (I put the comment on the one Python test line that holds the CSP spec's example hash; that is the reviewed mechanism and loosens nothing.) The hooks are never skipped, so nothing can land until one of these is chosen.

## Options (cheapest first; PROCESS.md §5)
1. **Teach the scan that a CSP hash source is not a secret**, in the same way it already treats a 64-hex value under a `sha256` or `checksum` key as a hash pin: a token in exactly the CSP form `'sha256-<44 base64 characters>'` (with the quotes CSP requires) is excused; the rest of the line is still scanned. Cost: the scan accepts one more shape, so a secret disguised in exactly that form would pass; with tests for the shape and its near misses. ADR-0040 is unchanged.
2. **Load the pre-paint script from a file instead** (`frontend/public/prepaint.js`, a classic `<script src>` first in `<head>`). It still runs before the first paint, since a classic script in `<head>` blocks the page until it has run. The CSP becomes plain `script-src 'self'` with no hash at all, so there is nothing for the scan to flag, no fingerprint to keep in step with the script, and no inline script allowed anywhere. Cost: one extra local file request at start-up (from the app's own bundle, microseconds); an ADR-0040 amendment (the hash and its test become "no inline script at all"). This is the alternative noted at the brief; the owner kept ADR-0040's inline script then, before this cost was known.

## Recommendation
Option 2: it removes the problem instead of excusing it, and it makes the CSP stricter (`'self'` only) and simpler (nothing to keep in step). Option 1 is sound too if the owner prefers the inline script; it is a small, tested change to the privacy guard. Review history: none for either (new mechanism family).

## Answer (owner, verbatim, with date)
Owner, 2026-11-28, in session: "Teach the scan". The option as put to the owner: "The privacy scan accepts exactly the form `'sha256-<44 base64 characters>'`, as it already accepts a 64-hex value under a sha256/checksum key; the rest of the line is still scanned. ADR-0040 is unchanged. It loosens the INV-23 privacy guard by one shape." Built in S0169 (`scripts/privacy_scan.py`, `CSP_HASH`).
