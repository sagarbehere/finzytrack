# ADR-0010: Validate candidate text with an overlay load (D2), behind a LoadPolicy

This record decides how the contract runs a full official load of text that is not yet on disk.

- **Status:** Accepted. Amended by ADR-0064 (2026-10-27): guardrail 3's re-check runs after "saved" in every mode and is never switched off; the load rule's "heavy-load semaphore" becomes one engine job at a time. Note of 2026-10-29 (S0056): on desktop every ledger loads in full; no trust prompt in v0.4.0 (ADR-0040).
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** write path, ledger

## Context

Contract step 3 (ADR-0008) needs Beancount's full load on the candidate texts before they reach disk. `beancount.loader.load_file` reads only from disk. In Beancount 3.2.0, `_parse_recursive()` is the only loader step that touches files; booking, plugins and validation run on in-memory entries. A prototype matched `load_file` exactly on the demo and on a nested-include ledger with `implicit_prices`.

## Decision

(owner, 2026-10-24)
- **Overlay load:** a small replica of `_parse_recursive` that uses `parse_string(text, report_filename=path)` for files with candidate text and `parse_file` for the rest, then Beancount's own sort, `booking.book()`, `run_transformations()` and `validation.validate()`, unchanged. It lives in `ledger/overlay.py` with a header naming Beancount 3.2.0 and the mirrored function.
- **Part of stage 1.** The contract does not ship without it.
- **One `LoadPolicy` parameter on every load path** (the write gate, BQL, the price sidecar, export, background reconciliation). It governs includes, documents roots, the plugin allowlist, `insert_pythonpath` and encrypted files. The desktop policy is permissive; the hosted body is written only when hosting is planned (ADR-0041). Untrusted code-running ledgers load under the hosted body until the user trusts them (ADR-0040).
- **The load rule:** full loads are invoked only from `ledger/overlay.py` and the reconciliation job, both under the heavy-load semaphore. Parser-only calls go through `beancount_port`. Only `beancount_port` imports `beancount`. An AST test checks the call sites.
- **Guardrails (required):**
  1. CI equivalence tests over the D1 corpus: `load_overlay(root, {})` and `load_overlay(root, disk_texts)` equal `load_file(root)`; a candidate equals `load_file` after a real write. The corpus includes globs, nested includes, `implicit_prices`, `option "documents"`, a `pythonpath` plugin and duplicate-include errors.
  2. Beancount stays pinned (ADR-0004); an upgrade re-runs the suite and re-mirrors the replica first.
  3. Runtime reconciliation: after each committed write, a real `load_file` (through the `LoadPolicy`) is compared with the adopted result, in the background; synchronously during rollout and dogfooding; off in hosted mode. A mismatch logs loudly, adopts the `load_file` result and shows a notice.
  4. Writes to encrypted files are refused.
- A per-file parse cache keyed by text hash is a later optimization, gated by the same suite.

## Alternatives considered and rejected

- **Shadow tree** (copy files to a temp dir and `load_file` there). Rejected: relative paths (`option "documents"`, documents, plugin paths, `pythonpath`) resolve inside the temp dir and change the result.
- **Write, verify, roll back.** Rejected: an invalid ledger sits on disk for a window that a crash, Fava or a file watcher can see.
- **Flattening all entries into one string** (the legacy gate). Rejected: it loses include, plugin-path and documents semantics.

## Consequences

- Exactly one full load per write (about 0.16 s on the demo).
- The replica must be re-mirrored whenever Beancount's loader changes.

## References

review:D1 §9, §12.3; review:18 §2 inv. 4, M-1, L11-02, L11-06; review:25 §2.4 item 4; ADR-0004, ADR-0008, ADR-0040, ADR-0041; INV-01, INV-02, INV-03.

## Amendment (2026-10-27): the runtime re-check in every mode (ADR-0064)

Owner, approving the first golden review's recommendations for the engine-scheduler spec: "I agree with all the recommendations. Proceed on that basis." (2026-10-27, in session, relayed by the orchestrator). The recommendation: the R4 re-check always runs after "saved" and gates only the next save, in every mode, and is never switched off (safety outranks speed; hosted mode is not built).
- **Superseded in guardrail 3:** "in the background; synchronously during rollout and dogfooding; off in hosted mode". **Instead:** after each committed write, the real `load_file` (through the `LoadPolicy`) is compared with the adopted result right after the save reports "saved", in every mode; the next save to that workspace waits for it (ADR-0008 amendment); a mismatch pauses saving, adopts the `load_file` result and shows the backup (`design/specs/engine-jobs.md` §6.7). It is never switched off. If hosting is ever planned, its cost is decided then, by a new ADR.
- **Also (ADR-0064 decision 1):** the load rule's "both under the heavy-load semaphore" becomes "both inside engine jobs, which run one at a time".
- **Unchanged:** the overlay load, the `LoadPolicy`, guardrails 1, 2 and 4, and the mismatch handling.

## Note (2026-10-29): desktop loads every ledger in full (ADR-0040)

Owner, 2026-10-29, in session: "I'm wondering if we should remove this feature for now. None of the beancount tools I know have this.. it'll likely cause some confusion (unless explained well) to even well-versed users.. or, at minimum, add one more friction step whose answer is difficult to give precisely or correctly. I'd say, pause this feature. Mark it as something to be done later, and be done with it. What do you think?" (S0056 verifier round 1, F3).
- **No longer holds on desktop:** "Untrusted code-running ledgers load under the hosted body until the user trusts them (ADR-0040)". **Instead:** v0.4.0 has no trust prompt (ADR-0040 amendment "no trust prompt on desktop in v0.4.0"); on desktop every ledger loads in full under the permissive body, its plugins and `insert_pythonpath` included, as `bean-check` and Fava load it. The hosted body is still written only when hosting is planned (ADR-0041).
- **Unchanged:** one `LoadPolicy` parameter on every load path, and everything else in this record.

## Amendment (2026-10-31): R4's load and the `LoadPolicy`

Owner, 2026-10-31, in session, answering `design/inbox/answered/20261031-r4-load-policy.md` (raised by S0066): "for the r4 load policy inbox item, I agree with the recommendation of option 1 with option 3's note".
- R4's real load (Beancount's own `load_file`, uncached, BL-0118) asks the `LoadPolicy` nothing per file, since Beancount's loader cannot be asked: "through the `LoadPolicy`" is met by running it only when the disk's fingerprint equals the record's, whose files, plugins and folders the save's own load passed through the policy. The window in which the load meets a file changed since that check is the whole load (spec §15); it is seen afterwards (`superseded`). R4's loads refuse encrypted files as the overlay does (no `gpg` runs).
- A parse-only pre-check asking the policy about every file is built only if a hosted mode is ever planned (ADR-0041 already requires that plan to revisit the load rules).
