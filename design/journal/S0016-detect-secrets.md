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
