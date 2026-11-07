---------------------------- MODULE MirrorPublish ----------------------------
(***************************************************************************)
(* TLA+ model of the mirror's generation publish, design/specs/mirror/     *)
(* publish.md (S0085; BL-0113). Durability only (EP-19): what a reader     *)
(* reads through `current.db` is one whole generation (never a partial,   *)
(* incomplete or torn file), built from one state the ledger really had,  *)
(* and a request that reads twice reads the same generation twice. GC     *)
(* timing, the grace period's length, availability (a read that finds no  *)
(* file) and every liveness property are not here: they are stateful      *)
(* tests of the code (publish.md §10). MAPPING.md maps every rule and     *)
(* property to this file, both ways, and lists what is left out.          *)
(*                                                                         *)
(* World: a ledger of two files (1, 2), each holding a version number; an *)
(* outside editor writes a fresh version (it never writes back bytes a    *)
(* file held before: publish.md §11, the A-B-A limit); a save of ours cut *)
(* after its first file leaves an `.open` journal until recovery. The     *)
(* mirror folder holds generations 1..MaxGen (a database file and its     *)
(* rollback journal each) and the link `current.db`. One engine job at a  *)
(* time (ADR-0064): a publish job, or a save. Readers (the screens' reads,*)
(* an outside tool, the save's gate) resolve the link and read.           *)
(*                                                                         *)
(* Durability: each generation holds its current state and the set of     *)
(* states a power loss may leave (`pl`); the link likewise (`lkpl`). An   *)
(* unsynced file may come back torn; an unsynced entry, a deletion or a   *)
(* rename may be undone, each independently (publish.md §9's file-system  *)
(* assumptions, as engine-jobs §13 states them).                          *)
(*                                                                         *)
(* MUTANT selects one seeded bug ("none" = the design).                   *)
(***************************************************************************)
EXTENDS Integers, FiniteSets, Sequences, TLC

CONSTANTS
    MUTANT,         \* "none" or the name of one seeded bug (MAPPING.md §4)
    Readers,        \* the readers (each one request at a time)
    MaxGen,         \* generation numbers 1..MaxGen
    MaxBuilds, MaxReqs, MaxEdits, MaxCuts, MaxCrash, MaxAppCrash, MaxPower,
    MayIncomplete   \* TRUE: a build may report itself incomplete (`build_complete = 0`)

ASSUME MUTANT \in STRING /\ MaxGen \in Nat /\ MaxGen >= 1 /\ MayIncomplete \in BOOLEAN

----------------------------------------------------------------------------
(* Basic sets and values                                                   *)
Files  == {1, 2}
Gens   == 1..MaxGen
ABS    == "abs"     \* no database file at the name
PART   == "part"    \* a build still writing it (its journal beside it)
INC    == "inc"     \* a whole file whose build said it is incomplete
TORN   == "torn"    \* bytes a power loss left: any mix, which a reader cannot tell apart
DONE   == "done"    \* a whole, complete generation
NoRows == <<0, 0>>
Ct(k, rows, lab) == [k |-> k, rows |-> rows, lab |-> lab]   \* a file's content: kind, the ledger state its rows
                                                           \* came from, and the state its `meta` names
Empty  == Ct(ABS, NoRows, NoRows)
Torn   == Ct(TORN, NoRows, NoRows)
Part   == Ct(PART, NoRows, NoRows)
Max(S) == CHOOSE x \in S : \A y \in S : y <= x

NoGen == [c |-> Empty, jl |-> FALSE, pl |-> {<<Empty, FALSE>>}, inc |-> 0]
NoCtl == [pc |-> "none", g |-> 0, lab |-> NoRows, rows |-> NoRows, stale |-> FALSE]
NoRd  == [st |-> "idle", g |-> 0, inc |-> 0, s1 |-> Empty, s2 |-> Empty]

----------------------------------------------------------------------------
VARIABLES
    disk,   \* the ledger: one version per file
    jopen,  \* a cut save's `.open` journal exists
    jpre,   \* its pre versions
    jpost,  \* the version it wrote into file 1
    nv,     \* the next fresh version
    hist,   \* ghost: every ledger state the disk had while no `.open` journal existed (the committed states)
    gens,   \* per generation: database content `c`, journal present `jl`, power-loss outcomes `pl` (pairs
            \* <<content, journal present>>), incarnation `inc` (a new file at the name is a new inode)
    lk,     \* the generation `current.db` names (0: no link)
    lkpl,   \* what a power loss may leave at `current.db`
    ctl,    \* the publish job's progress
    rd,     \* per reader: its request's state, the generation it resolved, its snapshots
    n,      \* counters that bound the environment, the user and the readers
    gh      \* ghost: branches reached (for the vacuity witnesses)

vars == <<disk, jopen, jpre, jpost, nv, hist, gens, lk, lkpl, ctl, rd, n, gh>>
ledger == <<disk, jopen, jpre, jpost, nv, hist>>

Init ==
    /\ disk = <<1, 1>> /\ jopen = FALSE /\ jpre = <<1, 1>> /\ jpost = 0 /\ nv = 2
    /\ hist = {<<1, 1>>}
    /\ gens = [g \in Gens |-> NoGen]
    /\ lk = 0 /\ lkpl = {0}
    /\ ctl = NoCtl
    /\ rd = [r \in Readers |-> NoRd]
    /\ n = [b |-> 0, e |-> 0, c |-> 0, cr |-> 0, ac |-> 0, pw |-> 0, q |-> [r \in Readers |-> 0]]
    /\ gh = [moved |-> FALSE, jref |-> FALSE, incref |-> FALSE, power |-> FALSE]

----------------------------------------------------------------------------
(* Helpers                                                                 *)
Named(g)  == gens[g].c.k # ABS \/ gens[g].jl                   \* a name of generation g is in the folder
\* §3: the next number is above every number any name in the folder carries, and above the link's
NextGen   == IF MUTANT = "reuse_number" THEN lk + 1
             ELSE 1 + Max({lk} \cup {g \in Gens : Named(g)})
\* §6: the previous generation: the highest-numbered file below the current one
Prev      == LET below == {g \in Gens : g < lk /\ gens[g].c.k # ABS} IN IF below = {} THEN 0 ELSE Max(below)
Keep      == {lk, Prev} \ {0}
AddHist(d, jo) == IF jo THEN hist ELSE hist \cup {d}
\* a directory sync of the mirror folder: every entry (file, journal, link) durable as it is now; a file's data
\* stays what its own sync made it
DirSynced(x) == IF x.c.k = ABS THEN {<<Empty, x.jl>>}
                ELSE LET live == {p \in x.pl : p[1].k # ABS}
                     IN IF live = {} THEN {<<x.c, x.jl>>} ELSE {<<p[1], x.jl>> : p \in live}
DirSync   == /\ gens' = [g \in Gens |-> [gens[g] EXCEPT !.pl = DirSynced(gens[g])]]
             /\ lkpl' = {lk}
Go(pc)    == ctl' = [ctl EXCEPT !.pc = pc]

----------------------------------------------------------------------------
(* The publish job (publish.md §4-§8), one engine job at a time            *)
StartPublish ==
    /\ ctl.pc = "none" /\ n.b < MaxBuilds
    /\ ctl' = [NoCtl EXCEPT !.pc = "clean"]
    /\ n' = [n EXCEPT !.b = @ + 1]
    /\ UNCHANGED <<ledger, gens, lk, lkpl, rd, gh>>

\* §6: the cleanup, first in every job: any database file not kept, then its journal once the file is gone. It
\* may stop anywhere (an I/O error, or the grace period keeps a file): any subset of the deletions happens.
P_CleanDb(g) ==
    /\ ctl.pc = "clean" /\ g \notin Keep /\ gens[g].c.k # ABS
    /\ gens' = [gens EXCEPT ![g] = [c |-> Empty, jl |-> @.jl, inc |-> @.inc + 1,
                                    pl |-> @.pl \cup {<<Empty, p[2]>> : p \in @.pl}]]
    /\ UNCHANGED <<ledger, lk, lkpl, ctl, rd, n, gh>>
P_CleanJl(g) ==
    /\ ctl.pc = "clean" /\ g \notin Keep /\ gens[g].c.k = ABS /\ gens[g].jl
    /\ gens' = [gens EXCEPT ![g] = [@ EXCEPT !.jl = FALSE, !.pl = @ \cup {<<p[1], FALSE>> : p \in @}]]
    /\ UNCHANGED <<ledger, lk, lkpl, ctl, rd, n, gh>>
P_CleanDone ==
    /\ ctl.pc = "clean" /\ Go("obs1")
    /\ UNCHANGED <<ledger, gens, lk, lkpl, rd, n, gh>>

\* §4 (the load, engine-jobs §6.7): the ledger set observed file by file, then each file read
P_Obs(i) ==
    /\ ctl.pc = "obs" \o ToString(i)
    /\ ctl' = [ctl EXCEPT !.lab[i] = disk[i], !.pc = IF i = 1 THEN "obs2" ELSE "read1"]
    /\ UNCHANGED <<ledger, gens, lk, lkpl, rd, n, gh>>
P_Read(i) ==
    /\ ctl.pc = "read" \o ToString(i)
    /\ ctl' = [ctl EXCEPT !.rows[i] = disk[i], !.pc = IF i = 1 THEN "read2" ELSE "create"]
    /\ UNCHANGED <<ledger, gens, lk, lkpl, rd, n, gh>>

\* §4: the build creates its own file under the next number (exclusive: a file at the name refuses it); a
\* journal left at that name would be played back into the new file (the stale-journal rule, §5)
P_Create ==
    /\ ctl.pc = "create"
    /\ IF MUTANT = "build_in_place" /\ lk # 0
       THEN \* ADR-0015's rejected alternative: rebuild the live file in place; its tables are rewritten and
            \* committed one by one, so a reader in between finds a whole file with some tables missing
            /\ gens' = [gens EXCEPT ![lk] = [@ EXCEPT !.c = Ct(INC, @.rows, @.lab),
                                                      !.pl = @ \cup {<<Ct(INC, gens[lk].c.rows, gens[lk].c.lab), FALSE>>, <<Torn, FALSE>>}]]
            /\ ctl' = [ctl EXCEPT !.pc = "commit", !.g = lk, !.stale = FALSE]
       ELSE LET N == NextGen IN
            IF N > MaxGen \/ gens[N].c.k # ABS
            THEN /\ ctl' = NoCtl /\ gens' = gens        \* out of names in this world, or the exclusive create refused
            ELSE /\ gens' = [gens EXCEPT ![N] = [c |-> Part, jl |-> TRUE, inc |-> @.inc + 1,
                                                  pl |-> @.pl \cup {<<Part, TRUE>>, <<Torn, FALSE>>, <<Torn, TRUE>>}]]
                 /\ ctl' = [ctl EXCEPT !.pc = "commit", !.g = N, !.stale = gens[N].jl]
    /\ UNCHANGED <<ledger, lk, lkpl, rd, n, gh>>

\* the build's one transaction commits: SQLite deletes the journal (not yet durable); `build_complete` is 1, or
\* 0 when a table is not built (MayIncomplete)
P_Commit ==
    /\ ctl.pc = "commit"
    /\ \E complete \in (IF MayIncomplete THEN BOOLEAN ELSE {TRUE}) :
         LET c == IF ctl.stale THEN Torn
                  ELSE Ct(IF complete THEN DONE ELSE INC, ctl.rows, ctl.lab)
         IN gens' = [gens EXCEPT ![ctl.g] = [@ EXCEPT !.c = c, !.jl = FALSE,
                        !.pl = @ \cup {<<Torn, FALSE>>, <<Torn, TRUE>>, <<c, FALSE>>, <<c, TRUE>>}]]
    /\ Go("check")
    /\ UNCHANGED <<ledger, lk, lkpl, rd, n, gh>>

