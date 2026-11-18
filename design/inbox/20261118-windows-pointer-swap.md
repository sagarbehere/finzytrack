# Windows' mirror pointer: what should the swap do on a cache volume without the POSIX rename, and what do outside tools get?
- Asked: 2026-11-18 by the orchestrator, S0130-windows-publish
- blocks: nothing (S0130 lands with option 1 of each question, the cheapest, which is what is built); BL-0087 should not turn Windows writes on before these are answered
- Rubric item: a rare setup (PROCESS.md §3 step 2: the policy option goes to the owner first); CAP-MIRROR-EXTERNAL is a release-blocking capability's Windows form
- ADRs checked: ADR-0015 (the pointer file on Windows; the retried removal, amendment of 2026-10-25), ADR-0069 (the plain rename for ledger files, unchanged here), ADR-0061 and its amendment of 2026-11-14

## Context
S0130 builds ADR-0015's pointer file. On Windows the current generation is named by `mirror/current.txt`, a file whose whole content is the generation's name. Each publish writes a new synced pointer and renames it over `current.txt`. The probe on the CI runner measured the facts below (journal S0130, the 2026-11-18 runs 6 and 7).
- The plain rename over `current.txt` (ADR-0069's primitive) is refused whenever any program holds `current.txt` open, even one that shares deletion. With a reader reading in a loop, 96 to 176 of 300 swaps were refused on NTFS and ReFS. The sidecar reads the pointer once per request, so publishes would sometimes fail.
- The rename with POSIX semantics is never refused under readers that share deletion: 0 of 400 on NTFS, and no reader ever saw a missing or torn pointer. Every reader of ours shares deletion. **NTFS offers this rename; ReFS, FAT32 and exFAT do not.**
- No antivirus refusal was seen for the pointer: 0 of 420 swaps with no reader, on every volume.

What is built:
- NTFS: the POSIX rename.
- Other volumes: the plain rename. A refused swap changes nothing: the job raises, the screens and the save's gate stay on the generation the pointer names, and the next open or rebuild tries again. No retry was added, so BL-0284's retry question stays yours.

Likelihood: the cache folder sits in the app's cache directory. Where the shell puts it on Windows is not decided yet (presumably the user's local app data). Windows installs only on NTFS, so a cache folder on ReFS, FAT32 or exFAT needs a redirected profile or a deliberate move: rare. A pin's read lasts microseconds, so even there a collision with a swap is rare in use. The probe's numbers come from a reader reading in a loop.

## Question 1: a cache folder on ReFS, FAT32 or exFAT (options cheapest first)
1. **The plain rename there, stated as a limit (built).** Cost: now and then a publish fails there, and the next job retries it. Truthful: nothing changes and nothing reads a wrong file. Your rule says a missing primitive takes "the stated, reported mode, never a silent fallback". This mode is stated (publish.md §11) but not reported to the user.
2. **Policy: the mirror's cache folder must be on NTFS on Windows.** The open refuses with a named reason elsewhere. Cost: one check and one message; such a setup gets no mirror at all (no screens) instead of an occasional failed publish.
3. **A bounded retry of a refused swap there.** A refused rename did nothing, so a retry is safe. This is BL-0284's mechanism, which you asked to see first. Cost: a retry policy, and its tests on the runner.

## Question 2: outside tools on Windows (CAP-MIRROR-EXTERNAL, M7, release-blocking)
The capability promises a stable read-only path. On macOS and Linux that is `current.db`, a link SQLite follows. A pointer file gives SQLite nothing to open, so on Windows:
1. **Copy path gives the current generation's own file (cheapest; stated as a limit in publish.md §11).** Windows keeps the file while a tool holds it open, and the cleanup removes it once the tool has closed it and the grace period (10 minutes) is over. A tool that reopens later finds it gone. The user docs would say so.
2. **Also keep `current.db` as a hard link to the current generation on NTFS, refreshed at each publish when no tool holds it.** A tool reopening `current.db` gets a recent whole generation, but while a tool holds it, it is not refreshed (SQLite opens without sharing deletion). Cost: a second mechanism with its own crash cases, an M7 slice.
3. **A symbolic link where Windows lets the app make one** (Developer Mode, or an administrator), else option 1. Cost: two behaviours depending on a Windows setting.

## Recommendation
- Question 1: option 1 as built. The setup is rare, the failure is availability only and truthful, and option 2 trades an occasional failed publish for no mirror at all. If you want your "never a silent fallback" rule applied to the mirror too, option 2 is the policy form and costs little.
- Question 2: option 1 for v0.4, with a backlog row on M7 (CAP-MIRROR-EXTERNAL's Windows note) so the docs state it.

The mechanism's review history (the publish): S0085 (1 round), S0087 (2 rounds), S0088 (2 rounds, 1 High fixed: the stamp), S0118. The swap itself has had one fix (S0087 round 1, red team F6). The POSIX rename for the pointer is new in S0130, and its reviews are under way.

## Answer (owner, verbatim, with date)
