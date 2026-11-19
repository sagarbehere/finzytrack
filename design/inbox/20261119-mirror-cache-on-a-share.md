# BL-0298: a mirror cache folder on a network share: refuse it by policy, or open SQLite there in its UNC form?
- Asked: 2026-11-19 by the orchestrator (before any BL-0298 slice; facts from the 2026-11-19 run 4, probe P8, head c7f454bf)
- blocks: nothing now; BL-0087 should not turn Windows writes on before this is answered
- Rubric item: a rare setup (PROCESS.md §3 step 2: the policy option goes to the owner first)
- ADRs checked: ADR-0015 (the mirror folder is wholly the app's; its amendments of 2026-11-18 and 2026-11-19, the share's best-effort folder sync), ADR-0061 and its amendment of 2026-11-14; publish.md §11 "Limit (a cache folder on a network share: no build yet)"

## Situation
Today no mirror generation can be built in a cache folder on a network share (SMB) on Windows. The store's build, its `meta` read and the sidecar's reader all open the generation by `Path(<folder handle's path>).as_uri()`. On a share, the folder handle's path is the UNC path (`\\server\share\...`), even when a mapped drive letter opened it. What happens today: the open and the rebuild end as `engine.internal`, a generic "internal error". This fails closed: there is no mirror, and saves are refused as "not ready". But the user is never told why.

Facts from the probe on the runner (P8, every probed folder; a build of 2000 rows in rollback mode, then two readers while a third holds a read transaction):

| Folder | `as_uri()` form (today) | UNC URI form `file:////server/share/...` | Plain path |
|---|---|---|---|
| NTFS C: and D:, FAT32, exFAT, ReFS | works | works (same as `as_uri()` off a share) | works |
| The share mapped as `Z:` | works when given `Z:\...`, but the product never gives that: the handle's path there is `\\?\UNC\localhost\ftshare\...` | works | works |
| `\\localhost\ftshare` | refused: "unable to open database file" (`file://localhost/ftshare/...`: SQLite takes `localhost` as this machine and looks for `/ftshare/...` on the local drive) | works | works |
| `\\<machine name>\ftshare` (a real server name) | refused: "invalid uri authority" (SQLite accepts no server name in the authority) | works | works |

The UNC form also worked with a folder name holding `#`, `%` and a space (percent-encoded). SQLite's rollback journal was gone after each commit. Both readers counted every row.

So **a "drive letter required" policy would not help**: the adapter reports a mapped drive by its UNC path. Any policy has to be "not on a network share" (the adapter's `remote()`, which the probe saw True on all three share spellings and False on every local volume).

What else bears on it:
- **A ledger saved on a share is already refused** (`no_drive_id`, BL-0219, deferred to M11). The cache folder is a separate question: it could be on a share while the ledger is local.
- **Where the shell puts the cache folder on Windows is not decided yet.** Presumably the user's local app data (`AppData\Local`). Windows does not roam that folder, and folder redirection does not move it. So a cache folder on a share needs a deliberate move, or a future setting that offers one: rare.
- **S0132 built a mode for exactly this setup**, at your choice of yesterday (owner, 2026-11-19: "Best effort on a share (Recommended)"): `SyncRefused`, a refused folder sync counted as done in the mirror's own folder, noted in the job's decision. It runs only where a build can land, so today it is reached only by tests with a store that writes through the port.

Likelihood: low (above). Nothing is lost either way: the mirror is a rebuildable cache, and today's failure is closed. What is at stake is availability, plus a truthful message for the user.

## Options (cheapest first)
1. **Policy: a mirror cache folder on a network share is refused, with a named reason.** The open and the rebuild check `remote()` on the cache folder first and refuse before any build, with a message naming the folder and saying to keep the app's cache on a local disk. Cost: one check in the publish's `open_folder`, one refusal code and its text, and a runner test on the CI share's three spellings. A consequence to accept with it: S0132's `SyncRefused` mode would never be reached in the product. It could be removed (less code: the port exception, the publish's catch, the `sync_refused` decision field, the fake's form and their tests) or kept against a later change of mind. Removing it would be its own small slice, only with your words, since you chose that mode.
2. **Open SQLite in the UNC form on a share** (`file:////server/share/...`, percent-encoded) in the engine's build and `meta` read (`mirror/build.py`, `mirror/store.py`) and the sidecar's reader (`app/mirror/files.file_uri`). One small helper per package, since they share no code (ADR-0054). Local paths keep `as_uri()` byte for byte. Cost: the two helpers with their cases on every OS; S0132's share publish test turned to the real SQLite store; a runner test that builds, publishes and reads on the share's three spellings. S0132's best-effort mode then becomes reachable, as you chose it. Limits to state: probed only against Windows' own SMB server on this machine. A NAS (Samba) and SQLite's locking over it are not probed, and SQLite's documentation warns against network file systems for concurrent writers. The mirror has one writer, into a fresh file, and readers that only read, which makes that risk small, but it is a limit.
3. **Both:** option 2 now, plus option 1's named refusal for a share whose server refuses the open (today's `engine.internal` turned into a named reason). Cost: option 2's, plus the refusal's code and text.

## Recommendation
**Option 1, the policy.** It is the cheapest truthful outcome for a rare setup that the app's own default location never produces, and it matches how a ledger on a share is already treated (refused, BL-0219). Option 2 is cheap too and works on the runner. But it keeps a share-hosted mirror alive on a family of mechanisms (the mirror publish) that has needed repeated fix rounds, and it rests on locking behaviour probed only against Windows' own SMB server. If you would rather keep the "Best effort on a share" direction of yesterday's choice, option 2 is the one that serves it.

Review history of the mechanism each option touches (the mirror publish, `mirror/publish.py` and `mirror/store.py`): S0085 (1 round), S0087 (2 rounds), S0088 (2 rounds, 1 High fixed: the stamp), S0118, S0130 (1 round), S0132 (2 verifier rounds and the data-safety red team; its share part: one owner choice on the folder sync, then BL-0298 found). Option 1 adds one check before the folder work begins; option 2 changes the open of every generation on every OS (byte for byte the same off a share).

## Answer (owner, verbatim, with date)