\* §4: a build that says it is incomplete is never published; its file is left for the cleanup
P_Check ==
    /\ ctl.pc = "check"
    /\ IF gens[ctl.g].c.k = INC /\ MUTANT # "publish_incomplete"
       THEN ctl' = NoCtl /\ gh' = [gh EXCEPT !.incref = TRUE]
       ELSE Go(IF MUTANT = "no_file_sync" THEN "dsync" ELSE "sync") /\ gh' = gh
    /\ UNCHANGED <<ledger, gens, lk, lkpl, rd, n>>

\* §4: the file's data synced (F_FULLFSYNC on macOS): every outcome in which the file exists holds these bytes
P_SyncFile ==
    /\ ctl.pc = "sync"
    /\ LET g == ctl.g IN
       gens' = [gens EXCEPT ![g] = [@ EXCEPT !.pl = {<<IF p[1].k = ABS THEN p[1] ELSE gens[g].c, p[2]>> : p \in @}]]
    /\ Go("dsync")
    /\ UNCHANGED <<ledger, lk, lkpl, rd, n, gh>>

\* §4: the folder synced: the new file's name and its journal's deletion durable
P_DirSync ==
    /\ ctl.pc = "dsync" /\ DirSync
    /\ Go(IF MUTANT = "no_recheck" THEN "jcheck" ELSE "re1")
    /\ UNCHANGED <<ledger, lk, rd, n, gh>>

