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
