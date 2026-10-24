# S0001: M0 spikes that run on macOS (S1–S8, S13)

## Brief
- Goal: run the M0 technical spikes that need only the owner's Mac, each with a pass/fail verdict against its criterion (review:C §5; review:27 §1(d)). Windows and Linux parts wait for CI.
- Risk class / lane: Medium / full (they inform write-path and stack decisions; no product code).
- Evidence: the full write-ups and sources are in the private review archive (`review:spikes/<id>`), because their logs contain machine details.

## Verdicts

| Spike | What | Verdict | Key numbers |
|---|---|---|---|
| S1 + S8 | Tauri 2 shell + Python sidecar, ad-hoc signing, updater N→N+1, frozen Beancount | **PARTIAL** (all unattended macOS checks pass; Gatekeeper dialog, Windows, Linux not tested) | cold start ≈ 2 s (≤ 10 s), warm ≈ 1.1 s (≤ 3 s); update ≈ 91 MB; no quarantine flag after an update; no orphan after `kill -9`; frozen = unfrozen hashes |
| S2 + S3 | Playwright WebKit vs the real WKWebView; cross-origin API and AI SDK v7 stream with Stop from `tauri://localhost` | **PARTIAL** (macOS pass; Windows/Linux not tested) | the real WKWebView *can* be driven in a test-only build (WebDriver plugin); first token ≈ 0.1 s; Stop ends server generation |
| S4 | Overlay load equivalence and write cost at 54k | **FAIL**, confined to very large single-file ledgers; equivalence exact | 194 comparisons, 0 mismatches; quiet-machine p95 per write incl. the synchronous re-check: **4.4 s for 12 files (passes ≤ 5 s)**, 7.3 s for one 20 MB file (5.5 s with GC pause + parse reuse), mirror update not included; peak memory 1.7× (multi-file) / 3.3× (single file) vs ≤ 3× |
| S5 | TLA+ model of the write protocol + TLC | **PASS** | small config 10 s (< 2 min); large 16 min (< 1 h); 4/4 planted bugs found (+6 more); 8 protocol gaps found |
| S6 | XState model-based tests against a real component | **PASS** | 14/14 states, 37/37 transitions executed by the live component in ≈ 7 s; generators 51 lines |
| S7 | openapi-typescript + openapi-fetch with branded Money / CivilDate types | **PASS** | zero casts in feature code; runtime decoding matches types; byte-stable spec export |
| S13 | React vs Vue in WebKit | **FAIL for React → Vue** (ADR-0053) | timing tied (0.97–1.02); React webview memory +22–64% (RSS) after the workload |

Not run here: S9 (oracle harness for 3 mutators), S12 (legacy API tests out of process), S10 (the process pilot) — they follow as normal M0 slices.

## Decisions
- ADR-0053: the frontend stays on Vue 3, by the owner's S13 rule (agent applied the rule; owner may narrow the memory metric or ask for a rerun).
- Pending owner (private question list): sidecar packaging (python-build-standalone recommended over PyInstaller, an ADR change), Linux package set, the meaning of "synchronous R4" and the large single-file budget, whole-set write lock, second-instance journal recovery, mirror GC grace period, the WebDriver plugin in test builds, the minimum macOS version, Tauri's CSP rewriting.

## Design amendments the spikes require (to apply before the slices that build them)
1. **Sidecar shipping (S1):** a onedir sidecar ships as `bundle.resources` and is spawned by the shell; `externalBin` cannot carry it. The sidecar runs a stdin-EOF parent-death watchdog (a process group alone does not stop orphans) plus a parent-pid poll. The sidecar is never extracted to a temp or data dir (first-run scans cost 17–49 s outside the signed app). → ARCHITECTURE §5.
2. **Linux updater (S1):** tauri-plugin-updater 2.13.3 updates AppImage, `.deb` and `.rpm` (the latter two behind a root prompt); the "AppImage only" note in CAP-DESK-AUTOUPDATE is wrong.
3. **Missing-plugin error (S8):** Beancount's error for an uninstalled plugin is one string with an embedded traceback; INV-03 messaging needs a mapping for it.
4. **CSP (S2):** add `ipc: http://ipc.localhost` to `connect-src`; turn off Tauri's automatic script-src hashing so the configured pre-paint hash is the only one; test that the shipped hash matches. Streams other than chat read with fetch streams (EventSource cannot send the token header). Send `Access-Control-Max-Age`. → ARCHITECTURE §5, BL-0032.
5. **WebKit baseline (S2):** Playwright's WebKit is newer than the shipped WKWebView; lint against the oldest supported WKWebView (no global `Temporal`, no async iteration of streams); canonicalize time-zone ids in tests; set the colour scheme explicitly; drag-and-drop needs two adapters (file paths in the app, bytes in the browser).
6. **Write path (S4):** take the overlay as bytes (CRLF inside multi-line strings); handle encrypted includes; new files must exist (empty) before an overlay edit; add to the D1 corpus the column-0 continuation line that defeats the ported span finder; `load_file` with the cache disabled deletes a stale `.picklecache` next to the root (the re-check must not run on the user's directory unexpectedly). Pause the garbage collector during a write and reuse the step-1 parse.
7. **Write protocol (S5 F-1..F-8):** hash each backup against the planned pre-hash (fail closed); re-hash every target before saying "unchanged"; acknowledge only after the journal close is durable; restore the root first; WARMING rebuild and snapshot reads take the locks and refuse while a journal is open; one journal per commit with a unique name, and fsync the journal directory before trusting "no journal"; the whole-set lock question (owner); the `FileSystem` port gets `atomic_swap`, not `os.replace`. → D1 ADRs (ADR-0008, ADR-0012, ADR-0013), ARCHITECTURE §8.3, §9.2.
8. **TLA+ in CI (S5):** T1 = small config + all bug and variant configs (≈ 1 min); T2 = large config (≈ 16 min) + legacy and liveness configs.
9. **Model-based tests (S6):** "the test run must execute every path" becomes "run a covering subset of the generated paths; coverage measured from the live actor must be 100%". A shared `test/mbt` helper (custom serializer keyed by transition, inspect-based coverage, greedy cover, 100% gate; about 100 lines). MBT checks that the UI matches the machine, not the machine's logic: EP-17 adds independent oracles and property tests on the pure domain. `createTestModel()` cannot handle machines that invoke actors; use `TestModel`.
10. **API binding (S7):** decoding happens in a small typed wrapper around openapi-fetch (middleware cannot return decoded values); a custom query serializer for civil dates; parameters use the `Annotated[..., Query()]` form so their `x-ft-type` survives; decimals are serialized with `format(d, "f")`; the generator refuses untagged unions, envelope-less errors and operations without a scope.

## Retro
- Seven spikes ran in parallel on one machine; timing results carry a load caveat (interleaved rounds controlled for it; S4 was rerun when the machine was idle: 12-file p95 went from 5.5 s to 4.4 s, so load roughly added 25%).
- Scanning the spike outputs with the T0 privacy scanner exposed three false-positive classes (lockfile `name@version`, epoch-ms and sizes in JSON, `icon@2x.png`), all fixed with meta-tests before any package lands on `next`.