\* §4 (engine-jobs §7): just before the swap, the ledger set observed again; a moved file ends the job
P_ReObs(i) ==
    /\ ctl.pc = "re" \o ToString(i)
    /\ IF disk[i] # ctl.lab[i]
       THEN ctl' = NoCtl /\ gh' = [gh EXCEPT !.moved = TRUE]
       ELSE Go(IF i = 1 THEN "re2" ELSE "jcheck") /\ gh' = gh
    /\ UNCHANGED <<ledger, gens, lk, lkpl, rd, n>>

\* §4 (engine-jobs §7): no `.open` journal of this workspace, else the job ends (`WRITES_PAUSED`)
P_JCheck ==
    /\ ctl.pc = "jcheck"
    /\ IF jopen /\ MUTANT # "no_open_check"
       THEN ctl' = NoCtl /\ gh' = [gh EXCEPT !.jref = TRUE]
       ELSE Go("swap") /\ gh' = gh
    /\ UNCHANGED <<ledger, gens, lk, lkpl, rd, n>>

\* §4: a temporary link to the new file, renamed over `current.db` (one step: the temporary link alone is
\* never read); until the folder's sync a power loss may keep the old link
P_Swap ==
    /\ ctl.pc = "swap"
    /\ lk' = ctl.g /\ lkpl' = lkpl \cup {ctl.g}
    /\ Go("lsync")
    /\ UNCHANGED <<ledger, gens, rd, n, gh>>

