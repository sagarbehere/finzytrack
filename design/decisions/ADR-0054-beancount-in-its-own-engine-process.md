# ADR-0054: Beancount runs in its own ledger-engine process; the main sidecar never imports it

This record splits the Python backend into two processes along the licence boundary: a small ledger engine that holds Beancount, and the main sidecar that holds everything else.

- **Status:** Accepted. Amended by ADR-0064 (2026-10-27): decision 4's heavy-load semaphore is replaced by one engine doing one job at a time, behind a queue in the main sidecar.
- **Date:** 2026-10-25
- **Deciders:** owner: "Yes, I liked the flipped architecture. Let's go with that." (2026-10-25, in session)
- **Area:** architecture, licence, backend
- **Supersedes:** the single-sidecar process model in ADR-0007 and ARCHITECTURE.md §1, §2, §5 (the rest of ADR-0007 stands)

## Context

Beancount is GPL-2.0-only. A licence inventory of the pinned backend set (75 packages; review:spikes/licence-inventory) found:
- **ofxtools** (the OFX reader) is **GPL-3.0-only**, which cannot be combined with GPL-2.0-only code in one program in any reading;
- the AI stack (`openai`, `requests` via `tiktoken`), FastAPI's required `opentelemetry-api`, `tzdata` and partly `pypdfium2` are **Apache-2.0**, which the FSF and the Apache Software Foundation both consider incompatible with GPL-2.0-only in one program;
- Beancount 3.2.0's **own** required dependencies `regex` and `python-dateutil` contain Apache-2.0 code.

In Python, importing a module into the same process is treated as combining the works. Separate programs that exchange data over a pipe or socket are generally aggregation, unless they exchange intimate internal data structures. The owner's own code is GPL-2.0-or-later (ADR-0042), so it can join either side. This record is not legal advice; ADR-0042's legal review covers it.

## Decision

