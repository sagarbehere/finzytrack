# S0016: detect-secrets in the privacy scan (BL-0035)

## Brief
- Goal: the T0/T1 privacy scanner also runs detect-secrets 1.5.0 (vendor key formats, secret keywords, high-entropy strings) on the same staged or tracked blobs, from a pinned environment, with network verification off, failing closed when the tool cannot run.
- Implements: BL-0035; INV-23; ARCHITECTURE.md §10 pin (detect-secrets 1.5.0); EP-24 (its own `uv.lock`, `exclude-newer = "14 days"`).
- Capabilities: none. Invariants touched: INV-23 (tightening).
- Risk class / lane: Medium / full (a privacy gate; tightening only). One verifier round unless a High.
- How it could be loosened or bypassed (written before coding): 1. the tool missing or failing → scan passes silently (must fail, exit 2); 2. detect-secrets' default network verification sends a found secret to the vendor (egress; must be off); 3. the hash-pin filter swallowing real secrets (only a 64-hex value under a `sha256` key); 4. staged content differing from disk (scan the staged blobs, written under their own names); 5. the suppression mark (same visible `privacy-scan: allow secret-entropy` as every rule; detect-secrets' own `pragma: allowlist secret` also works and is equally visible); 6. the rule not wired into `run()`.
- Acceptance tests: `scripts/tests/test_privacy_scan.py::SecretEntropy`.
- Expected doc impact: `privacy_scan.py` doc; PROCESS.md §8 (scanner rules); ARCHITECTURE.md §10 row; backlog; roadmap; ci.yml (uv before the scan).
- Open questions: none.
- Base SHA: 8ba75f0.