\* §4: the folder synced again; then the job answers "published"
P_LinkSync ==
    /\ ctl.pc = "lsync" /\ DirSync
    /\ ctl' = NoCtl
    /\ UNCHANGED <<ledger, lk, rd, n, gh>>

----------------------------------------------------------------------------
(* The ledger: our cut save, its recovery, the outside editor              *)

\* a save of ours (a job of its own: never beside a publish) ended by an engine death after its first file:
\* the `.open` journal stays until recovery (engine-jobs §6.6, §8.1)
SaveCut ==
    /\ ctl.pc = "none" /\ ~jopen /\ n.c < MaxCuts
    /\ jopen' = TRUE /\ jpre' = disk /\ jpost' = nv
    /\ disk' = [disk EXCEPT ![1] = nv] /\ nv' = nv + 1
    /\ n' = [n EXCEPT !.c = @ + 1]
    /\ UNCHANGED <<hist, gens, lk, lkpl, ctl, rd, gh>>

\* recovery (a job): file 1 back to its pre bytes, the journal resolved; it stops (and the journal stays) when
\* another program changed a file the journal names
Recover ==
    /\ ctl.pc = "none" /\ jopen /\ disk[1] = jpost /\ disk[2] = jpre[2]
    /\ disk' = jpre /\ jopen' = FALSE /\ hist' = hist \cup {jpre}
    /\ UNCHANGED <<jpre, jpost, nv, gens, lk, lkpl, ctl, rd, n, gh>>