1. **Two backend processes.**
   - **Ledger engine** (`engine/`): Beancount, beanquery, their own dependencies, and our ledger code: the overlay load and `LoadPolicy`, the parser and grammar facade (`beancount_port`), the write-safety contract and writer (journal, swap, backups, read-back, re-check), the mirror build and atomic generation publish, BQL execution, and Beancount-derived facts (account grammar, tolerance, booking). A GPL-2.0-only program: it contains only Beancount, what Beancount itself requires, and code licensed GPL-2.0-or-later.
   - **Main sidecar** (`backend/`): FastAPI, workspaces, boot and lifecycle, recipes and SQL over the mirror, import readers (ofxtools, PDF, CAS, email), the categorizer, prices, AI (pydantic-ai with the openai and anthropic SDKs), logging. **It never imports Beancount** and is distributed under GPLv3 (the owner's "or later" allows it), which accepts Apache-2.0 and GPL-3.0 components.
2. **A plain-data protocol between them** (`contracts/engine-protocol.schema.json`, versioned): JSON messages over a local pipe (or a loopback socket), request in, result out. It carries intents and edit plans, refusals with their error keys, semantic diffs, located errors, query tables, ledger facts (the Ledger Profile, the account grammar pattern, tolerances) and mirror-generation ids. **No Beancount objects, no pickles, no callbacks, no shared memory** cross it. The protocol is designed as a public interface, so it stays arm's-length.
3. **Reads go through the mirror**, which the engine builds and publishes; the main sidecar reads the published SQLite generation directly with the standard library, as already decided (ADR-0015). Only writes, BQL, loads for reconciliation and Beancount-derived facts go to the engine.
4. **Lifecycle:** the main sidecar spawns and supervises the engine (parent-death watchdog as in spike S1; restart with backoff). The engine keeps no resident ledger between operations (INV-10) and owns the heavy-load semaphore. One engine serves the user container; per-workspace state stays in the requests.
5. **Two Python environments, two dependency sets**, each with its own lockfile, licence allowlist and notices section: the engine's allowlist is GPL-2.0-compatible plus an explicit, reviewed list of Beancount's own dependencies (`regex`, `python-dateutil`, pending legal advice); the main sidecar's allowlist is GPL-3.0-compatible (Apache-2.0 allowed). A T1 check proves the main environment cannot import `beancount`, and that the engine environment contains only its allowlisted packages.
6. **INV-02 is unchanged** (Beancount is the oracle, never reimplemented). Beancount-derived rules the main sidecar needs (account grammar, tolerance) come from the engine as data or calls, never as a reimplementation.

## Alternatives considered and rejected

- **One sidecar, avoid Apache-2.0-only packages near Beancount** (owner discussion, option 1). Rejected: it cannot remove ofxtools (GPL-3.0-only), FastAPI's opentelemetry-api, or Beancount's own Apache-2.0 dependencies.
- **One sidecar plus a separate AI process** (option 2). Rejected in favour of this record: it still puts FastAPI, ofxtools and the import stack in the Beancount process.
- **Not shipping a bundled backend** (users install it with pip). Rejected: it defeats the desktop installer.
- **Asking Beancount upstream for "or later" or a linking exception.** Not rejected, not relied on: worth asking, but out of our control.

## Consequences

- ARCHITECTURE.md §1, §2, §5, §10 and §11 change (amendment block in §2.0). The package map's `ledger` and the mirror's build half move to the engine; the main sidecar gets an `engine_client` port behind the same use-case interfaces.
- Packaging (spike S1) ships two Python trees in the bundle; with python-build-standalone (Q21, pending) both can share one interpreter build with two separate site-packages. Signing, the updater and the orphan rules cover both processes.
- The write path gains one local hop per write; the engine returns diffs and results, not entries, so the cost is small next to the load itself (S4). Spike S4's timings stay valid for the engine.
- Tests: the engine has its own unit, corpus and model tests; contract tests on the protocol schema on both sides; an architecture test per environment (no `beancount` import outside `engine/`; no engine import of main-sidecar packages).
- The legal review (ADR-0042) gets two concrete questions: is this split aggregation, and is shipping Beancount with its own Apache-2.0 dependencies acceptable.
- TLA+ (S5): the engine is the writer actor; the model is unchanged.
- **Performance (estimates, to be measured in M1a by extending the S4 harness):** reads are unchanged (they use the mirror); a write adds one local round trip with a plan in and a result out (typically a few kB, well under 10 ms) on top of the engine work S4 measured; large BQL results pay JSON serialization (page or stream them); a second interpreter costs roughly 40–80 MB while running; the engine starts in parallel with the main sidecar. Rule: entries never cross the boundary per request.
- **SQLite:** no licensing effect. SQLite is public domain and Python's `sqlite3` is part of CPython (PSF licence, GPL-compatible); the mirror file is data, and reading it from the main sidecar or from outside tools is not linking.

## Amendment (2026-10-26): TatSu-LTS on the engine's reviewed list

Owner: "Please include the bin query in the engine, sorting out the license issues, make that a backlog item. I don't want functionality to be compromised during development because of legal questions." (2026-10-26, in session; "bin query" = beanquery; `design/inbox/answered/20261026-beanquery-tatsu-licence.md`).
- Decision 5's reviewed list now also names **TatSu-LTS**, which beanquery requires. Its licence is BSD with a fourth clause (SPDX `BSD-3-Clause-Attribution`): every redistribution must keep an acknowledgment sentence. Like `regex` and `python-dateutil` it passes the engine's licence check with a warning, pending legal advice, and only while its licence is the reviewed expression.
- `THIRD_PARTY_NOTICES` carries the acknowledgment verbatim; the licence check reads the Python packages' licence texts and refuses a bundled package whose licence asks for an acknowledgment the notices do not carry (S0037).
- beanquery 0.2.0 joined the engine in S0037. Functionality is not held by the licence question: the legal review before v0.4.0 (ADR-0042) settles TatSu-LTS together with `regex`'s CNRI-Python part (BL-0085).

## References

ADR-0002, ADR-0007, ADR-0010, ADR-0015, ADR-0042; INV-02, INV-10; review:spikes/licence-inventory; review:spikes/s01-tauri-sidecar, s04-overlay-54k, s05-tla-write-protocol.

## Amendment (2026-10-27): one engine, one job at a time (ADR-0064)

Owner: "if we are not most of the way there, or if we can't asses that with confidence, then I agree that we go back to one engine doing one job at a time and massively simplify everything." (2026-10-27, in session; the full record and the other quotes are in ADR-0064.)
- **Superseded in decision 4:** "owns the heavy-load semaphore". **Instead:** at most one engine process exists, and it runs one job at a time; the queue lives in the main sidecar (`UserContainer`), where a job the user waits for goes ahead of background work. One load at a time bounds memory; there is no semaphore, no slot count and no admission.
- **Also in decision 4:** "restart with backoff" stays (S0040's bounded restart); a Cancel ends the job's engine and is not a stop; no job has a time limit. An engine that ran user code (plugins) is ended after its job, and the next job gets a fresh one (the owner's 2026-10-26 decision 5, kept on ADR-0064's Unchanged line).
- **Unchanged:** decisions 1-3, 5 and 6; "one engine serves the user container"; the engine keeps no resident ledger (INV-10).