## Decisions
- detect-secrets runs from its own environment (`scripts/secrets-scan/`), like the oracle harness, so the stdlib scanner keeps running on the system Python (agent).
- On this repo it finds 4 things: three sha256 pins in `shell/sidecar/pbs.lock.json` (now recognised as pins) and one fake password in a test (marked) (agent).
- Pre-commit cost: about 1.5 s for `--all` (230 files), well under the 30 s T0 budget (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks with must-not-pass tests: an AWS key id, a high-entropy base64 string, a 64-hex value under another key, and a 63-hex value under `sha256` are flagged; tool missing, non-zero exit and unreadable output raise; `main` exits 2 on a tool failure; `--no-verify` is in the command.
- Self-mutation: 6 tried, 6 killed.

## Verifier
- Round 1, head 0a27345: FAIL (risk raised to High). Attacks: 12 (tool failures; network egress with sockets blocked; wiring; option injection by file name; paths escaping the temp folder; name-based skips; line counting; the pin filter; mutants; direct runs of the test file; the lock's age span; CI order).
- F1 High: blobs were written under their own paths, so an absolute or `../` path wrote outside the temp folder and could overwrite the scanned file. Fixed (EP-30): every blob is written under a generated name (`blob00000.txt`) and mapped back.
- F2 High: a file named like an option (`--exclude-files=.*`) switched the rule off. Fixed: generated names and `--` before them.
- F3 Medium: detect-secrets skipped files by name and extension (lock files, `.css`, `.svg`, `.map`). Fixed: those two filters are disabled; the pin rule also covers `checksum = "<hex>"` and `sha256:<hex>`, and the repo stays clean with lockfiles scanned.
- F4 Medium: lines were split on form feeds and other separators detect-secrets ignores, so a mark could cover another line. Fixed: split on `\n` only.
- F5 Low: pin keys were not anchored (`api_secret_sha256`). Fixed: exact keys at a word boundary.
- F6 Low: normalised path spellings raised a `KeyError`. Fixed by the generated names; unknown result files raise `ToolError`.
- F7 Medium (tests): no case of a pin and a secret on one line. Added.
- F8 Low: the new test class sat after the `__main__` guard. Moved.
- F9 Low: one value reported once per plugin. Deduplicated per line.
- Docs: roadmap wording; ARCHITECTURE.md §11 lists `scripts/secrets-scan/`.
- Re-verification required (High fixes): round 2 below.
- Round 2, head d861aaa: FAIL. Attacks: 13 (round-1 repros, new tests on old code, lone CR, file types, locales, git's file filter, argv, pin forms, mutants, written encodings, the config transformer, the full scan, three ways of running the tests). Round-1 F1, F2, F3 (name skips), F5–F9 hold.
  - F1 Medium: a lone CR still moved a mark (detect-secrets reads with universal newlines). Fixed: CRLF, CR and LF each end a line; the test covers form feed, lone CR and CRLF.
  - F2 Medium: generated `.txt` names lost detect-secrets' per-language keyword rules. Fixed: a plain suffix (`.go`, `.c`, `.yaml`, ...) is kept, anything else becomes `.txt`; tests for Go and C keywords and for suffix sanitising.
  - F3 Medium: under a non-UTF-8 locale detect-secrets skipped files it could not decode, silently. Fixed: the tool runs with `PYTHONUTF8=1`; test under a Shift JIS locale.
  - F4 Medium (tests): the mark test passed on the old code. Replaced (see F1).
  - F5 Low: the `sha256:<hex>` pin form was dead code (detect-secrets never flags it). Dropped.
  - F6 Low: a test checked a shared temp path. It now uses its own temp folder and checks that nothing is left behind.
  - Self-mutation of round-3 fixes: 6 tried, 6 killed.
- Re-verification required (3+ Medium on a High slice): round 3 below.
- Round 3, head c288bc2: FAIL. Attacks: 10 (prior repros; injection with kept suffixes; name skips; a differential scan of 25 texts under their own suffix and as `.txt`; transformers; YAML line counting; mutants; full scan; the locale test on Linux; test hygiene).
  - R3-F1 Medium: keeping the suffix lost detections that only the `.txt` rules make (YAML block, folded and multi-line scalars; unquoted Python values and keywords; `.properties`). Fixed (EP-30): every text is scanned twice, as `.txt` and under its kept suffix, and the findings united; six cases tested.
  - R3-F2 Medium: in YAML, PyYAML ends lines at NEL, LS and PS too, so a mark could cover another line. Fixed: YAML-scan hits are mapped with those breaks; tests for all three.
  - R3-F3 Low (tests): a CRLF mark above a secret. Added.
  - Note: on Linux CI the Shift JIS locale is probably absent, so the locale test only bites on macOS (the pre-push hook runs it there).
  - Self-mutation of round-4 fixes: 4 tried, 3 killed; `yaml-breaks-ignored` is equivalent under the union (the `.txt` scan reports the value on its true line); kept for correct line numbers on YAML-only hits.
- Re-verification required: round 4 below.
- Round 4, head 646590b: FAIL. Attacks: 10 (prior repros; merging hits numbered differently; pins; unparsable YAML; per-scan detections; naming; mutants; fail-closed under an old Python; full scan; tests on base).
  - R4-F1 Medium: duplicates were dropped before the mark and pin checks, so an excused hit on one line hid a YAML-only hit numbered the same on another line (no visible mark needed with a pin). R4-F2 Medium (a regression): YAML hits were always mapped with PyYAML's breaks, but detect-secrets numbers unparsable YAML by universal newlines. Fixed together (EP-30): a hit is excused only when its line shows the mark or pin under every numbering that may apply (universal always; PyYAML's too for a YAML scan), and duplicates are dropped only after that decision.
  - R4-F3 Low (tests): the YAML test used a value both scans find. Replaced by YAML-only values (mark below, a marked line and a pin with the same number elsewhere) and an unparsable file.
  - R4-F4 Low: under Python 3.9 the scanner crashed (exit 1). It now refuses with exit 2 below Python 3.12; test.
- Re-verification required: round 5 below.
- Round 5, head e62f313: PASS WITH FINDINGS. Attacks: 9 (all prior repros, 19 cases; the suite and full scan; new tests on base; ordering of excused and unexcused hits; values spanning YAML lines; legitimate marks after separators; `.yml`/`.eyaml`; 9 mutants; the floor under the system Python 3.9).
  - R5-F1 Low: in a parsable YAML file with a NEL/LS/PS above a value, its correct mark no longer excuses it (universal numbering is also required). Accepted: it fails closed, the characters are rare, and the workaround (a mark that holds under both numberings) is stated in the module doc.
  - R5-F2 Low (tests): the marked-line subtest now uses the same value, so it guards R4-F1. R5-F3 Low (tests): a supported Python (3.12, 3.13) gets past the floor.
  - Doc: the module doc names the Python floor and the exit status 2.
  - Note: a single-quoted YAML string spanning lines is found by neither detect-secrets scan (a gap in the tool, recorded here, not fixed).

## Retro
- Five verifier rounds on one privacy gate. Rounds 1–2 were real holes (paths, option injection, name skips, line counting); rounds 3–5 came from each fix (kept suffixes, then the union, then its line mapping) opening the next case. EP-30 would have pointed earlier to the shape that ended it: decide conservatively (excuse only under every numbering) instead of modelling the tool's numbering exactly.
- Process cost: about 2 h 30 min building and fixing over five rounds; verifiers about 6 + 8 + 4.5 + 4 + 4 min.