\* an outside program writes a file at any time, with bytes the file never held before
Editor(i) ==
    /\ n.e < MaxEdits
    /\ disk' = [disk EXCEPT ![i] = nv] /\ nv' = nv + 1
    /\ hist' = AddHist(disk', jopen)
    /\ n' = [n EXCEPT !.e = @ + 1]
    /\ UNCHANGED <<jopen, jpre, jpost, gens, lk, lkpl, ctl, rd, gh>>

----------------------------------------------------------------------------
(* Readers (§7): the screens' reads, an outside tool, the save's gate      *)

\* a request resolves `current.db` once (one `readlink`) and pins that generation's name
R_Resolve(r) ==
    /\ rd[r].st = "idle" /\ n.q[r] < MaxReqs /\ lk # 0
    /\ rd' = [rd EXCEPT ![r] = [NoRd EXCEPT !.st = "res", !.g = lk, !.inc = gens[lk].inc]]
    /\ n' = [n EXCEPT !.q[r] = @ + 1]
    /\ UNCHANGED <<ledger, gens, lk, lkpl, ctl, gh>>

\* opening a file read-only: gone (no file, or another file at the name), refused (a journal beside it: SQLite
\* finds it hot and a read-only connection may not roll it back), or read (an open descriptor: what it reads
\* never changes while it is open, even when the name is deleted)
Opens(g, inc) == g # 0 /\ gens[g].inc = inc /\ gens[g].c.k \notin {ABS, PART} /\ ~gens[g].jl

R_Read1(r) ==
    /\ rd[r].st = "res"
    /\ rd' = [rd EXCEPT ![r] = IF Opens(rd[r].g, rd[r].inc)
                               THEN [@ EXCEPT !.st = "one", !.s1 = gens[rd[r].g].c]
                               ELSE NoRd]
    /\ UNCHANGED <<ledger, gens, lk, lkpl, ctl, n, gh>>

\* the request's second read opens the pinned name again (a new connection)
R_Read2(r) ==
    /\ rd[r].st = "one"
    /\ LET g   == IF MUTANT = "reresolve_per_read" THEN lk ELSE rd[r].g
           inc == IF MUTANT # "reresolve_per_read" THEN rd[r].inc ELSE IF lk = 0 THEN 0 ELSE gens[lk].inc
       IN rd' = [rd EXCEPT ![r] = IF Opens(g, inc) THEN [@ EXCEPT !.st = "two", !.s2 = gens[g].c] ELSE NoRd]
    /\ UNCHANGED <<ledger, gens, lk, lkpl, ctl, n, gh>>

R_End(r) ==
    /\ rd[r].st = "two" /\ rd' = [rd EXCEPT ![r] = NoRd]
    /\ UNCHANGED <<ledger, gens, lk, lkpl, ctl, n, gh>>

----------------------------------------------------------------------------
(* The OS                                                                  *)
EngineCrash ==
    /\ ctl.pc # "none" /\ n.cr < MaxCrash
    /\ ctl' = NoCtl /\ n' = [n EXCEPT !.cr = @ + 1]
    /\ UNCHANGED <<ledger, gens, lk, lkpl, rd, gh>>

\* the app (sidecar and engine) dies: its readers go; the folder stays as it is
AppCrash ==
    /\ n.ac < MaxAppCrash
    /\ ctl' = NoCtl /\ rd' = [r \in Readers |-> NoRd] /\ n' = [n EXCEPT !.ac = @ + 1]
    /\ UNCHANGED <<ledger, gens, lk, lkpl, gh>>

