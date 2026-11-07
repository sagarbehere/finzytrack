# Mirror: the generation publish

## Orientation
- **What this is:** the rules for how a finished mirror build becomes the mirror that the screens, outside tools and the save's gate read: the folder and what is ours in it, generation names, the build into its own name, the refusal of an incomplete build, the syncs, the link swap, the stale-journal rule, what `meta` carries, the cleanup, what a reader does, what the gate compares, and what a crash at each step leaves. Rules only: why is in ADR-0015 (and its amendments), when and how in the journals (S0085, S0086).
- **The bytes it protects:** no ledger bytes: the publish writes only the app's cache folder. It keeps two outcomes truthful: what a reader reads is one whole generation, built from one state the ledger really had; and the save's gate compares the plan with the generation that is really published.
- **Where the code is:** built: the build `ledger_engine.mirror.build.build` (a fresh file or none, S0050), the rebuild's load `ledger_engine.ledger.rebuild.run` (S0070), the read-only open `app.mirror.files.open_mirror` (S0052). **Not built yet** (S0086, BL-0113): the publish, the cleanup and the number (`ledger_engine.mirror.publish`), the gate's read of the published generation, the reader's pin (`app.mirror.files`), the `meta` keys of §8. Windows' pointer file: BL-0206 (M11).
- **Decisions it applies:** ADR-0015 (the rebuildable cache; build a new generation, publish it atomically; the grace period; the content-hash fingerprint; the amendment of 2026-11-07: the folder is ours, grace only, no pin tracking), ADR-0016 (the reads), ADR-0064 (one engine, one job at a time), ADR-0068 D1 (the rebuild after a save is the publish's caller), ADR-0061 (macOS first; Windows in M11).
- **Invariants:** INV-05 (the mirror is a rebuildable cache, published atomically), INV-10 (bounded caches), INV-23 (logs carry no content).
- **Model:** `formal/tla/mirror-publish/` checks §10's two properties (durability only, EP-19); `MAPPING.md` maps it both ways.
- **Traps:** never open `current.db` by that name from our code: resolve it once and open the generation (§7). Never write into a file that has been published, and never reuse a number while any name carrying it remains (§3, §5). A build that says it is incomplete is never published: until BL-0120 builds the `commodities` rows every build says so, so S0086's publish needs BL-0120 first.

## 1. Terms

- **Mirror folder:** `<app cache dir>/workspaces/<key>/mirror/`, keyed by the workspace's registry key (ADR-0015).
- **Generation:** one mirror database file, `gen-<N>.db`, written once by one build and never changed afterwards.
- **Current generation:** the one `current.db` names. **Previous generation:** the highest-numbered generation file below the current one.
- **Publish:** making a new generation current (§4). **Cleanup:** removing what is not kept (§6).
- **Committed ledger state:** the ledger files' contents at a moment when no save of ours is half done (no `.open` journal of this workspace, `design/specs/engine-jobs.md` §6.5).

## 2. The folder is ours

- **The whole mirror folder is ours** (owner, 2026-11-07, ADR-0015 amendment): the publish and its cleanup may remove anything in it and replace whatever sits at `current.db`.
- **Never through a link:** every entry is examined without following links (`lstat`). A link is removed as a link, never what it points to. A folder inside is removed with everything in it, again without following any link inside it (a descriptor-relative walk).
- **The folder itself:** before anything else, a link at `mirror/` is removed as a link, and anything else that is not a folder (a file) is removed; then the folder is made (mode `0700`) and its parent synced. The folders above `mirror/` (`workspaces/<key>/`, the app cache dir) are the app's and may be links; they are followed, as the save follows the app's own folder (engine-jobs §6.1 step 8).
- **Only the engine writes it,** inside an engine job (`open` or `rebuild`), holding the engine lock: one job at a time, so no two publishes or cleanups ever run together, and none runs beside a save (ADR-0064). The main sidecar and outside tools only read.

## 3. Generation names and numbers

- A generation is `gen-<N>.db`: `N` in decimal from 1, no leading zeros. SQLite names its journals after it: `gen-<N>.db-journal` (rollback mode; our builds never make `-wal` or `-shm`, but the rule below counts them too).
- **The next number** is 1 + the largest of: every `N` in a name `gen-<N>.db` or `gen-<N>.db-<anything>` in the folder after the cleanup (§6), the current generation's, and the **floor** the sidecar sends with the job (the highest generation it has served in this session). So a number is never reused while any name that carries it remains, and the numbers grow through a session even when the OS purges the cache folder under a running app.
- **Across restarts** the numbers grow while the folder survives. After a purge between sessions, or a power loss that undid the last publish, a number can come again; no running reader or screen remembers it then (every reader dies with the app or the machine).

## 4. The publish

Inside an `open` or `rebuild` job. Step 1 is the job's first step, before its load; steps 2 to 9 follow once the load has a result (the rebuild after a save: `ledger.rebuild.run`'s `loaded`, engine-jobs §6.7; the open: its overlay load, BL-0152). A job that finds the published generation fresh (§8) builds nothing and stops after step 1. Steps in this order:

1. **The folder** (§2) and **the cleanup** (§6).
2. **The number** `N` (§3).
3. **The build** writes `gen-<N>.db` with an exclusive create (`build.build`): a file at the name refuses it. The build writes everything in one SQLite transaction in rollback mode; at its commit SQLite deletes the journal. A build that fails removes its file (`BuildFailed`, or an error); one cut by a crash leaves its file and journal, never published, for the next cleanup.
4. **Never incomplete:** a build whose result says `complete` is false (`meta.build_complete = 0`) is not published; its file stays for the next cleanup. So a published generation always says `build_complete = 1`.
5. **The file synced** (`durable_sync`: `F_FULLFSYNC` on macOS; SQLite's own syncs do not reach the disk there).
6. **The folder synced**: the new file's name and its journal's deletion are durable.
7. **Checked again, just before the swap** (engine-jobs §7): the ledger set the build read, observed again, equals the fingerprint the build is stamped with; and this workspace has no `.open` journal. A moved ledger is not published (the job tries again, at most 3 tries in a row, engine-jobs §6.7, §7); an `.open` journal refuses the job (`WRITES_PAUSED {unfinished_save}`). The built file stays for the next cleanup.
8. **The swap:** a symbolic link `current.db.<N>.tmp` whose target is the relative name `gen-<N>.db` (an entry already at the temporary name is removed first: it is ours), then renamed over `current.db`. Readers see the old or the new link, never neither.
9. **The folder synced** again. Only then does the job answer "published", with `N` and `ledger_version` (§8).

## 5. The stale-journal rule

ADR-0015: "The live path never sits next to a leftover `-wal`." Renaming a new database over a path that has an old journal or WAL beside it makes SQLite play that journal into the new file. Here no database is ever renamed or rebuilt at a path:
- Readers and the gate never open `current.db` by that name; they open the generation (§7). SQLite itself, when a program opens `current.db`, names the journals after the file the link resolves to (checked with SQLite 3.53 on macOS in S0085: a WAL opened through `current.db` is `gen-1.db-wal`).
- A build always writes a new name, and no journal of that number exists (§3).
- Our builds write rollback mode; a reader refuses a file in WAL mode (S0052's header check).
- The cleanup removes `current.db-*` names (a program whose SQLite does not resolve links) and the journals of generations it does not keep (§6).

## 6. The cleanup

First in every `open` and `rebuild` job, before its load (§4 step 1), whether or not the job then builds; so never beside a build of its own.
- **Kept:** `current.db` when it is a link whose target is exactly `gen-<C>.db` and that file is a regular file in the folder (`C` is then the current number); the current generation; the previous generation; and each older generation `G` whose **replacement** is less than the **grace period** ago (10 minutes; a configuration value, ADR-0015 amendment). A generation's replacement time is the modification time of the next-higher-numbered generation file present: that file's build ended just before it was published (below the current number every file was published, since files above it are removed first).
- **A journal beside a kept generation is left as it is**: a reader refuses that file (SQLite finds the journal hot and a read-only connection may not roll it back), and the open then builds and publishes a new generation.
- **Removed:** everything else: every generation numbered above the current one (never published), every other generation, a journal of a generation not kept (after its database file), temporary links, `current.db` when it is not a kept link, `current.db-*`, and any other file, link or folder.
- **Order:** a database file before its journal, so a file that a build left half written never loses its journal while it exists.
- **A removal that fails** is logged (content-free, INV-23) and skipped; the next cleanup tries again. Nothing fails the publish: §3's number skips every number still in a name.
- **No pin tracking:** a reader's open connection keeps its file after the name is removed (macOS, Linux), so the cleanup needs to know nothing about readers (owner, 2026-11-07). Windows cannot remove an open file; its pointer file and retry are BL-0206 (M11).
- **Open question Q1** (§13): how many generations the grace period may keep during a burst of saves.

## 7. Readers, and the save's gate

- **A request resolves once:** `readlink(mirror/current.db)`; the target must be exactly `gen-<N>.db`, else there is no published mirror (`MirrorUnavailable`, the caller decides on a build). The request pins `mirror/gen-<N>.db`, and every read of that request opens that path (`files.open_mirror`: read-only, header and `meta` checked); it never resolves the link again, so its reads all come from one generation.
- **A pinned file that is gone** (the cleanup removed it after the grace period, or the OS purged the cache) is `MirrorUnavailable("cannot_open")`; the route answers "try again" (BL-0126) and the next request resolves again.
- **An open connection** keeps reading its file after the cleanup removes the name.
- **Outside tools** open `current.db` read-only (the stable path, CAP-MIRROR-EXTERNAL); SQLite resolves the link when it opens it. A tool that stays connected keeps its generation; one that opens again after a publish reads the new one.
- **The envelope's `meta.ledger_version`** is the pinned generation's (§8; BL-0126).
- **The save's gate** (engine-jobs §6.1 step 4): inside the save job the engine resolves `current.db` once and reads that generation's `meta`; the mirror check passes only when its fingerprint digest equals the plan's. No published generation, or one that cannot be read, refuses `mirror_stale` (`WORKSPACE_NOT_READY {WARMING}`). One job at a time: no publish runs between this read and the save's commit point. `SaveRequest.mirror`, the sidecar's copy of the digest, goes (S0086).

## 8. What `meta` carries

- `build_complete` = `1` (always, for a published generation, §4 step 4); `surface_version`; `export_version` (the hash of the DDL).
- `fingerprint`: `ledger_set.encode` of the fingerprint the build is stamped with (the overlay load's, or the rebuild's observation): the ledger set it read and each path's state, so the freshness check decodes it, observes the disk (about 3 ms at 54k) and compares digests without a load (BL-0125).
- `ledger_version` = `<N>/<digest>`, the generation number and `ledger_set.digest` of that fingerprint (`sha256:<hex>`), for example `42/sha256:3f9a…`. Ordered by `N` (ADR-0037: a late response cannot roll the version back; §3).
- `engine_build`: the engine build that wrote the rows, so a change in the row logic with the same DDL rebuilds (BL-0125). `as_of`: the build's "today" (ISO date, the user's time zone), since `account_balances` and `lots` stop at it. `built_at`: when it was built.
- **Fresh** (BL-0125's check, used by the open and the focus check): the generation's fingerprint digest equals the disk's, its `export_version` and `engine_build` are the running engine's, and its `as_of` is today. A generation that is not fresh is read with the banner while a rebuild runs (`WARMING`, ADR-0015); writes wait for the new publish.
- The new keys (`engine_build`, `as_of`) and the `ledger_version` format are S0086's, with `surface.toml`'s `meta` note.

## 9. A crash at each step, and what the next start sees

"Death" is an engine death or an app crash (the folder stays as it is); "power loss" adds what an unsynced step may lose. File-system assumptions are engine-jobs §13's: a file's data is durable after its sync; a directory entry (made, renamed or removed) after its folder's sync; unsynced operations in one folder may each be kept or lost on their own.

| Cut after | Death: the folder | Power loss may also leave | Readers until the next publish | The next job |
|---|---|---|---|---|
| 1. the folder, the cleanup (part done) | old entries partly removed (a journal may remain without its file); or a link at `mirror/` removed and no folder yet | removed entries back | the current generation; none when the folder was replaced | makes the folder, removes the rest |
| 3. the build, writing | `gen-<N>.db` with its journal | either missing, or torn | the current generation (`gen-<N>` is never linked) | removes both, file first |
| 3. the build, committed | `gen-<N>.db` whole, no journal | the file missing or torn, the journal back | the current generation | removes it |
| 4. refused incomplete | `gen-<N>.db` whole, incomplete | as above | the current generation | removes it |
| 5. the file synced | `gen-<N>.db` whole | the file missing, or its journal back | the current generation | removes it |
| 6. the folder synced; 7. refused | `gen-<N>.db` whole | nothing more | the current generation | removes it |
| 8. the temporary link made | the temporary link | the link missing | the current generation | removes it and `gen-<N>` |
| 8. renamed over `current.db` | `current.db` → `gen-<N>.db` | `current.db` back on the old generation; `gen-<N>` above it | the new generation; after a power loss, the old one | after a power loss removes `gen-<N>` (above the current number) |
| 9. the folder synced | published | nothing more | the new generation | the cleanup (§6) |

- **No step leaves a reader a partial, incomplete or torn generation**: `current.db` names a file only after its data and name are durable (steps 5, 6 before 8), and the old link stays on a whole file until the new one replaces it.
- **The next start** reads the published generation and checks its freshness (§8): a power loss that undid a publish leaves the older generation, which is not fresh, so the open rebuilds.
- **A cut save's `.open` journal** never reaches a reader through the mirror: step 7 refuses it; the open waits for recover-all (INV-32).

## 10. What the TLA+ model checks

**Durability only** (EP-19; BL-0113): `formal/tla/mirror-publish/MirrorPublish.tla`. Its world: a ledger of two files written by an outside program at any time and by a save of ours cut after its first file (its `.open` journal until recovery); the mirror folder with generations 1..MaxGen (a database file and its journal each) and `current.db`; one engine job at a time (§2); readers that resolve and read twice per request (the screens, an outside tool, the gate); engine deaths, app crashes and power losses under §9's file-system assumptions. The cleanup may remove any file it does not keep, at any time in its step, so the grace period's length does not matter to the properties.

**Properties:**
- `ReadersSeeCompleteGeneration`: every read a reader accepts reads a whole, complete generation: never a file a build is still writing, one whose build said it is incomplete, or bytes a power loss tore.
- `SnapshotIsSomeCommittedState`: what a reader reads is the build of one committed ledger state that the ledger really had, its `meta` names exactly that state, and the two reads of a request read the same generation. The save's gate is such a reader.

**Seeded bugs, each must fail its property** (`MAPPING.md` §4.1): `no_file_sync`, `publish_incomplete`, `build_in_place`, `reuse_number` (`ReadersSeeCompleteGeneration`); `no_recheck`, `no_open_check`, `reresolve_per_read` (`SnapshotIsSomeCommittedState`).

**Not modelled** (checked by S0086's tests instead): the grace period's timing and the replacement time (§6), the folder and link rules (§2), unknown entries, the floor (§3), the freshness check (§8), availability (a reader that finds no file answers "try again"), Windows (BL-0206), and Beancount's load itself (the model's load reads two version numbers).

## 11. Honest limits

- **Limit (A-B-A):** the fingerprint is a content hash, so an edit made and undone to the same bytes while the load runs is not seen: the generation's rows can come from the edited bytes while `meta` names the original ones. The rows are still a state the ledger had; the next save's gate and the next build see the disk as it is (the model's editor never writes back earlier bytes).
- **Limit (outside writers):** the mirror is read-only for outside tools (ADR-0015's contract). A program that writes into a generation can make what readers see wrong; a reader refuses such a file only when it left a journal or switched it to WAL mode.
- **Limit (old SQLite):** a program whose SQLite does not resolve links names its journals after `current.db`; the cleanup removes those names, but a write through it is an outside writer.
- **Limit (clock):** the grace period uses file modification times: a clock set forward removes replaced generations early (a pinned request then answers "try again"); a clock set back keeps them longer.
- **Limit (cleanup only in jobs):** nothing is removed between jobs, so the folder keeps what the last job left until the next open or rebuild.
- **Limit (a removal that failed):** a generation that the cleanup could not remove can sit between two published ones and shorten the grace of the one below it; a pinned request then answers "try again".
- **Limit (link swapped in):** a program writing inside the cache folder can put a link in place of an entry between our look and our removal; the removal then removes that link, never its target.
- **Limit (disk):** every kept generation takes its full size (about 80 MB at 54k entries, S0050); see Q1.
- **Limit (Windows):** this design needs a link that can be renamed over and the removal of open files: BL-0206 (M11).

## 12. Simplification pass (EP-30)

| Mechanism | Requirement it serves | Can a policy forbid the risk instead? |
|---|---|---|
| A new file per generation, never written after publish (§3, §4) | readers never see a partial mirror (INV-05; v0.3's in-place rebuild) | no: reads run while a build runs |
| Exclusive create, fresh number never reused while named (§3) | no journal of another file is played into a new one (§5) | no: crashes leave journals |
| Refuse `build_complete = 0` (§4 step 4) | a published mirror is complete | no: a build can leave a table unbuilt (every build today, until BL-0120) |
| File sync, folder sync before the swap (§4 steps 5, 6) | a power loss never leaves `current.db` on a torn or missing file | no: power losses happen; the cost (one file sync, two folder syncs per publish) is measured in S0086 |
| Check again before the swap (§4 step 7) | the published rows and `meta` are one state the ledger had, never a half-done save | no: outside edits and cut saves happen |
| Link swap by rename, folder synced (§4 steps 8, 9) | readers see old or new, never neither | no: readers read during every publish |
| One resolve per request (§7) | a request's reads come from one generation | no need: it costs nothing |
| No pin tracking (§6) | none needed on macOS/Linux | **cut** (owner, 2026-11-07) |
| Grace period and previous generation kept (§6) | a pinned request, or an outside tool that opens by name again, finds its file | kept by the owner (2026-11-07); its disk cost is Q1 |
| Cleanup never follows a link (§2) | no file outside the folder is ever removed | the owner made the folder ours; the link rule is what keeps that safe |
| The floor (§3) | `ledger_version` never goes back under a running screen after a purge | kept: one number sent with the job |
| `current.db-*` removal (§5, §6) | an old-SQLite tool's journal never sits by the stable path | kept: part of "remove everything not kept" |
| Removing unknown entries | the folder is the app's; nothing else to decide | owner's decision 1 |

Cut: pin tracking in the app (owner); a second link for the previous generation (the highest number below the current one serves); a cleanup after the publish as well as before (one place is enough: §9).

## 13. Open questions

- **Q1 (owner; blocks S0086's cleanup rule only): disk use during a burst of saves.** The grace period keeps every generation replaced in the last 10 minutes. A rebuild follows every save, so a burst of saves (one every few seconds while categorizing) keeps one generation per save: at 54k entries about 80 MB each, so 20 saves in 10 minutes keep about 1.6 GB, and a long burst far more. Options and the recommendation: `design/inbox/20261108-mirror-generations-disk.md`. Until answered, S0086 builds the rule as ADR-0015 states it.
