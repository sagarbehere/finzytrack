------------------------------ MODULE EngineJobs ------------------------------
(***************************************************************************)
(* TLA+ model of the durable core of design/specs/engine-jobs.md: the     *)
(* save (§6.1 step 8, §6.4), its rollback, recovery (§8.1), create and    *)
(* copy (§8.2), under engine crashes, app crashes, power loss, I/O errors *)
(* and an outside editor. Durability only (EP-19; ADR-0068 D5, S0072): no *)
(* committed change is lost or torn, and recovery returns a committed     *)
(* state. The queue, the switch, the boot screens, quit, Cancel, advice   *)
(* and liveness are not here: their tables and stateful tests check them. *)
(* The save, its rollback and recovery follow the code as built (S0072,   *)
(* BL-0161): writes go through a staging name, an outside save met at a   *)
(* name of ours is moved to a kept name (never swapped back), and nothing *)
(* at a path the save was creating is ever touched. MAPPING.md maps every *)
(* rule and property to this file, both ways, and lists what is left out. *)
(*                                                                         *)
(* World: workspaces W1 = {R, N, I}, W2 = {R, J}, W3 = {K}; W1 and W2     *)
(* share R; K lives on a disk that may become unreachable. A save of W1   *)
(* replaces R and creates N (a later save replaces N too) and leaves the  *)
(* include I alone. Journals b2 (W2: R, J) and b3 (W3: K) may be left at  *)
(* boot by crashed saves; s1..s3 are the journals of the saves made here. *)
(* File contents are version numbers: 0 absent, -1 torn, 1 initial, 2 the *)
(* boot journals' post bytes, 3..5 the posts of s1..s3, 11..15 and 21..25 *)
(* an outside editor's bytes.                                              *)
(*                                                                         *)
(* Durability: every name holds its current content and the set of        *)
(* contents it may hold after a power loss (`pl`, `tpl`, `sgpl`, `bkpl`).  *)
(* A write that is not synced adds the torn and the absent outcomes; a    *)
(* swap or rename that is not yet dir-synced keeps the old outcome        *)
(* possible (a pending swap couples the file and its temp: `cp`). A       *)
(* folder sync makes its entries durable, not the data of the files they  *)
(* name: a file's data an in-place write left unsynced may fall back      *)
(* (`dpl`) until that file's own sync (S0101, BL-0243; spec §13).         *)
(*                                                                         *)
(* The swap is assumed (spec §13): the best-effort mode on volumes        *)
(* without it (§6.8, ADR-0068 D2) is outside this model.                  *)
(*                                                                         *)
(* MUTANT selects one seeded bug ("none" = the design as built).          *)
(***************************************************************************)
EXTENDS Integers, FiniteSets, Sequences, TLC

CONSTANTS
    MUTANT,         \* "none" or the name of one seeded bug (MAPPING.md §6)
    BootJ,          \* subset of {"b2","b3"}: journals left by crashed saves at boot
    BootTemps,      \* TRUE: a boot journal's file may have a staging file or a temp of ours beside it
    BootTorn,       \* TRUE: b3's journal is torn: its file list cannot be read
    BootLink,       \* TRUE: after the crash, R's folder was replaced by a link (the path changer)
    EdFiles,        \* the ledger files the outside editor may write
    EdDurable,      \* TRUE: the editor's saves are durable (it syncs its folder); FALSE: §13's weaker file system
    MaxSaves, MaxEdits, MaxCrash, MaxAppCrash, MaxPower, MaxStarts,
    MaxUnreach, MaxLink, MaxIO, MaxDrop,
    DoCopy,         \* TRUE: the user may copy W2
    CreateTarget    \* "none" | "absent" | "empty" | "nonempty"

ASSUME MUTANT \in STRING /\ BootJ \subseteq {"b2","b3"}
ASSUME BootTorn => "b3" \in BootJ
ASSUME CreateTarget \in {"none","absent","empty","nonempty"}
ASSUME MaxSaves <= 3

----------------------------------------------------------------------------
(* Basic sets                                                              *)
Files   == {"R","N","I","J","K"}
Idx(f)  == CASE f = "R" -> 1 [] f = "N" -> 2 [] f = "I" -> 3 [] f = "J" -> 4 [] f = "K" -> 5
LS(w)   == CASE w = 1 -> {"R","N","I"} [] w = 2 -> {"R","J"} [] w = 3 -> {"K"} [] OTHER -> {}
Tg      == {"R","N"}            \* what a W1 save writes
ABS     == 0
TORN    == -1
V0      == 1
BPOST   == 2
SaveIds == {"s1","s2","s3"}
BootIds == {"b2","b3"}
JIds    == BootIds \cup SaveIds
JOrder  == <<"b2","b3","s1","s2","s3">>
SId(i)  == JOrder[i + 2]
PostV(s) == CASE s = "s1" -> 3 [] s = "s2" -> 4 [] s = "s3" -> 5 [] OTHER -> BPOST
JFiles(j) == CASE j = "b2" -> {"R","J"} [] j = "b3" -> {"K"} [] OTHER -> Tg
TSlots   == UNION {{<<j, f>> : f \in JFiles(j)} : j \in JIds}   \* the names of ours beside each file a journal names
ROrd(j)  == CASE j = "b2" -> <<"R","J">> [] j = "b3" -> <<"K">> [] OTHER -> <<"R","N">>  \* root first
RecOrd(j) == CASE j = "b2" -> <<"J","R">> [] j = "b3" -> <<"K">> [] OTHER -> <<"N","R">> \* the record's order
WOrd     == <<"N","R">>        \* the save's order: children first, the root last (§6.1 step 8.3)
Frn(f, v) == IF v = 10 + Idx(f) THEN 20 + Idx(f) ELSE 10 + Idx(f)
CL       == {"closed"}
OpenSt   == {"open","topen"}
Names    == {"a","m","o","x"}   \* create target: a = a file the job writes, m = manifest, o = another name, x = prior content
ZeroFP   == [f \in Files |-> 0]
Min(S)   == CHOOSE x \in S : \A y \in S : x <= y

NoJ == [st |-> "none", pl |-> {"none"}, files |-> {}, pre |-> ZeroFP, post |-> ZeroFP, why |-> "", old |-> FALSE]
BootSt(j) == IF j = "b3" /\ BootTorn THEN "topen" ELSE "open"
BootJRec(j) == [NoJ EXCEPT !.st = BootSt(j), !.pl = {BootSt(j)}, !.old = TRUE,
                  !.files = JFiles(j),
                  !.pre = [f \in Files |-> IF f \in JFiles(j) THEN V0 ELSE 0],
                  !.post = [f \in Files |-> IF f \in JFiles(j) THEN BPOST ELSE 0]]
NoRun == [k |-> "none", id |-> "none"]
NoCtl == [pc |-> "none", s |-> "none", plan |-> ZeroFP, fi |-> 1, rj |-> "none", tried |-> {},
          rbt |-> "none", repl |-> FALSE, put |-> {}, taken |-> FALSE]

----------------------------------------------------------------------------
VARIABLES
    fs,    \* ledger files cur/pl; per journal and file: save temps tc/tpl, staging files sg/sgpl, restore temps
           \* rt, kept copies kp; backups bk/bkpl per save; pending swaps cp/cpn/cpj; dpl: per file, what its data may
           \* fall back to after a power loss once its entry is durable (an unsynced in-place write, S0101)
    jr,    \* the records in local/*/journal/: one per journal id
    env,   \* the path changer's link, the unreachable disk, bytes written outside the ledger
    tgt,   \* the create's target folder: names c/pl, folder exists
    app,   \* "recover" (recover-all runs before any other work), "run", or "dead"
    run,   \* the job the engine runs (one at a time, ADR-0064)
    ctl,   \* the running job's progress
    n,     \* counters that bound the environment and the user
    gh     \* ghost: what a save was told, and history the properties need

vars == <<fs, jr, env, tgt, app, run, ctl, n, gh>>

----------------------------------------------------------------------------
(* Initial state: the first start                                          *)
BootFiles == (IF "b2" \in BootJ THEN {"R","J"} ELSE {}) \cup (IF "b3" \in BootJ THEN {"K"} ELSE {})
BI(f) == IF f \in BootFiles THEN {V0, BPOST} ELSE {V0}
\* what a crashed save may have left beside a boot journal's file: at pre, a cut write at the staging name or
\* its synced temp; at post, the old object the swap moved to the temp name
BTemps(j, v) == IF j \in BootJ /\ BootTemps
                THEN (IF v = BPOST THEN {<<ABS, ABS>>, <<V0, ABS>>}
                      ELSE {<<ABS, ABS>>, <<ABS, TORN>>, <<BPOST, ABS>>})
                ELSE {<<ABS, ABS>>}
RecJob == [k |-> "recover", id |-> "rc"]
StartCtl == [NoCtl EXCEPT !.pc = IF MUTANT = "rv_boot_skips_recovery" THEN "none" ELSE "rc_init"]
Init ==
  \E cR \in BI("R"), cJ \in BI("J"), cK \in BI("K"):
  \E tR \in BTemps("b2", cR), tJ \in BTemps("b2", cJ), tK \in BTemps("b3", cK):
    LET c  == [f \in Files |-> CASE f = "R" -> cR [] f = "J" -> cJ [] f = "K" -> cK
                                 [] f = "N" -> ABS [] OTHER -> V0]
        tp(x) == CASE x = <<"b2","R">> -> tR [] x = <<"b2","J">> -> tJ [] x = <<"b3","K">> -> tK
                   [] OTHER -> <<ABS, ABS>>
    IN
    /\ fs = [cur |-> c, pl |-> [f \in Files |-> {c[f]}],
             tc |-> [x \in TSlots |-> tp(x)[1]], tpl |-> [x \in TSlots |-> {tp(x)[1]}],
             sg |-> [x \in TSlots |-> tp(x)[2]], sgpl |-> [x \in TSlots |-> {tp(x)[2]}],
             rt |-> [x \in TSlots |-> ABS], kp |-> [x \in TSlots |-> {}],
             bk |-> [s \in SaveIds |-> ABS], bkpl |-> [s \in SaveIds |-> {ABS}],
             dpl |-> [f \in Files |-> {}],
             cp |-> [f \in Files |-> {}], cpn |-> [f \in Files |-> {}], cpj |-> [f \in Files |-> "none"]]
    /\ jr = [j \in JIds |-> IF j \in BootJ THEN BootJRec(j) ELSE NoJ]
    /\ env = [lnk |-> BootLink, unr |-> FALSE, outside |-> 0]
    /\ tgt = [ex |-> CreateTarget \in {"empty","nonempty"},
              c  |-> [x \in Names |-> IF x = "x" /\ CreateTarget = "nonempty" THEN "frn" ELSE "no"],
              pl |-> [x \in Names |-> {IF x = "x" /\ CreateTarget = "nonempty" THEN "frn" ELSE "no"}]]
    \* the start recovers every journal before any other work (ADR-0063, INV-32); seeded bug rv_boot_skips_recovery:
    \* it does not
    /\ app = IF MUTANT = "rv_boot_skips_recovery" THEN "run" ELSE "recover"
    /\ run = IF MUTANT = "rv_boot_skips_recovery" THEN NoRun ELSE RecJob
    /\ ctl = StartCtl
    /\ n = [ed |-> 0, crash |-> 0, ac |-> 0, pw |-> 0, start |-> 0, unr |-> 0, link |-> 0, io |-> 0,
            drop |-> 0, save |-> 0, create |-> 0, copy |-> 0]
    /\ gh = [told |-> [s \in SaveIds |-> "none"], pre |-> [s \in SaveIds |-> ZeroFP],
             ed |-> [f \in Files |-> 0], edSeen |-> [f \in Files |-> FALSE],
             dropped |-> {}, ovBefore |-> FALSE, ovAfter |-> FALSE, outW |-> FALSE]

----------------------------------------------------------------------------
(* Helpers                                                                 *)
Busy    == run.k # "none"
Step(p) == Busy /\ ctl.pc = p
FP(w)   == [f \in Files |-> IF f \in LS(w) THEN fs.cur[f] ELSE 0]
\* an unresolved journal that names a file of the files F (a torn one names every file: its list cannot be read)
OpenOn(F) == {j \in JIds : jr[j].st = "topen" \/ (jr[j].st = "open" /\ jr[j].files \cap F # {})}
SetTold(s, m) == gh' = [gh EXCEPT !.told[s] = m]
\* §6.6 / §11: "unchanged" only after the targets were hashed again and equal their pre bytes
Rehash(s) == IF \A f \in Tg : fs.cur[f] = gh.pre[s][f] THEN "unchanged" ELSE "changed"
\* §6.6: the outcome after an engine death, a pure function of the record and the file hashes
DiskOutcome(s) == CASE jr[s].st \in CL -> "saved"
                    [] jr[s].st = "resolved" -> "notsaved"
                    [] jr[s].st \in OpenSt \cup {"kept"} -> "unfinished"
                    [] OTHER -> Rehash(s)
EndJob == run' = NoRun /\ ctl' = NoCtl

----------------------------------------------------------------------------
(* USER ACTIONS: what starts work, and the way out of a stopped journal     *)

\* a confirmed save of W1 (the preview read the plan's fingerprint just before: the plan is the disk now)
StartSave ==
  /\ app = "run" /\ ~Busy /\ n.save < MaxSaves
  /\ LET s == SId(n.save + 1) IN
     /\ run' = [k |-> "save", id |-> s]
     /\ ctl' = [NoCtl EXCEPT !.pc = "s_gate", !.s = s, !.plan = FP(1)]
     /\ n' = [n EXCEPT !.save = @ + 1]
     /\ gh' = [gh EXCEPT !.pre[s] = FP(1)]
  /\ UNCHANGED <<fs, jr, env, tgt, app>>

StartCreate ==
  /\ app = "run" /\ ~Busy /\ CreateTarget # "none" /\ n.create = 0
  /\ run' = [k |-> "create", id |-> "cr"] /\ ctl' = [NoCtl EXCEPT !.pc = "cr_tgt"]
  /\ n' = [n EXCEPT !.create = 1]
  /\ UNCHANGED <<fs, jr, env, tgt, app, gh>>

StartCopy ==
  /\ app = "run" /\ ~Busy /\ DoCopy /\ n.copy = 0
  /\ run' = [k |-> "copy", id |-> "cp"] /\ ctl' = [NoCtl EXCEPT !.pc = "cp_chk"]
  /\ n' = [n EXCEPT !.copy = 1]
  /\ UNCHANGED <<fs, jr, env, tgt, app, gh>>

\* recover-all again in the session: Retry, Back or a switch, which recover first (ADR-0063; spec §9.1)
StartRecover ==
  /\ app = "run" /\ ~Busy /\ MUTANT # "rv_boot_skips_recovery"
  /\ \E j \in JIds : jr[j].st \in OpenSt
  /\ app' = "recover" /\ run' = RecJob /\ ctl' = [NoCtl EXCEPT !.pc = "rc_init"]
  /\ UNCHANGED <<fs, jr, env, tgt, n, gh>>

\* the path guard (§6.3): a recorded path that no longer resolves to its canonical path
PathMoved(f) == env.lnk /\ f = "R"
Guarded == MUTANT \notin {"no_path_guard", "fb_no_guard"}
\* "Keep the files as they are now" (§8.1) for a journal recovery stopped: writes nothing to the ledger files;
\* first durable_syncs every file and folder the journal names, then renames the journal .kept (synced). Refused
\* (`path_moved`, writing nothing) while a folder a readable journal names is not at its recorded path: what stands
\* there may not be what Keep can sync (red team r1 R1-3; S0100, BL-0224); a torn journal names no folder
Keep(j) ==
  /\ app = "run" /\ ~Busy /\ jr[j].st \in OpenSt /\ jr[j].why # ""
  /\ jr[j].st = "topen" \/ \A f \in jr[j].files : ~(PathMoved(f) /\ Guarded)
  /\ jr' = [jr EXCEPT ![j].st = "kept", ![j].pl = {"kept"}]
  /\ fs' = IF MUTANT = "keep_no_sync" \/ jr[j].st = "topen" THEN fs   \* a torn journal names nothing to sync
           ELSE LET F == jr[j].files IN
                [fs EXCEPT !.pl = [f \in Files |-> IF f \in F THEN {fs.cur[f]} ELSE fs.pl[f]],
                           !.cp = [f \in Files |-> IF f \in F THEN {} ELSE fs.cp[f]],
                           !.cpn = [f \in Files |-> IF f \in F THEN {} ELSE fs.cpn[f]],
                           !.cpj = [f \in Files |-> IF f \in F THEN "none" ELSE fs.cpj[f]],
                           !.dpl = [f \in Files |-> IF f \in F THEN {} ELSE fs.dpl[f]],
                           !.tpl = [x \in TSlots |-> IF x[2] \in F THEN {fs.tc[x]} ELSE fs.tpl[x]],
                           !.sgpl = [x \in TSlots |-> IF x[2] \in F THEN {fs.sg[x]} ELSE fs.sgpl[x]]]
  /\ UNCHANGED <<env, tgt, app, run, ctl, n, gh>>

\* the next start after the app ended (a crash or a power loss): recover-all first
Relaunch ==
  /\ app = "dead" /\ n.start < MaxStarts
  /\ n' = [n EXCEPT !.start = @ + 1]
  \* every journal on disk now is "from before this start" (INV-32); the reasons an earlier recover-all gave are
  \* cleared, so only this start's recover-all can make a journal blocking
  /\ jr' = [j \in JIds |-> IF jr[j].st \in OpenSt THEN [jr[j] EXCEPT !.old = TRUE, !.why = ""] ELSE jr[j]]
  /\ app' = IF MUTANT = "rv_boot_skips_recovery" THEN "run" ELSE "recover"
  /\ run' = IF MUTANT = "rv_boot_skips_recovery" THEN NoRun ELSE RecJob
  /\ ctl' = StartCtl
  /\ UNCHANGED <<fs, env, tgt, gh>>

----------------------------------------------------------------------------
(* THE SAVE (§6.1)                                                         *)
SV  == ctl.s
PV  == PostV(ctl.s)
Pre(f) == ctl.plan[f]
CurF == WOrd[ctl.fi]
TS == <<ctl.s, CurF>>

\* the save's rollback starts (§6.4) over the files `cur`: the restore of its own record (`RS_*`), checks first
StartRBOn(msg, cur) ==
  [ctl EXCEPT !.pc = IF MUTANT # "rollback_skips_temp_rule" THEN "rs_chk" ELSE "rs_sync",
              !.rj = ctl.s, !.fi = 1, !.rbt = msg, !.tried = {},
              \* (seeded bug rollback_skips_temp_rule: the rollback puts back what is at post, unchecked)
              !.put = {f \in Tg : Pre(f) # ABS /\ cur[f] = PV},
              !.taken = \E f \in Tg : Pre(f) = ABS /\ cur[f] # ABS]
StartRB(msg) == StartRBOn(msg, fs.cur)

NextS(p) ==
  CASE p = "s_bk" -> IF MUTANT = "no_backup_sync" THEN "s_jp" ELSE "s_bks"
    [] p = "s_bks" -> "s_jp"
    [] p = "s_jp" -> IF MUTANT = "direct_open" THEN "s_jds" ELSE "s_jps"
    [] p = "s_jps" -> "s_jo"
    [] p = "s_jo" -> IF MUTANT = "no_journal_sync" THEN "s_tw1" ELSE "s_jds"
    [] p = "s_jds" -> "s_tw1"
    [] p = "s_tw1" -> "s_tw2"
    [] p = "s_tw2" -> "s_ts"
    [] p = "s_ts" -> "s_sw"
    [] p = "s_sw" -> "s_cmp"
    [] p = "s_cmp" -> "s_ds"
    [] p = "s_rbk" -> IF MUTANT = "temps_after_close" THEN "s_close" ELSE "s_rmt"
    [] p = "s_rmt" -> IF MUTANT = "temps_after_close" THEN "s_saved" ELSE "s_close"
    [] p = "s_close" -> IF MUTANT = "report_before_close_sync" THEN "s_saved" ELSE "s_csync"
    [] p = "s_csync" -> CASE MUTANT = "temps_after_close" -> "s_rmt"
                          [] OTHER -> "s_saved"
Adv(p) == [ctl EXCEPT !.pc = NextS(p)]
SaveFx(f1, j1, c1) == fs' = f1 /\ jr' = j1 /\ ctl' = c1 /\ UNCHANGED <<env, tgt, app, run, n, gh>>

\* steps 1-4, 7: the records (an .open journal on a file of W1's ledger set refuses: the save's own check and, for
\* another workspace's journal, the open that recover-before-open blocks), the gate (the disk's fingerprint equals
\* the plan's), then the commit point. No load, no re-check (ADR-0068 D1), no lock (D3, D4).
S_Gate ==
  /\ Step("s_gate")
  /\ IF FP(1) # ctl.plan \/ OpenOn(LS(1)) # {}
     THEN /\ SetTold(SV, "notsaved") /\ EndJob
          /\ UNCHANGED <<fs, jr, env, tgt, app, n>>
     ELSE /\ ctl' = [ctl EXCEPT !.pc = "s_bk"]
          \* the editor's bytes the plan was made on are the bytes the user previewed and replaces
          /\ gh' = [gh EXCEPT !.edSeen = [f \in Files |-> IF f \in LS(1) THEN TRUE ELSE gh.edSeen[f]]]
          /\ UNCHANGED <<fs, jr, env, tgt, app, run, n>>

\* step 8.0-8.1: a created path must be free, a replaced file synced (an editor's unsynced bytes made durable), read
\* and equal to its pre bytes, else "not saved" (nothing replaced; the re-hash says unchanged or changed); then its
\* backup written (its data synced) and the backup folder synced
S_Backup ==
  /\ Step("s_bk")
  /\ IF \E f \in Tg : fs.cur[f] # Pre(f)
     THEN /\ SetTold(SV, Rehash(SV)) /\ EndJob
          /\ UNCHANGED <<fs, jr, env, tgt, app, n>>
     ELSE SaveFx([fs EXCEPT !.pl["R"] = IF fs.cp["R"] = {} THEN {fs.cur["R"]} ELSE @, !.dpl["R"] = {},
                            !.bk[SV] = Pre("R"), !.bkpl[SV] = {ABS, TORN, Pre("R")}],
                 jr, Adv("s_bk"))
S_BackupSync == Step("s_bks") /\ SaveFx([fs EXCEPT !.bkpl[SV] = {Pre("R")}], jr, Adv("s_bks"))

\* step 8.2: the journal as .part, its data synced, renamed .open, the folder synced
NewJ == [NoJ EXCEPT !.files = Tg,
           !.pre = [f \in Files |-> IF f \in Tg THEN ctl.plan[f] ELSE 0],
           !.post = [f \in Files |-> IF f \in Tg THEN PV ELSE 0]]
S_JPart ==
  /\ Step("s_jp")
  /\ SaveFx(fs, [jr EXCEPT ![SV] = IF MUTANT = "direct_open"
                                   THEN [NewJ EXCEPT !.st = "open", !.pl = {"none","topen","open"}]
                                   ELSE [NewJ EXCEPT !.st = "part", !.pl = {"none","tpart","part"}]],
            Adv("s_jp"))
S_JPartSync == Step("s_jps") /\ SaveFx(fs, [jr EXCEPT ![SV].pl = {"none","part"}], Adv("s_jps"))
S_JOpen == Step("s_jo") /\ SaveFx(fs, [jr EXCEPT ![SV].st = "open", ![SV].pl = @ \cup {"open"}], Adv("s_jo"))
S_JDirSync == Step("s_jds") /\ SaveFx(fs, [jr EXCEPT ![SV].pl = {jr[SV].st}], Adv("s_jds"))

\* step 8.3, per file (children first, the root last): the new bytes written at the staging name (cut off here, a
\* torn file there), then synced and renamed to the temp name, then that folder synced (S0061 round 3)
S_TempStart ==
  /\ Step("s_tw1")
  /\ SaveFx([fs EXCEPT !.sg[TS] = TORN, !.sgpl[TS] = {ABS, TORN}], jr, Adv("s_tw1"))
S_TempDone ==
  /\ Step("s_tw2")
  \* (seeded bug no_temp_fsync: the staging file's data is not synced before its rename)
  /\ SaveFx([fs EXCEPT !.sg[TS] = ABS, !.sgpl[TS] = {ABS, TORN, PV},
                       !.tc[TS] = PV, !.tpl[TS] = IF MUTANT = "no_temp_fsync" THEN {ABS, TORN, PV} ELSE {ABS, PV}],
            jr, Adv("s_tw2"))
S_TempSync ==
  /\ Step("s_ts")
  /\ SaveFx([fs EXCEPT !.sgpl[TS] = {ABS},
                       !.tpl[TS] = IF MUTANT = "no_temp_fsync" THEN {TORN, PV} ELSE {PV}],
            jr, Adv("s_ts"))

\* An exchange or exclusive rename in the ledger folder is one operation that a power loss keeps or drops whole
\* (§13 file-system assumptions; no order between operations is assumed): until the folder is synced, the file and
\* its temp survive as a pair from before or after each pending operation (cp: every possible pair; cpn: the pairs
\* in which the latest operation persisted). Without a pending operation they are independent.
PlOf(f) == IF fs.cp[f] # {} THEN {p[1] : p \in fs.cp[f]} ELSE fs.pl[f]
Pairs(f, t) == IF fs.cp[f] # {} /\ fs.cpj[f] = t[1] THEN fs.cp[f]
               ELSE {<<x, y>> : x \in PlOf(f), y \in fs.tpl[t]}
Firsts(P)  == {p[1] : p \in P}
Seconds(P) == {p[2] : p \in P}
Couple(f, t, P, New) ==
  LET old == <<fs.cpj[f], f>>
      stale == fs.cp[f] # {} /\ fs.cpj[f] # t[1]
      fs0 == IF stale THEN [fs EXCEPT !.tpl[old] = {p[2] : p \in fs.cp[f]}] ELSE fs
  IN [fs0 EXCEPT !.cp[f] = P \cup New, !.cpn[f] = New, !.cpj[f] = t[1],
                 !.pl[f] = Firsts(P \cup New), !.tpl[t] = Seconds(P \cup New)]

\* the file system after the swap (a file at pre) or the exclusive rename to a created path, of the current file
SwapFs ==
  LET f == CurF  t == TS  P == Pairs(f, t) IN
  IF Pre(f) # ABS
  THEN [Couple(f, t, P, {<<a, b>> : a \in Seconds(P) \ {ABS}, b \in Firsts(P) \ {ABS}})
          EXCEPT !.cur[f] = fs.tc[t], !.tc[t] = fs.cur[f], !.dpl[f] = {}]
  ELSE [Couple(f, t, P, {<<a, ABS>> : a \in Seconds(P) \ {ABS}})
          EXCEPT !.cur[f] = fs.tc[t], !.tc[t] = ABS, !.dpl[f] = {}]
\* the exclusive rename can take place: no entry stands at the created path (seeded bug plain_create: it always can)
CanSwap == Pre(CurF) # ABS \/ fs.cur[CurF] = ABS \/ MUTANT = "plain_create"

\* a replace by atomic swap (a file at pre), or an exclusive rename to a created path that fails if an entry
\* appeared (the save stops `changed` and rolls back; what stands there is never touched)
S_Swap ==
  /\ Step("s_sw")
  /\ IF CanSwap
     THEN SaveFx(SwapFs, jr, [Adv("s_sw") EXCEPT !.pc = IF Pre(CurF) = ABS THEN "s_ds" ELSE @, !.repl = TRUE])
     ELSE SaveFx(fs, jr, StartRB("changed"))

\* §6.4: what the swap displaced: the pre or the post bytes are ours; anything else is an outside save, moved at
\* once to the file's kept name (never swapped back), the folder synced, and the save stops and rolls back
S_Compare ==
  /\ Step("s_cmp")
  /\ LET f == CurF  t == TS IN
     IF fs.tc[t] \in {Pre(f), PV} \/ MUTANT = "no_displaced_check"
     THEN SaveFx(fs, jr, Adv("s_cmp"))
     ELSE SaveFx([fs EXCEPT !.kp[t] = @ \cup {fs.tc[t]}, !.tc[t] = ABS, !.tpl[t] = {ABS},
                            !.pl[f] = {fs.cur[f]}, !.cp[f] = {}, !.cpn[f] = {}, !.cpj[f] = "none"],
                 \* (seeded bug x_keep_and_go_on, verifier S0072 r1 F4: the save carries on past the kept copy)
                 jr, IF MUTANT = "x_keep_and_go_on" THEN Adv("s_cmp") ELSE StartRB("changed"))

S_DirSync ==
  /\ Step("s_ds")
  /\ LET f == CurF  t == TS IN
     SaveFx([fs EXCEPT !.pl[f] = IF fs.cp[f] = {} THEN @ ELSE Firsts(fs.cpn[f]),
                       !.tpl[t] = IF fs.cp[f] = {} THEN @ ELSE Seconds(fs.cpn[f]),
                       !.cp[f] = {}, !.cpn[f] = {}, !.cpj[f] = "none"],
            jr,
            IF ctl.fi < Len(WOrd) THEN [ctl EXCEPT !.fi = @ + 1, !.pc = "s_tw1"] ELSE [ctl EXCEPT !.pc = "s_rbk"])

\* step 8.4: the read-back, then each temp (the old object, at the pre bytes) checked and removed, the folder synced
S_ReadBack ==
  /\ Step("s_rbk")
  /\ SaveFx(fs, jr, IF \A f \in Tg : fs.cur[f] = PV THEN Adv("s_rbk") ELSE StartRB("changed"))
S_RemoveTemps ==
  /\ Step("s_rmt")
  /\ IF \E f \in Tg : fs.tc[<<SV, f>>] \notin {ABS, Pre(f), PV} /\ MUTANT # "no_displaced_check"
     THEN \* outside bytes in a temp (reached only after a seeded bug: the editor writes only the files' names)
          LET f == CHOOSE g \in Tg : fs.tc[<<SV, g>>] \notin {ABS, Pre(g), PV} IN
          SaveFx([fs EXCEPT !.kp[<<SV, f>>] = @ \cup {fs.tc[<<SV, f>>]}, !.tc[<<SV, f>>] = ABS,
                            !.tpl[<<SV, f>>] = {ABS}], jr, StartRB("changed"))
     ELSE SaveFx([fs EXCEPT !.tc = [x \in TSlots |-> IF x[1] = SV THEN ABS ELSE fs.tc[x]],
                            !.tpl = [x \in TSlots |-> IF x[1] = SV THEN {ABS} ELSE fs.tpl[x]]],
                 jr, Adv("s_rmt"))

\* step 8.5: the journal close; the save has landed exactly when the closed record is durable
S_Close ==
  /\ Step("s_close")
  /\ jr' = [jr EXCEPT ![SV].st = "closed", ![SV].pl = {"open","closed"}]
  /\ ctl' = Adv("s_close")
  /\ IF MUTANT = "report_before_close_sync" THEN SetTold(SV, "saved") ELSE UNCHANGED gh
  /\ UNCHANGED <<fs, env, tgt, app, run, n>>
S_CloseSync ==
  /\ Step("s_csync")
  /\ jr' = [jr EXCEPT ![SV].pl = {jr[SV].st}]
  /\ IF MUTANT = "report_before_close_sync" THEN EndJob ELSE ctl' = Adv("s_csync") /\ UNCHANGED run
  /\ UNCHANGED <<fs, env, tgt, app, n, gh>>
\* step 9: "saved"; the job ends (the rebuild after it writes nothing: not modelled)
S_Saved ==
  /\ Step("s_saved")
  /\ SetTold(SV, "saved")
  \* (seeded bug report_before_close_sync: "saved" is said before the close is synced)
  /\ IF MUTANT = "report_before_close_sync" THEN ctl' = [ctl EXCEPT !.pc = "s_csync"] /\ UNCHANGED run ELSE EndJob
  /\ UNCHANGED <<fs, jr, env, tgt, app, n>>

----------------------------------------------------------------------------
(* THE RESTORE: the save's rollback (§6.4) and recovery's try (§8.1), one  *)
(* restore over one journal's record (`ledger.restore`)                     *)
RJn  == ctl.rj
InRollback == run.k = "save"
JPre(f)  == jr[RJn].pre[f]
JPost(f) == jr[RJn].post[f]
Slot(f) == <<RJn, f>>
\* the temp rule (owner, 2026-10-30, "Staging name"): a staging file is ours whatever it holds; a temp name (the
\* save's temp, the restore temp) is ours only when it holds the pre or the post bytes. Seeded bugs: no_temp_check
\* (recovery takes every temp for ours), rollback_skips_temp_rule (the rollback does), old_temp_rule (a staging file
\* is judged by its bytes, as a temp name is)
Held(j, f) == {ABS, jr[j].pre[f], jr[j].post[f]}
NoTempRule == (MUTANT = "no_temp_check" /\ ~InRollback) \/ (MUTANT = "rollback_skips_temp_rule" /\ InRollback)
TempOurs(j, f, v) == v \in Held(j, f) \/ NoTempRule
StagingOurs(j, f, v) == IF MUTANT = "old_temp_rule" THEN v \in Held(j, f) ELSE TRUE
\* seeded bug block_without_cause: the old object at pre beside a file at post is taken for an outside save
Bogus(j, f) == MUTANT = "block_without_cause" /\ fs.cur[f] = jr[j].post[f] /\ fs.tc[<<j, f>>] = jr[j].pre[f]
ForeignTemp(j, f) ==
  \/ ~TempOurs(j, f, fs.tc[<<j, f>>]) \/ ~TempOurs(j, f, fs.rt[<<j, f>>])
  \/ ~StagingOurs(j, f, fs.sg[<<j, f>>]) \/ Bogus(j, f)
BackupBad(j, f) == j \in SaveIds /\ f = "R" /\ fs.bk[j] # jr[j].pre[f]
\* the per-file judgement (`core.recovery.judge`) after the folder's guards: a stop, or a verdict
Stops(j, f) ==
  \/ PathMoved(f) /\ Guarded
  \/ env.unr /\ f = "K"
  \/ fs.kp[<<j, f>>] # {}                  \* a kept copy of this journal beside it: the user has not dealt with it
  \/ ForeignTemp(j, f)
  \/ /\ jr[j].pre[f] # ABS /\ fs.cur[f] # jr[j].pre[f]
     /\ (fs.cur[f] = jr[j].post[f] => BackupBad(j, f))
AtPost(j, f) == jr[j].pre[f] # ABS /\ fs.cur[f] = jr[j].post[f] /\ fs.cur[f] # jr[j].pre[f]
Taken(j, f) == jr[j].pre[f] = ABS /\ fs.cur[f] # ABS
FirstStop(j) == LET is == {i \in 1..Len(RecOrd(j)) : Stops(j, RecOrd(j)[i])} IN RecOrd(j)[Min(is)]

\* the journal stops: the rollback leaves it .open (Unfinished once a replace was asked for, else "not saved" with
\* the journal open); recovery marks it blocking
RestoreStop(why) ==
  IF InRollback
  THEN /\ SetTold(ctl.s, IF ctl.repl THEN "unfinished" ELSE ctl.rbt) /\ EndJob /\ UNCHANGED jr
  ELSE /\ jr' = [jr EXCEPT ![RJn].why = why]
       /\ ctl' = [ctl EXCEPT !.pc = "rc_next", !.tried = @ \cup {RJn}, !.rj = "none"]
       /\ UNCHANGED <<run, gh>>

\* step 2: every file checked, in the record's order; at the first that stops, the outside saves at its names of
\* ours are moved to its kept name first (out of reach of every deletion), then the journal stops, nothing else
\* written. Otherwise: what to put back (the replaced files at post) and whether a created path is taken.
RS_Check ==
  /\ Step("rs_chk")
  /\ IF \E f \in jr[RJn].files : Stops(RJn, f)
     THEN LET f == FirstStop(RJn)  t == Slot(f)
              keep == ~(PathMoved(f) /\ Guarded) /\ ~(env.unr /\ f = "K") /\ fs.kp[t] = {}
              T  == IF keep THEN {fs.tc[t], fs.rt[t]} \ Held(RJn, f) ELSE {}
          IN /\ fs' = IF T = {} THEN fs
                      ELSE [fs EXCEPT !.kp[t] = @ \cup T,
                                      !.tc[t] = IF fs.tc[t] \in T THEN ABS ELSE @,
                                      !.tpl[t] = IF fs.tc[t] \in T THEN {ABS} ELSE @,
                                      !.rt[t] = IF fs.rt[t] \in T THEN ABS ELSE @]
             /\ RestoreStop("stopped")
             /\ UNCHANGED <<env, tgt, app, n>>
     ELSE /\ ctl' = [ctl EXCEPT !.pc = "rs_sync",
                                !.put = {f \in jr[RJn].files : AtPost(RJn, f)},
                                !.taken = \E f \in jr[RJn].files : Taken(RJn, f)]
          /\ UNCHANGED <<fs, jr, env, tgt, app, run, n, gh>>

\* step 3 begins: every folder the record names synced first (a swap a crashed save left unsynced is durable before
\* anything it displaced is removed). A folder sync makes entries durable: each named file's name now holds its
\* current object, whose data may still fall back to `dpl` (an unsynced in-place write; spec §13, S0101)
SyncNamed(F, J) ==
  [fs EXCEPT !.pl = [f \in Files |-> IF f \in F THEN {fs.cur[f]} \cup fs.dpl[f] ELSE fs.pl[f]],
             !.cp = [f \in Files |-> IF f \in F THEN {} ELSE fs.cp[f]],
             !.cpn = [f \in Files |-> IF f \in F THEN {} ELSE fs.cpn[f]],
             !.cpj = [f \in Files |-> IF f \in F THEN "none" ELSE fs.cpj[f]],
             !.tpl = [x \in TSlots |-> IF x[2] \in F THEN {fs.tc[x]} ELSE fs.tpl[x]],
             !.sgpl = [x \in TSlots |-> IF x[2] \in F THEN {fs.sg[x]} ELSE fs.sgpl[x]]]
RS_Sync ==
  /\ Step("rs_sync")
  /\ fs' = SyncNamed(jr[RJn].files, RJn)
  /\ ctl' = [ctl EXCEPT !.pc = "rs_clean"]
  /\ UNCHANGED <<jr, env, tgt, app, run, n, gh>>

\* the files to put back, root first
PutAt(i) == ROrd(RJn)[i]
NextPut(i) == {k \in i..Len(ROrd(RJn)) : PutAt(k) \in ctl.put}
RF == PutAt(ctl.fi)
\* move on to the next file to put back, or the folders' sync
RestNextFrom(i) == IF NextPut(i) = {} THEN [ctl EXCEPT !.pc = IF MUTANT = "resolve_before_restores_synced"
                                                            THEN "rs_res" ELSE "rs_all"]
                   ELSE [ctl EXCEPT !.fi = Min(NextPut(i)), !.pc = "rs_w"]

\* the names of ours removed (each checked again by the temp rule; nothing writes there since the check), but the
\* temp beside a file to put back, which holds the only other copy of its pre bytes until they are back
RS_Clean ==
  /\ Step("rs_clean")
  /\ LET J == RJn  F == jr[RJn].files
         gone(x) == x[1] = J /\ x[2] \in F
     IN fs' = [fs EXCEPT
                 !.sg = [x \in TSlots |-> IF gone(x) THEN ABS ELSE fs.sg[x]],
                 !.sgpl = [x \in TSlots |-> IF gone(x) THEN fs.sgpl[x] \cup {ABS} ELSE fs.sgpl[x]],
                 !.rt = [x \in TSlots |-> IF gone(x) THEN ABS ELSE fs.rt[x]],
                 !.tc = [x \in TSlots |-> IF gone(x) /\ x[2] \notin ctl.put THEN ABS ELSE fs.tc[x]],
                 !.tpl = [x \in TSlots |-> IF gone(x) /\ x[2] \notin ctl.put THEN fs.tpl[x] \cup {ABS}
                                           ELSE fs.tpl[x]]]
  /\ ctl' = RestNextFrom(1)
  /\ UNCHANGED <<jr, env, tgt, app, run, n, gh>>

\* step 3 per file: the pre bytes from the backup written at the restore staging name, synced, renamed to the
\* restore temp, that rename synced (one step: a cut write at the staging name is the staging rule's, as the save's)
RS_Write ==
  /\ Step("rs_w")
  /\ fs' = [fs EXCEPT !.rt[Slot(RF)] = JPre(RF)]
  /\ ctl' = [ctl EXCEPT !.pc = "rs_x"]
  /\ UNCHANGED <<jr, env, tgt, app, run, n, gh>>

\* the restore swap, then the folder synced at once (S0061 round 4)
RS_Swap ==
  /\ Step("rs_x")
  /\ LET f == RF  t == Slot(f) IN
     IF PathMoved(f)
     THEN \* reached only without the guard: the write goes through the link to a file no journal names
          /\ env' = [env EXCEPT !.outside = JPre(f)]
          /\ gh' = [gh EXCEPT !.outW = TRUE]
          /\ fs' = [fs EXCEPT !.rt[t] = ABS]
          /\ ctl' = RestNextFrom(ctl.fi + 1)
     ELSE /\ LET nv == fs.rt[t]
                 \* seeded bug no_restore_temp_fsync: the restore temp's data not synced before the swap
                 unsynced == MUTANT = "no_restore_temp_fsync"
                 \* seeded bug resolve_before_restores_synced: the swap is not synced at once
                 lazy == MUTANT = "resolve_before_restores_synced"
             IN fs' = [fs EXCEPT !.cur[f] = nv, !.rt[t] = fs.cur[f],
                                 !.pl[f] = (IF lazy THEN fs.pl[f] ELSE {}) \cup {nv}
                                           \cup (IF unsynced THEN {TORN} ELSE {}),
                                 !.cp[f] = {}, !.cpn[f] = {}, !.cpj[f] = "none",
                                 !.dpl[f] = IF unsynced THEN {TORN} ELSE {}]
          /\ ctl' = [ctl EXCEPT !.pc = "rs_c"]
          /\ UNCHANGED <<env, gh>>
  /\ UNCHANGED <<jr, tgt, app, run, n>>

\* what the restore swap displaced: ours (the post or the pre bytes) is removed; anything else is an outside save in
\* the moment between the check and the swap: moved to the kept name, and the journal stops
RS_Compare ==
  /\ Step("rs_c")
  /\ LET f == RF  t == Slot(f) IN
     IF fs.rt[t] \in {JPost(f), JPre(f)} \/ MUTANT = "no_displaced_check"
     THEN /\ fs' = [fs EXCEPT !.rt[t] = ABS]
          /\ ctl' = [ctl EXCEPT !.pc = "rs_t"]
          /\ UNCHANGED <<jr, run, gh>>
     ELSE /\ fs' = [fs EXCEPT !.kp[t] = @ \cup {fs.rt[t]}, !.rt[t] = ABS]
          /\ RestoreStop("stopped")
  /\ UNCHANGED <<env, tgt, app, n>>

\* the save's temp beside a file now back at pre, checked again by the temp rule and removed
RS_Temp ==
  /\ Step("rs_t")
  /\ LET f == RF  t == Slot(f) IN
     IF TempOurs(RJn, f, fs.tc[t])
     THEN /\ fs' = [fs EXCEPT !.tc[t] = ABS, !.tpl[t] = @ \cup {ABS}]
          /\ ctl' = RestNextFrom(ctl.fi + 1)
          /\ UNCHANGED <<jr, run, gh>>
     ELSE /\ fs' = [fs EXCEPT !.kp[t] = @ \cup {fs.tc[t]}, !.tc[t] = ABS, !.tpl[t] = {ABS}]
          /\ RestoreStop("stopped")
  /\ UNCHANGED <<env, tgt, app, n>>

\* the replaced files step 2 found at pre (the rest are put back, or created): what step 4 flushes
Flushed == {f \in jr[RJn].files : JPre(f) # ABS /\ f \notin ctl.put}
\* every folder the record names synced; a created path that is taken ends the try here (`created_path_taken`:
\* the other files durably at pre, the journal .open)
RS_All ==
  /\ Step("rs_all")
  /\ fs' = SyncNamed(jr[RJn].files, RJn)
  /\ IF ctl.taken
     THEN RestoreStop("stopped")
     ELSE /\ ctl' = [ctl EXCEPT !.pc = IF Flushed = {} THEN "rs_res" ELSE "rs_fl"]
          /\ UNCHANGED <<jr, run, gh>>
  /\ UNCHANGED <<env, tgt, app, n>>

\* step 4's flush (S0101, BL-0243; owner, 2026-11-11: "Flush named files only (Recommended)"): the data of each
\* replaced file step 2 found at pre synced (read-only), so an unsynced revert to the pre bytes is durable before the
\* record says resolved; a file put back holds the restore's own synced bytes. Seeded bug rs_no_file_sync: no flush
RS_Flush ==
  /\ Step("rs_fl")
  /\ fs' = IF MUTANT = "rs_no_file_sync" THEN fs
           ELSE [fs EXCEPT !.pl = [f \in Files |-> IF f \in Flushed /\ fs.cp[f] = {} THEN {fs.cur[f]} ELSE fs.pl[f]],
                           !.dpl = [f \in Files |-> IF f \in Flushed THEN {} ELSE fs.dpl[f]]]
  /\ ctl' = [ctl EXCEPT !.pc = "rs_res"]
  /\ UNCHANGED <<jr, env, tgt, app, run, n, gh>>

\* step 4: only then the journal renamed .resolved and its folder synced
RestoreDone ==
  IF InRollback
  THEN SetTold(ctl.s, ctl.rbt) /\ EndJob
  ELSE /\ ctl' = [ctl EXCEPT !.pc = "rc_next", !.tried = @ \cup {RJn}, !.rj = "none"]
       /\ UNCHANGED <<run, gh>>
RS_Resolve ==
  /\ Step("rs_res")
  /\ IF ctl.taken /\ MUTANT = "resolve_before_restores_synced"
     THEN \* the seeded order still stops at a taken created path, after its late sync
          /\ ctl' = [ctl EXCEPT !.pc = "rs_all"] /\ UNCHANGED <<jr, run, gh>>
     ELSE /\ jr' = [jr EXCEPT ![RJn].st = "resolved", ![RJn].pl = {"resolved"}]
          /\ IF MUTANT = "resolve_before_restores_synced"
             THEN ctl' = [ctl EXCEPT !.pc = "rs_late"] /\ UNCHANGED <<run, gh>>
             ELSE RestoreDone
  /\ UNCHANGED <<fs, env, tgt, app, n>>
\* seeded bug resolve_before_restores_synced only: the folders' sync after the record says resolved
RS_Late ==
  /\ Step("rs_late")
  /\ fs' = SyncNamed(jr[RJn].files, RJn)
  /\ RestoreDone
  /\ UNCHANGED <<jr, env, tgt, app, n>>

----------------------------------------------------------------------------
(* RECOVER-ALL (§8.1): every .open journal tried, one at a time            *)
\* start: earlier judgements cleared; .part journals deleted (they never block); the journal folders synced
RC_Init ==
  /\ Step("rc_init")
  /\ jr' = [j \in JIds |-> CASE jr[j].st \in {"part","tpart"} -> NoJ
                              [] jr[j].st \in OpenSt -> [jr[j] EXCEPT !.why = "", !.pl = {jr[j].st}]
                              [] OTHER -> [jr[j] EXCEPT !.pl = {jr[j].st}]]
  /\ ctl' = [ctl EXCEPT !.pc = "rc_next", !.tried = {}]
  /\ UNCHANGED <<fs, env, tgt, app, run, n, gh>>

Untried == {j \in JIds : jr[j].st \in OpenSt /\ j \notin ctl.tried}
RC_Next ==
  /\ Step("rc_next")
  /\ IF Untried = {}
     THEN /\ app' = "run" /\ EndJob
          /\ UNCHANGED <<fs, jr, env, tgt, n, gh>>
     ELSE LET j == JOrder[Min({i \in 1..Len(JOrder) : JOrder[i] \in Untried})] IN
          IF jr[j].st = "topen"
          THEN \* a journal whose file list cannot be read blocks every open
               /\ jr' = [jr EXCEPT ![j].why = "stopped"]
               /\ ctl' = [ctl EXCEPT !.tried = @ \cup {j}]
               /\ UNCHANGED <<fs, env, tgt, app, run, n, gh>>
          ELSE /\ ctl' = [ctl EXCEPT !.rj = j, !.pc = "rs_chk", !.put = {}, !.taken = FALSE]
               /\ UNCHANGED <<fs, jr, env, tgt, app, run, n, gh>>

----------------------------------------------------------------------------
(* COPY OF W2 (§8.2)                                                       *)
\* its sources must not be named by an unresolved journal, blocking or in-session (seeded bug
\* copy_checks_blocking_only: only blocking ones)
CP_Check ==
  /\ Step("cp_chk")
  /\ IF \E j \in OpenOn(LS(2)) : MUTANT = "copy_checks_blocking_only" => jr[j].why # ""
     THEN EndJob
     ELSE ctl' = [ctl EXCEPT !.pc = "cp_load"] /\ UNCHANGED run
  /\ UNCHANGED <<fs, jr, env, tgt, app, n, gh>>
CP_Load ==            \* reads the sources and writes the copy (into its own target, the create's names)
  /\ Step("cp_load")
  /\ ctl' = [ctl EXCEPT !.pc = "cp_man"]
  /\ UNCHANGED <<fs, jr, env, tgt, app, run, n, gh>>
CP_Manifest ==       \* the copy's manifest last
  /\ Step("cp_man")
  /\ EndJob
  /\ UNCHANGED <<fs, jr, env, tgt, app, n, gh>>

----------------------------------------------------------------------------
(* CREATE (§8.2): the target folder and its names                          *)
CrFx(t1, c1) == tgt' = t1 /\ ctl' = c1 /\ UNCHANGED <<fs, jr, env, app, run, n, gh>>
CrStop == EndJob /\ UNCHANGED <<fs, jr, env, tgt, app, n, gh>>
\* step 1: the target must not exist or must be empty
CR_Target ==
  /\ Step("cr_tgt")
  /\ IF (\E x \in Names : tgt.c[x] # "no") /\ MUTANT # "no_refuse_nonempty"
     THEN CrStop
     ELSE CrFx([tgt EXCEPT !.ex = TRUE], [ctl EXCEPT !.pc = IF MUTANT = "manifest_first" THEN "cr_mr" ELSE "cr_wa"])
\* step 3: each file created exclusively; a collision with a name the job writes stops it
CR_WriteA ==
  /\ Step("cr_wa")
  /\ IF tgt.c["a"] # "no" /\ MUTANT # "plain_file_write"
     THEN CrStop
     ELSE CrFx([tgt EXCEPT !.c["a"] = "ours", !.pl["a"] = {"no","torn","ours"}], [ctl EXCEPT !.pc = "cr_ws"])
CR_SyncA ==
  /\ Step("cr_ws")
  /\ CrFx([tgt EXCEPT !.pl["a"] = IF tgt.c["a"] = "ours" /\ MUTANT # "rv_create_no_sync" THEN {"ours"} ELSE @],
          [ctl EXCEPT !.pc = IF MUTANT = "no_readback" THEN "cr_mr" ELSE "cr_rb"])
\* step 4: read back every file
CR_ReadBack ==
  /\ Step("cr_rb")
  /\ IF tgt.c["a"] # "ours" THEN CrStop ELSE CrFx(tgt, [ctl EXCEPT !.pc = "cr_mr"])
\* step 7: the manifest last: its temp written and synced, renamed exclusively (a .finzytrack that appeared
\* meanwhile stops the job and is left as it is), the parent synced
CR_ManifestRename ==
  /\ Step("cr_mr")
  /\ IF tgt.c["m"] # "no" /\ MUTANT # "manifest_overwrite"
     THEN CrStop
     ELSE CrFx([tgt EXCEPT !.c["m"] = "ours", !.pl["m"] = {"no","ours"}], [ctl EXCEPT !.pc = "cr_ms"])
CR_ManifestSync ==
  /\ Step("cr_ms")
  /\ CrFx([tgt EXCEPT !.pl["m"] = {"ours"}],
          [ctl EXCEPT !.pc = IF MUTANT = "manifest_first" /\ tgt.c["a"] = "no" THEN "cr_wa" ELSE "cr_reg"])
\* step 8: register
CR_Register == Step("cr_reg") /\ CrStop

----------------------------------------------------------------------------
(* ENVIRONMENT: the OS, the outside editor, the path changer               *)
SavePcs == {"s_gate","s_bk","s_bks","s_jp","s_jps","s_jo","s_jds","s_tw1","s_tw2","s_ts","s_sw","s_cmp","s_ds",
            "s_rbk","s_rmt","s_close","s_csync","s_saved"}

\* an engine death (any signal or exit we did not cause). A save's outcome is then read from the disk in a fresh
\* engine, after its journal folder's sync (§6.6; seeded bug outcome_unsynced: without the sync); recover-all marks
\* the journal in hand failed and goes on in a fresh engine (seeded bug crash_unmarked: unmarked)
EngineCrash ==
  /\ app # "dead" /\ Busy /\ n.crash < MaxCrash
  /\ n' = [n EXCEPT !.crash = @ + 1]
  /\ CASE run.k = "save" ->
            /\ SetTold(run.id, DiskOutcome(run.id))
            /\ jr' = IF MUTANT # "outcome_unsynced" /\ jr[run.id].st # "none"
                     THEN [jr EXCEPT ![run.id].pl = {jr[run.id].st}] ELSE jr
            /\ EndJob
            /\ UNCHANGED <<fs, env, tgt, app>>
       [] run.k = "recover" ->
            /\ jr' = IF ctl.rj # "none" /\ MUTANT # "crash_unmarked" /\ jr[ctl.rj].st \in OpenSt
                     THEN [jr EXCEPT ![ctl.rj].why = "failed"] ELSE jr
            /\ ctl' = [ctl EXCEPT !.pc = IF ctl.pc = "rc_init" THEN "rc_init" ELSE "rc_next", !.rj = "none",
                                  !.tried = IF ctl.rj # "none" THEN @ \cup {ctl.rj} ELSE @]
            /\ UNCHANGED <<fs, env, tgt, app, run, gh>>
       [] OTHER ->
            /\ EndJob
            /\ UNCHANGED <<fs, jr, env, tgt, app, gh>>

\* an I/O error: after the journal is .open, the save rolls back; before it (a full disk), nothing was replaced:
\* "not saved" with the re-hash (a .part is left for recover-all); during a restore, the try fails at once. At the
\* swap or exclusive rename (`s_sw`) the replace counts as asked for (`repl`, the code's `progress.replaced`): the
\* error may come before the operation or after it took place (an operation that happened but answered an error);
\* elsewhere the error comes before the step's effect (MAPPING.md §3)
WritePcs == {"s_tw1","s_tw2","s_ts","s_sw","s_cmp","s_ds","s_rbk","s_rmt"}
PreJournalPcs == {"s_bk","s_bks","s_jp","s_jps"}
RestPcs  == {"rs_chk","rs_sync","rs_clean","rs_w","rs_x","rs_c","rs_t","rs_all","rs_fl","rs_res"}
IOFail ==
  /\ app # "dead" /\ Busy /\ n.io < MaxIO
  /\ n' = [n EXCEPT !.io = @ + 1]
  /\ \/ /\ run.k = "save" /\ ctl.pc \in WritePcs
        /\ ctl' = [StartRB("notsaved") EXCEPT !.repl = @ \/ ctl.pc = "s_sw"]
        /\ UNCHANGED <<fs, jr, env, tgt, app, run, gh>>
     \/ /\ run.k = "save" /\ ctl.pc = "s_sw" /\ CanSwap
        /\ fs' = SwapFs
        /\ ctl' = [StartRBOn("notsaved", SwapFs.cur) EXCEPT !.repl = TRUE]
        /\ UNCHANGED <<jr, env, tgt, app, run, gh>>
     \/ /\ ctl.pc \in RestPcs /\ ctl.rj # "none"
        /\ RestoreStop("failed")
        /\ UNCHANGED <<fs, env, tgt, app>>
     \/ /\ run.k = "save" /\ ctl.pc \in PreJournalPcs
        /\ SetTold(SV, Rehash(SV)) /\ EndJob
        /\ UNCHANGED <<fs, jr, env, tgt, app>>

\* the app crashes; its engine goes with it
AppCrash ==
  /\ app # "dead" /\ n.ac < MaxAppCrash
  /\ n' = [n EXCEPT !.ac = @ + 1]
  /\ app' = "dead" /\ EndJob
  /\ UNCHANGED <<fs, jr, env, tgt, gh>>

\* a power loss: only synced steps count; every process dies
RECURSIVE ProdF(_, _)
ProdF(P, S) == IF S = {} THEN {<<>>}
               ELSE LET x == CHOOSE y \in S : TRUE IN
                    {[t \in DOMAIN g \cup {x} |-> IF t = x THEN v ELSE g[t]] : g \in ProdF(P, S \ {x}), v \in P[x]}
Choices(P) == {[x \in DOMAIN P |-> g[x]] : g \in ProdF(P, DOMAIN P)}
PairOr(f) == IF fs.cp[f] # {} THEN fs.cp[f] ELSE {<<0, 0>>}
PowerLoss ==
  /\ n.pw < MaxPower
  /\ \E cur0 \in Choices(fs.pl), tc0 \in Choices(fs.tpl), sg \in Choices(fs.sgpl), bk \in Choices(fs.bkpl),
        js \in Choices([j \in JIds |-> jr[j].pl]), tg \in Choices(tgt.pl),
        pR \in PairOr("R"), pN \in PairOr("N") :
     LET C  == {f \in {"R","N"} : fs.cp[f] # {}}
         pr(f) == IF f = "R" THEN pR ELSE pN
         cur == [f \in Files |-> IF f \in C THEN pr(f)[1] ELSE cur0[f]]
         tc  == [x \in TSlots |-> IF \E f \in C : x = <<fs.cpj[f], f>> THEN pr(x[2])[2] ELSE tc0[x]]
     IN
       /\ fs' = [fs EXCEPT !.cur = cur, !.pl = [f \in Files |-> {cur[f]}],
                           !.tc = tc, !.tpl = [x \in TSlots |-> {tc[x]}],
                           !.sg = sg, !.sgpl = [x \in TSlots |-> {sg[x]}],
                           !.bk = bk, !.bkpl = [s \in SaveIds |-> {bk[s]}],
                           !.cp = [f \in Files |-> {}], !.cpn = [f \in Files |-> {}],
                           !.cpj = [f \in Files |-> "none"], !.dpl = [f \in Files |-> {}]]
       /\ jr' = [j \in JIds |-> IF js[j] = "none" THEN NoJ
                                ELSE [jr[j] EXCEPT !.st = js[j], !.pl = {js[j]}, !.why = ""]]
       /\ tgt' = [tgt EXCEPT !.c = tg, !.pl = [x \in Names |-> {tg[x]}]]
       \* §13: an outside save a power loss dropped before that program synced it is its own loss
       /\ gh' = [gh EXCEPT !.ed = [f \in Files |->
                    IF ~EdDurable /\ gh.ed[f] # 0 /\ fs.pl[f] # {gh.ed[f]} /\ cur[f] # gh.ed[f]
                       /\ ~\E j \in JIds : f \in JFiles(j) /\ tc[<<j, f>>] = gh.ed[f]
                    THEN 0 ELSE gh.ed[f]]]
  /\ n' = [n EXCEPT !.pw = @ + 1]
  /\ app' = "dead" /\ EndJob
  /\ UNCHANGED env

\* the outside editor: writes any file at any time (durably, or not on the weaker file system)
\* what it writes: bytes of its own, or the file's initial bytes back (a revert: `git checkout`, an undo; S0101)
EdVals(f) == {Frn(f, fs.cur[f])} \cup (IF fs.cur[f] \notin {V0, ABS} THEN {V0} ELSE {})
Editor(f) ==
  /\ f \in EdFiles /\ n.ed < MaxEdits
  /\ \E v \in EdVals(f) :
     LET
         \* §13 file system: a durable save syncs its data and its folder; the folder's sync makes every entry
         \* operation made there before it durable, our pending ones in W1's folder included
         C  == IF f \in LS(1) THEN {g \in {"R","N"} : fs.cp[g] # {}} ELSE {}
         T  == {<<fs.cpj[g], g>> : g \in C}
         f1 == [fs EXCEPT !.pl = [g \in Files |-> IF g \in C THEN Firsts(fs.cpn[g]) ELSE fs.pl[g]],
                          !.tpl = [x \in TSlots |-> IF x \in T THEN Seconds(fs.cpn[x[2]]) ELSE fs.tpl[x]],
                          !.cp = [g \in Files |-> IF g \in C THEN {} ELSE fs.cp[g]],
                          !.cpn = [g \in Files |-> IF g \in C THEN {} ELSE fs.cpn[g]],
                          !.cpj = [g \in Files |-> IF g \in C THEN "none" ELSE fs.cpj[g]]]
         \* not durable: kept or dropped by a power loss on its own; a pending exchange of ours on the same name
         \* stays kept-or-dropped whole; the file's data may fall back to what it held before (`dpl`) even after
         \* its entry is durable, until its own sync (S0101)
         cpE == IF fs.cp[f] # {} THEN fs.cp[f] \cup {<<v, y>> : y \in Seconds(fs.cp[f])} ELSE {}
         f2 == [fs EXCEPT !.cur[f] = v,
                          !.pl[f] = IF fs.cp[f] # {} THEN Firsts(cpE) ELSE @ \cup {v},
                          !.cp[f] = cpE,
                          !.cpn[f] = {<<v, p[2]>> : p \in fs.cpn[f]},
                          !.dpl[f] = @ \cup ({fs.cur[f]} \ {ABS})]
     IN
     /\ fs' = IF EdDurable THEN [f1 EXCEPT !.cur[f] = v, !.pl[f] = {v}, !.dpl[f] = {}] ELSE f2
     /\ gh' = [gh EXCEPT !.ed[f] = v, !.edSeen[f] = FALSE]
  /\ n' = [n EXCEPT !.ed = @ + 1]
  /\ UNCHANGED <<jr, env, tgt, app, run, ctl>>

\* into the create's target: a file under a name the job writes, under another name, or over a file the job has
\* already written
CrPc == IF run.k = "create" THEN ctl.pc ELSE "none"
EditorDrop(x) ==
  /\ tgt.ex /\ n.drop < MaxDrop /\ x \in {"a","m","o"} /\ tgt.c[x] = "no"
  /\ tgt' = [tgt EXCEPT !.c[x] = "frn", !.pl[x] = {"frn"}]
  /\ gh' = IF x \in {"a","m"} THEN [gh EXCEPT !.dropped = @ \cup {x}] ELSE gh
  /\ n' = [n EXCEPT !.drop = @ + 1]
  /\ UNCHANGED <<fs, jr, env, app, run, ctl>>
EditorOverwrite ==
  /\ tgt.c["a"] = "ours" /\ n.drop < MaxDrop
  /\ tgt' = [tgt EXCEPT !.c["a"] = "frn", !.pl["a"] = {"frn"}]
  /\ gh' = IF CrPc \in {"cr_ws","cr_rb"} THEN [gh EXCEPT !.ovBefore = TRUE] ELSE [gh EXCEPT !.ovAfter = TRUE]
  /\ n' = [n EXCEPT !.drop = @ + 1]
  /\ UNCHANGED <<fs, jr, env, app, run, ctl>>

\* the path changer: after a crash, a folder is replaced by a link (to a copy of the post bytes)
PathChange ==
  /\ app = "dead" /\ n.link < MaxLink
  /\ \E j \in JIds : jr[j].st = "open" /\ "R" \in jr[j].files
  /\ env' = [env EXCEPT !.lnk = TRUE]
  /\ n' = [n EXCEPT !.link = @ + 1]
  /\ UNCHANGED <<fs, jr, tgt, app, run, ctl, gh>>

\* the disk that holds K becomes unreachable, or answers again
Unreach ==
  /\ n.unr < MaxUnreach
  /\ env' = [env EXCEPT !.unr = ~@]
  /\ n' = [n EXCEPT !.unr = @ + 1]
  /\ UNCHANGED <<fs, jr, tgt, app, run, ctl, gh>>

----------------------------------------------------------------------------
(* NEXT                                                                    *)
EngineStep ==
  \/ S_Gate \/ S_Backup \/ S_BackupSync \/ S_JPart \/ S_JPartSync \/ S_JOpen \/ S_JDirSync
  \/ S_TempStart \/ S_TempDone \/ S_TempSync \/ S_Swap \/ S_Compare \/ S_DirSync
  \/ S_ReadBack \/ S_RemoveTemps \/ S_Close \/ S_CloseSync \/ S_Saved
  \/ RS_Check \/ RS_Sync \/ RS_Clean \/ RS_Write \/ RS_Swap \/ RS_Compare \/ RS_Temp \/ RS_All \/ RS_Flush
  \/ RS_Resolve
  \/ RS_Late
  \/ RC_Init \/ RC_Next
  \/ CP_Check \/ CP_Load \/ CP_Manifest
  \/ CR_Target \/ CR_WriteA \/ CR_SyncA \/ CR_ReadBack \/ CR_ManifestRename \/ CR_ManifestSync \/ CR_Register

UserStep ==
  \/ StartSave \/ StartCreate \/ StartCopy \/ StartRecover \/ \E j \in JIds : Keep(j) \/ Relaunch

EnvStep ==
  \/ EngineCrash \/ IOFail \/ AppCrash \/ PowerLoss
  \/ \E f \in Files : Editor(f)
  \/ \E x \in {"a","m","o"} : EditorDrop(x) \/ EditorOverwrite
  \/ PathChange \/ Unreach

Next == EngineStep \/ UserStep \/ EnvStep

Spec == Init /\ [][Next]_vars

----------------------------------------------------------------------------
(* PROPERTIES: durability (EP-19; names as in the spec's §13)              *)

\* the save
SavedSurvivesPowerLoss ==
  \A s \in SaveIds : gh.told[s] = "saved" =>
     /\ jr[s].pl \subseteq CL
     /\ \A f \in Tg : (fs.cur[f] = PostV(s) /\ ~\E t \in JIds : t # s /\ f \in jr[t].files
                                                         /\ jr[t].st \in OpenSt \cup {"part","tpart"})
                     => fs.pl[f] = {PostV(s)}
OnDisk(s, f) == fs.cur[f] = PostV(s) \/ PostV(s) \in fs.pl[f]
BeforeClose(s) == jr[s].st \notin CL \cup {"resolved","kept"}
BackupDurableBeforeReplace ==
  \A s \in SaveIds : OnDisk(s, "R") /\ BeforeClose(s) => fs.bkpl[s] = {gh.pre[s]["R"]}
JournalDurableBeforeFirstReplace ==
  \A s \in SaveIds : (\E f \in Tg : OnDisk(s, f)) /\ BeforeClose(s) => jr[s].pl = {"open"}
\* a journal of ours that never became a durable .open never blocks (a boot journal torn by something else,
\* BootTorn, blocks: its file list cannot be read)
PartJournalNeverBlocks ==
  \A j \in JIds : jr[j].why # "" => (jr[j].st \in {"open","kept","resolved"}
                                      \/ (j \in BootIds /\ jr[j].st = "topen"))
\* the editor's latest bytes of a file are in the file, at a name of ours beside it, or in a kept copy that a journal
\* still stops on (.open: recovery stops while the copy exists, the save's gate refuses) or that the user chose to keep
\* (.kept); a kept copy no journal names any more is lost to the user (verifier S0072 r1 F4). Or the user previewed
\* them and the save replaced them (the backup holds them). Or they are a save's pre bytes of a file it writes, which
\* its backup holds, and the file holds that same save's post bytes (an editor's revert that save's swap displaced and
\* took for ours: §6.4, "deleting it loses nothing"; Limit (same bytes); S0101, tightened in round 1, verifier F2: any
\* older save's backup with those bytes hid a lost revert under no_displaced_check. The tightening is incomplete: a
\* later save's rollback back to an older save's post bytes makes fs.cur[f] = PostV(s) true again, so that older
\* backup can still hide a revert; MAPPING.md, "Limit (older backup)", S0111 verifier F1). The model writes one backup per
\* save, R's (`bk`), for the step that backs up every file the save replaces (`writer._backups`, §6.1 step 8.1): so
\* for any file of Tg the pre bytes are the ones the save recorded (`gh.pre`) and the backup step is the one `bk`
\* shows written (S0111: L_save_deep's revert on N, owner's run of 2026-11-13)
EditKept(f) ==
  \/ gh.ed[f] = fs.cur[f]
  \/ f \in Tg /\ \E s \in SaveIds : gh.pre[s][f] = gh.ed[f] /\ gh.ed[f] # ABS /\ fs.bk[s] = gh.pre[s]["R"]
                                 /\ gh.pre[s]["R"] # ABS /\ fs.cur[f] = PostV(s)
  \/ \E j \in JIds : f \in JFiles(j) /\ gh.ed[f] \in {fs.tc[<<j, f>>], fs.rt[<<j, f>>]}
  \/ \E j \in JIds : f \in JFiles(j) /\ jr[j].st \in OpenSt \cup {"kept"} /\ gh.ed[f] \in fs.kp[<<j, f>>]
ExternalEditNeverSilentlyLost ==
  \A f \in Files : gh.ed[f] # 0 /\ ~gh.edSeen[f] => EditKept(f)

\* recovery
NoTornLedger == \A f \in Files : fs.cur[f] = TORN => \E j \in JIds : jr[j].st \in OpenSt /\ f \in jr[j].files
ResolveDurable ==
  \A j \in JIds : (jr[j].st # "resolved" /\ jr'[j].st = "resolved") =>
     \* every file durably holds something other than our post bytes: its pre bytes (a created path: nothing)
     \A f \in jr[j].files : fs'.cur[f] \notin {jr[j].post[f], TORN}
                            /\ jr[j].post[f] \notin fs'.pl[f] /\ TORN \notin fs'.pl[f]
NoTornLedgerAfterRecovery == []NoTornLedger /\ [][ResolveDurable]_vars
\* ExternalEditNeverSilentlyLost for the files a journal names (the ones recovery and rollback swap and clean)
RecoveryNeverClobbersForeignBytes ==
  \A f \in Files : (gh.ed[f] # 0 /\ ~gh.edSeen[f] /\ \E j \in JIds : f \in jr[j].files) => EditKept(f)
RecoveryWritesOnlyRecordedPaths == ~gh.outW
\* a cut write of ours (at a staging name, whatever it holds) is never the reason a journal stops, and no temp of
\* ours outlives its save once the journal is closed (§6.1 step 8.4)
OnlyCutTemps(j) ==
  /\ jr[j].st = "open"
  /\ \A f \in jr[j].files :
        /\ ~PathMoved(f) /\ ~(env.unr /\ f = "K") /\ fs.kp[<<j, f>>] = {}
        /\ fs.tc[<<j, f>>] \in Held(j, f) /\ fs.rt[<<j, f>>] \in Held(j, f)
        /\ IF jr[j].pre[f] = ABS THEN fs.cur[f] = ABS
           ELSE fs.cur[f] = jr[j].pre[f] \/ (fs.cur[f] = jr[j].post[f] /\ ~BackupBad(j, f))
  /\ \E f \in jr[j].files : fs.sg[<<j, f>>] # ABS
CutTempNoBlock ==
  \A j \in JIds : (jr[j].why = "" /\ jr'[j].why = "stopped") => ~OnlyCutTemps(j)
NoTempOutlivesJournal ==
  \A j \in SaveIds, f \in Tg :
     (fs.tc[<<j, f>>] # ABS \/ fs.sg[<<j, f>>] # ABS) =>
        (jr[j].st \in {"open","topen","kept","part","tpart","none"} \/ (Busy /\ ctl.s = j))
CutTempNeverBlocks == [][CutTempNoBlock]_vars /\ []NoTempOutlivesJournal
\* "Keep the files as they are now" (§8.1): what the user keeps is what a power loss leaves (else a mix of the save's
\* pre and post bytes, which no journal names any more, could come back)
KeptFilesDurable ==
  [][\A j \in JIds : (jr[j].st = "open" /\ jr'[j].st = "kept") =>
        \A f \in jr[j].files : fs'.pl[f] = {fs'.cur[f]}
                               /\ \A x \in TSlots : x[2] = f => fs'.tpl[x] = {fs'.tc[x]}]_vars
\* INV-32: no work starts in a session before every journal from before its start was recovered or marked blocking
EveryJournalRecoveredOrBlocking ==
  app = "run" => \A j \in JIds : jr[j].st \in OpenSt /\ jr[j].old => jr[j].why # ""
\* a journal becomes blocking only with its cause present (written independently of the check's own predicate)
Cause(j, why) ==
  CASE why = "stopped" ->
         \/ jr[j].st = "topen"
         \/ \E f \in jr[j].files :
               \/ PathMoved(f) \/ (env.unr /\ f = "K")
               \/ fs.kp[<<j, f>>] # {} \/ fs'.kp[<<j, f>>] # {}               \* an outside save at a name of ours
               \/ fs.tc[<<j, f>>] \notin Held(j, f) \/ fs.rt[<<j, f>>] \notin Held(j, f)
               \/ (jr[j].pre[f] = ABS /\ fs.cur[f] # ABS)                     \* a created path taken
               \/ (jr[j].pre[f] # ABS /\ fs.cur[f] \notin {jr[j].pre[f], jr[j].post[f]})
               \/ (jr[j].pre[f] # ABS /\ fs.cur[f] = jr[j].post[f] /\ BackupBad(j, f))
    [] why = "failed" -> n'.crash > n.crash \/ n'.io > n.io
    [] OTHER -> FALSE
BlockedOnlyForCause ==
  [][\A j \in JIds : (jr[j].why = "" /\ jr'[j].why # "") => Cause(j, jr'[j].why)]_vars
\* copy: never over a file an unresolved journal names, blocking or in-session (INV-32)
NoCopyOverOpenJournal ==
  run.k = "copy" /\ ctl.pc # "cp_chk" => OpenOn(LS(2)) = {}

\* create
NoManifestOverIncompleteFolder ==
  /\ tgt.c["m"] = "ours" => (tgt.c["a"] = "ours" \/ gh.ovAfter)
  /\ "ours" \in tgt.pl["m"] => (tgt.pl["a"] = {"ours"} \/ gh.ovAfter)
NonEmptyTargetUntouched ==
  CreateTarget = "nonempty" => \A x \in {"a","m"} : tgt.c[x] # "ours" /\ "ours" \notin tgt.pl[x]
DroppedFileNeverReplaced == \A x \in gh.dropped : tgt.c[x] = "frn"
ForeignOverwriteCaughtByReadBack ==
  gh.ovBefore => tgt.c["m"] # "ours" /\ CrPc \notin {"cr_mr","cr_ms","cr_reg"}

----------------------------------------------------------------------------
(* WITNESSES (vacuity): each base world must reach the branch its witness  *)
(* names; the configuration checks the witness's negation, which TLC must  *)
(* report violated (MAPPING.md §6.2), so a base PASS is never vacuous      *)
\* a save lands and is told saved (S_save_crash)
W_Saved == gh.told["s1"] # "saved"
\* a rollback resolves its journal (S_save_crash)
W_RollbackResolved == ~(jr["s1"].st = "resolved" /\ ~Busy)
\* a rollback cannot resolve: Unfinished (S_save_crash)
W_RollbackUnfinished == gh.told["s1"] # "unfinished"
\* an outside save met by the save's swap is kept (S_save_crash)
W_KeptCopySave == \A x \in TSlots : fs.kp[x] = {}
\* a created path taken stops the journal, the other files undone (S_save_crash)
W_CreatedPathTaken == ~(jr["s1"].st = "open" /\ jr["s1"].why = "stopped" /\ fs.cur["N"] # ABS)
\* a rollback puts pre bytes back (S_save_crash)
W_RollbackPutsBack == ~(Busy /\ run.k = "save" /\ ctl.pc = "rs_c")
\* recovery resolves a boot journal (S_recover)
W_RecoverResolves == jr["b2"].st # "resolved"
\* recovery keeps an outside save it meets (S_recover)
W_RecoverKeptCopy == \A x \in TSlots : fs.kp[x] = {}
\* a try that fails marks its journal failed (S_recover)
W_RecoverFailed == jr["b2"].why # "failed"
\* Keep the files as they are now (S_keep)
W_Keep == \A j \in JIds : jr[j].st # "kept"
\* Keep of a journal whose file list cannot be read (S_recover_torn)
W_KeepTorn == jr["b3"].st # "kept"
\* after a power loss in the save, recovery resolves its journal (S_save_power)
W_CutStagingDeleted == ~(jr["s1"].st = "resolved" /\ app = "run")
\* a cut write at a staging name meets recovery (S_save_power)
W_StagingCutSeen == ~(\E x \in TSlots : fs.sg[x] = TORN /\ app = "recover")
\* a copy reaches its manifest (S_copy)
W_CopyDone == ~(run.k = "copy" /\ ctl.pc = "cp_man")
\* a copy meets an in-session .open journal (S_copy)
W_CopyRefusedInsession == ~(run.k = "copy" /\ ctl.pc = "cp_chk" /\ jr["s1"].st = "open")
\* a create registers (S_create_absent)
W_CreateDone == ~(run.k = "create" /\ ctl.pc = "cr_reg")
\* the second save lands (S_save_two)
W_SecondSaved == gh.told["s2"] # "saved"
\* the second save's rollback puts both files back (S_save_two)
W_SecondRollbackTwoFiles == ~(run.k = "save" /\ ctl.s = "s2" /\ ctl.pc = "rs_w" /\ ctl.put = {"R","N"})
\* a save cut by a power loss is undone (S_save_power)
W_PowerAfterSaveRecovered == ~(jr["s1"].st = "resolved")
\* the path guard stops a journal (S_recover_link)
W_LinkStop == jr["b2"].why # "stopped"
\* an outside save not synced is that program's loss (§13) (S_fs_weak)
W_WeakLossAccepted == ~(n.ed > 0 /\ \A f \in Files : gh.ed[f] = 0)
\* an outside save is kept on the weaker file system (S_fs_weak_recover)
W_WeakRecoverKept == \A x \in TSlots : fs.kp[x] = {}
\* a revert another program left unsynced meets the restore's flush (S_fs_weak_recover; S0101)
W_RevertFlushed == ~(Busy /\ ctl.pc = "rs_fl" /\ fs.dpl["R"] # {} /\ "R" \in Flushed)
\* an outside save after the gate is displaced into the temp (S_save_crash)
W_EditorAfterGate == ~(Busy /\ run.k = "save" /\ ctl.pc \in {"s_cmp"} /\ fs.tc[<<"s1","R">>] = 11)
=============================================================================