\* the machine loses power: every reader goes (outside tools too); each generation and the link keep one of
\* their possible outcomes, chosen independently. The ledger's own durability is engine-jobs' model.
PowerLoss ==
    /\ n.pw < MaxPower
    /\ \E pick \in [Gens -> UNION {gens[g].pl : g \in Gens}], l \in lkpl :
         /\ \A g \in Gens : pick[g] \in gens[g].pl
         /\ gens' = [g \in Gens |-> [c |-> pick[g][1], jl |-> pick[g][2], pl |-> {pick[g]}, inc |-> gens[g].inc + 1]]
         /\ lk' = l /\ lkpl' = {l}
    /\ ctl' = NoCtl /\ rd' = [r \in Readers |-> NoRd]
    /\ n' = [n EXCEPT !.pw = @ + 1] /\ gh' = [gh EXCEPT !.power = TRUE]
    /\ UNCHANGED ledger

----------------------------------------------------------------------------
EngineStep ==
    \/ StartPublish \/ P_CleanDone \/ P_Create \/ P_Commit \/ P_Check \/ P_SyncFile \/ P_DirSync
    \/ P_JCheck \/ P_Swap \/ P_LinkSync \/ SaveCut \/ Recover
    \/ \E g \in Gens : P_CleanDb(g) \/ P_CleanJl(g)
    \/ \E i \in Files : P_Obs(i) \/ P_Read(i) \/ P_ReObs(i)

ReaderStep ==
    \E r \in Readers : R_Resolve(r) \/ R_Read1(r) \/ R_Read2(r) \/ R_End(r)

EnvStep ==
    \/ EngineCrash \/ AppCrash \/ PowerLoss
    \/ \E i \in Files : Editor(i)

Next == EngineStep \/ ReaderStep \/ EnvStep

Spec == Init /\ [][Next]_vars

----------------------------------------------------------------------------
(* Properties (publish.md §10; BL-0113)                                    *)

\* Every read a reader accepted read a whole, complete generation: never a file a build is still writing, one
\* whose build said it is incomplete, or bytes a power loss tore.
ReadersSeeCompleteGeneration ==
    \A r \in Readers :
        /\ rd[r].st \in {"one", "two"} => rd[r].s1.k = DONE
        /\ rd[r].st = "two" => rd[r].s2.k = DONE

\* What a reader read is the build of one state the ledger really had while no save of ours was half done,
\* its `meta` names exactly that state, and a request's two reads read the same generation.
SnapshotIsSomeCommittedState ==
    \A r \in Readers :
        /\ (rd[r].st \in {"one", "two"} /\ rd[r].s1.k = DONE) =>
              rd[r].s1.rows \in hist /\ rd[r].s1.lab = rd[r].s1.rows
        /\ rd[r].st = "two" => rd[r].s2 = rd[r].s1

----------------------------------------------------------------------------
(* Vacuity witnesses (MAPPING.md §4.2): each base world must reach each    *)
(* branch, so TLC must report the negation violated.                       *)
W_ReadTwice       == ~\E r \in Readers : rd[r].st = "two"
W_ReadOldAfterNew == ~\E r \in Readers : rd[r].st \in {"one", "two"} /\ lk # 0 /\ rd[r].g < lk
W_ReadDeleted     == ~\E r \in Readers : rd[r].st = "one" /\ gens[rd[r].g].c.k = ABS
W_ReadAfterPower  == ~\E r \in Readers : rd[r].st \in {"one", "two"} /\ gh.power
W_TornLeft        == ~\E g \in Gens : gens[g].c.k = TORN
W_LinkReverted    == ~(gh.power /\ lk # 0 /\ \E g \in Gens : g > lk /\ gens[g].c.k = DONE)
W_MovedRefused    == ~gh.moved
W_OpenRefused     == ~gh.jref
W_IncompleteRefused == ~gh.incref
=============================================================================
