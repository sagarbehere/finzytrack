------------------------------ MODULE EngineJobs ------------------------------
(***************************************************************************)
(* TLA+ model of design/specs/engine-jobs.md (draft 5 with the model      *)
(* findings F1-F4, O1, N1-N3 and model review 1 folded in, 4fb67b23),     *)
(* built only from that spec's §13 obligations and the sections it cites. *)
(* MAPPING.md maps every spec rule and property to this file, both ways,  *)
(* and lists what is abstracted away.                                      *)
(*                                                                         *)
(* World (§13): workspaces W1 = {R, N, I}, W2 = {R, J}, W3 = {K}; W1 and   *)
(* W2 share R, W3 shares nothing; K lives on a disk that may become        *)
(* unreachable. A save of W1 replaces R, creates N and leaves the include  *)
(* I alone. Journals b2 (W2: R, J) and b3 (W3: K) may be left at boot by   *)
(* crashed saves; s1..s3 are the journals of the saves made in the model.  *)
(* File contents are version numbers: 0 absent, -1 torn, 1 initial,        *)
(* 2 the boot journals' post bytes, 3..5 the posts of saves s1..s3,        *)
(* 11..15 and 21..25 an outside editor's bytes.                            *)
(*                                                                         *)
(* Durability: every name (ledger file, temp, backup, journal, target      *)
(* name) holds its current content and the set of contents it may hold    *)
(* after a power loss (`pl`). A write that is not synced adds the torn     *)
(* and the absent outcomes; a rename that is not yet dir-synced keeps the  *)
(* old outcome possible.                                                   *)
(*                                                                         *)
(* MUTANT selects one seeded bug ("none" = the spec as written).           *)
(***************************************************************************)
EXTENDS Integers, FiniteSets, Sequences, TLC

CONSTANTS
    MUTANT,         \* "none" or the name of one seeded bug (MAPPING.md, mutants)
    UserBudget,     \* 0: user exits unbounded (safety); n > 0: at most n of them (liveness worlds driven by
                    \* engine and app fairness assume the user eventually stops restarting work)
    BootJ,          \* subset of {"b2","b3"}: journals left by crashed saves at boot
    BootTemps,      \* TRUE: a boot journal's file may have a temp beside it (cut, pre or post)
    BootTarget,     \* the workspace the first boot opens (0 = none)
    BootOrphan,     \* TRUE: an orphaned engine of the crashed session holds b2's locks at boot
    BootLink,       \* TRUE: after the crash, R's folder was replaced by a link (the path changer)
    EdFiles,        \* the ledger files the outside editor may write
    R4Outs,         \* subset of {"pass","nondet","differ"}: what a fresh load may give
    StopBound,      \* S0040: stops in the window that make the engine Unavailable (3)
    MaxSaves, MaxBg, MaxReads, MaxRebuild, MaxEdits, MaxCrash, MaxAppCrash,
    MaxPower, MaxStarts, MaxSwitch, MaxHang, MaxStuck, StuckSigOnly, MaxOrphan,
    MaxUnreach, MaxLink, MaxForget, MaxIO, MaxClose, MaxUpdate,
    MaxExpire, MaxDrop, MaxResume,
    DoCopy,         \* TRUE: the user may copy W2 (app-scoped)
    CreateTarget,   \* "none" | "absent" | "empty" | "nonempty"
    DoQuit,         \* TRUE: the user may quit (graceful) or update
    EdDurable,      \* TRUE: the outside editor's saves are durable (it syncs its folder); FALSE: §13's
                    \* weaker file system, where its save and our unsynced operations persist independently
    RestartAfterQuit, \* TRUE: a next start may follow a quit or Quit anyway (else only a crash or power loss)
    CloseQueued,    \* TRUE: closing a tab may also hit a queued job (FALSE where only Cancel is the waiting user's exit)
    JobBound        \* upper bound of job ids (liveness quantifies over 1..JobBound)

ASSUME MUTANT \in STRING /\ BootJ \subseteq {"b2","b3"} /\ BootTarget \in 0..3
ASSUME CreateTarget \in {"none","absent","empty","nonempty"}
ASSUME MaxSaves + MaxBg <= 3

----------------------------------------------------------------------------
(* Basic sets                                                              *)
Files   == {"R","N","I","J","K"}
Idx(f)  == CASE f = "R" -> 1 [] f = "N" -> 2 [] f = "I" -> 3 [] f = "J" -> 4 [] f = "K" -> 5
WS      == {1, 2, 3}
LS(w)   == CASE w = 1 -> {"R","N","I"} [] w = 2 -> {"R","J"} [] w = 3 -> {"K"} [] OTHER -> {}
Tg      == {"R","N"}            \* what a W1 save writes: R replaced, N created
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
TSlots   == UNION {{<<j, f>> : f \in JFiles(j)} : j \in JIds}   \* a save's temp beside each file it names
ROrd(j)  == CASE j = "b2" -> <<"R","J">> [] j = "b3" -> <<"K">> [] OTHER -> <<"R","N">>  \* root first
WOrd     == <<"N","R">>        \* children first, root last (§6.1 step 8.3)
Frn(f, v) == IF v = 10 + Idx(f) THEN 20 + Idx(f) ELSE 10 + Idx(f)
CL       == {"closed","done","mismatch"}
OpenSt   == {"open","topen"}
Names    == {"a","m","o","x"}   \* create target: a = a file the job writes, m = manifest, o = another name, x = prior content
ZeroFP   == [f \in Files |-> 0]
Reads    == {"read","rebuild"}
Writing(k) == k \in {"save","create","copy"}
Min(S)   == CHOOSE x \in S : \A y \in S : x <= y

NoJ == [st |-> "none", pl |-> {"none"}, lin |-> 0, key |-> 0, files |-> {},
        pre |-> ZeroFP, post |-> ZeroFP, pfp |-> ZeroFP, why |-> "", old |-> FALSE,
        build |-> 0, sb |-> FALSE, cnc |-> FALSE, att |-> 0]
BootJRec(j) == [NoJ EXCEPT !.st = "open", !.pl = {"open"},
                  !.lin = IF j = "b2" THEN 2 ELSE 3, !.key = IF j = "b2" THEN 2 ELSE 3,
                  !.files = JFiles(j),
                  !.pre = [f \in Files |-> IF f \in JFiles(j) THEN V0 ELSE 0],
                  !.post = [f \in Files |-> IF f \in JFiles(j) THEN BPOST ELSE 0],
                  !.build = 1]
NoEnded == [k |-> "none", wf |-> FALSE, conf |-> FALSE, req |-> "present", past |-> FALSE]
NoEng   == [p |-> 0, st |-> "none", user |-> FALSE, stuck |-> FALSE, reason |-> "none",
            endBy |-> "none", ended |-> NoEnded]
NoCtl   == [pc |-> "none", s |-> "none", plan |-> ZeroFP, past |-> FALSE, hung |-> FALSE,
            fi |-> 1, rj |-> "none", tried |-> {}, ofp |-> ZeroFP, tries |-> 0,
            r4j |-> "none", r4ctx |-> "none", r4b |-> FALSE, r4o |-> "none", rbt |-> "none",
            skip |-> "none", rinit |-> FALSE]
Job(id, k, w, wf, conf, own, s, plan) ==
    [id |-> id, k |-> k, w |-> w, wf |-> wf, conf |-> conf, req |-> "present",
     own |-> own, s |-> s, plan |-> plan]
NoCnt == [ed |-> 0, crash |-> 0, ac |-> 0, pw |-> 0, start |-> 0, sw |-> 0, hang |-> 0,
          stuck |-> 0, orph |-> 0, unr |-> 0, link |-> 0, forget |-> 0, io |-> 0,
          close |-> 0, upd |-> 0, exp |-> 0, drop |-> 0,
          save |-> 0, bg |-> 0, read |-> 0, rebuild |-> 0, copy |-> 0, create |-> 0,
          resume |-> 0, ua |-> 0]

----------------------------------------------------------------------------
VARIABLES
    fs,    \* ledger files: cur, pl, save temps tc/tpl (per journal), backups bk/bkpl (per save), restore temps rt
    jr,    \* the records in local/*/journal/: one per journal id
    pst,   \* other durable state: build, W1's registry key, quit notes (one per job), link, unreachable disk, orphans, mirror
    tgt,   \* the create's target folder: names c/pl, folder exists
    ses,   \* the sidecar session: app phase, stage, open workspace, screen, stops, advice, locks
    q,     \* the queue (a sequence of jobs)
    runs,  \* the running job (a set so that AtMostOneJobRunning can be stated)
    ctl,   \* the running job's progress
    eng,   \* the engine process(es) and their group
    n,     \* counters that bound the environment and the user
    gh     \* ghost: what the user was told, and history the properties need

vars == <<fs, jr, pst, tgt, ses, q, runs, ctl, eng, n, gh>>

----------------------------------------------------------------------------
(* Initial state: the first boot                                           *)
BootFiles == (IF "b2" \in BootJ THEN {"R","J"} ELSE {}) \cup (IF "b3" \in BootJ THEN {"K"} ELSE {})
BI(f) == IF f \in BootFiles THEN {V0, BPOST} ELSE {V0}
TI(j, v) == IF j \in BootJ /\ BootTemps
            THEN (IF v = BPOST THEN {ABS, V0} ELSE {ABS, TORN, BPOST})
            ELSE {ABS}
RecJob(id) == Job(id, "recover", 0, TRUE, FALSE, "switch", "none", ZeroFP)
PreFP(w) == [f \in Files |-> IF f \in LS(w) THEN (IF f = "N" THEN ABS ELSE V0) ELSE 0]

Init ==
  \E cR \in BI("R"), cJ \in BI("J"), cK \in BI("K"):
  \E tR \in TI("b2", cR), tJ \in TI("b2", cJ), tK \in TI("b3", cK):
    LET c  == [f \in Files |-> CASE f = "R" -> cR [] f = "J" -> cJ [] f = "K" -> cK
                                 [] f = "N" -> ABS [] OTHER -> V0]
        tc == [x \in TSlots |-> CASE x = <<"b2","R">> -> tR [] x = <<"b2","J">> -> tJ
                                  [] x = <<"b3","K">> -> tK [] OTHER -> ABS]
    IN
    /\ fs = [cur |-> c, pl |-> [f \in Files |-> {c[f]}], tc |-> tc,
             tpl |-> [x \in TSlots |-> {tc[x]}],
             bk |-> [s \in SaveIds |-> ABS], bkpl |-> [s \in SaveIds |-> {ABS}],
             rt |-> [f \in Files |-> ABS],
             cp |-> [f \in Files |-> {}], cpn |-> [f \in Files |-> {}], cpj |-> [f \in Files |-> "none"]]
    /\ jr = [j \in JIds |-> IF j \in BootJ THEN BootJRec(j) ELSE NoJ]
    /\ pst = [build |-> 1, reg1 |-> 1, notes |-> {}, lnk |-> BootLink, outside |-> 0,
              unr |-> FALSE, orphan |-> IF BootOrphan THEN JFiles("b2") ELSE {}, ostuck |-> FALSE,
              last |-> BootTarget,
              mirror |-> [w \in WS |-> [fp |-> PreFP(w), src |-> "open", build |-> 1]]]
    /\ tgt = [ex |-> CreateTarget \in {"empty","nonempty"},
              c  |-> [x \in Names |-> IF x = "x" /\ CreateTarget = "nonempty" THEN "frn" ELSE "no"],
              pl |-> [x \in Names |-> {IF x = "x" /\ CreateTarget = "nonempty" THEN "frn" ELSE "no"}]]
    /\ ses = [app |-> "boot", stage |-> "recover", opened |-> {}, from |-> 0,
              target |-> BootTarget, screen |-> "none", sctx |-> "none", endedBy |-> "none",
              stops |-> 0, advice |-> "none", locks |-> {}, mem |-> {}]
    /\ q = <<RecJob(1)>>
    /\ runs = {}
    /\ ctl = NoCtl
    /\ eng = NoEng
    /\ n = NoCnt
    /\ gh = [told |-> [s \in SaveIds |-> "none"], cxl |-> [s \in SaveIds |-> FALSE],
             pre |-> [s \in SaveIds |-> ZeroFP],
             ed |-> [f \in Files |-> 0], edSeen |-> [f \in Files |-> FALSE],
             acc |-> [f \in Files |-> {}],
             edLoad |-> FALSE, outW |-> FALSE,
             dropped |-> {}, ovBefore |-> FALSE, ovAfter |-> FALSE,
             crOut |-> "none", crMsg |-> "none", crChecked |-> FALSE, crTold |-> "none", cpMan |-> FALSE, cpTold |-> "none",
             resumed |-> 0]

----------------------------------------------------------------------------
(* Helpers                                                                 *)
Alive   == ses.app # "dead"
Busy    == runs # {}
RJ      == CHOOSE r \in runs : TRUE
Unavail == ses.stops >= StopBound
EngOK   == eng.st = "busy" /\ ~eng.stuck /\ ~ctl.hung
Step(p) == Busy /\ ctl.pc = p /\ EngOK
SpawnPcs == {"s_spawn","r4_spawn","rc_spawn"}
FP(w)   == [f \in Files |-> IF f \in LS(w) THEN fs.cur[f] ELSE 0]
KeyOf(w) == IF w = 1 THEN pst.reg1 ELSE w
\* §6.5: a workspace's records are those whose registry key or lineage id is the workspace's
Match(j, w) == jr[j].st # "none" /\
               IF MUTANT = "gate_by_key" THEN jr[j].key = KeyOf(w)
               ELSE jr[j].lin = w \/ jr[j].key = KeyOf(w)
OpenJ(w)    == {j \in JIds : jr[j].st \in OpenSt /\ Match(j, w)}
ClosedJ(w)  == {j \in JIds : jr[j].st = "closed" /\ Match(j, w)}
MismJ(w)    == {j \in JIds : jr[j].st = "mismatch" /\ Match(j, w)}
\* the gate's view: the files (§6.5), or, for the seeded bug, the in-memory cache
GateClosed(w) == CASE MUTANT = "gate_in_memory" -> {j \in ses.mem : jr[j].st = "closed"}
                   [] MUTANT = "r4_skipped_before_save" -> {}
                   [] OTHER -> ClosedJ(w)
GateMism(w)   == IF MUTANT = "gate_in_memory" THEN {} ELSE MismJ(w)
RemoveAt(s, i) == SubSeq(s, 1, i - 1) \o SubSeq(s, i + 1, Len(s))
InQ(id) == \E i \in 1..Len(q) : q[i].id = id
QHas(P(_)) == \E i \in 1..Len(q) : P(q[i])
Ended(r) == [k |-> r.k, wf |-> r.wf, conf |-> r.conf, req |-> r.req, past |-> ctl.past]
Retired == IF eng.user THEN [eng EXCEPT !.st = "sig", !.reason = "retire"]
           ELSE [eng EXCEPT !.st = "idle"]
SigEng(reason, by) == [eng EXCEPT !.st = "sig", !.reason = reason, !.endBy = by, !.ended = Ended(RJ)]
\* our end of a running job: its engine is signalled; a job between two engines holds none
CancelEng(by) == IF eng.st = "busy" THEN SigEng("cancel", by) ELSE eng
Spawned == [p |-> eng.p + 1, st |-> "busy", user |-> FALSE, stuck |-> FALSE, reason |-> "none",
            endBy |-> "none", ended |-> NoEnded]
Reused  == [eng EXCEPT !.st = "busy"]
\* §5.1: a new engine only after the previous one is reaped and its group has no live member
SlotFree == eng.p = 0 \/ (eng.st = "idle" /\ ~eng.user)
            \/ (MUTANT = "spawn_before_reap" /\ eng.st = "sig")
Fresh == IF eng.st = "idle" /\ ~eng.user THEN Reused ELSE Spawned
SetTold(g, s, m) == [g EXCEPT !.told[s] = m]
\* §6.6 / §11: "unchanged" only after the targets were hashed again and equal their pre bytes
Rehash(s) == IF MUTANT = "unchanged_without_rehash" \/ \A f \in Tg : fs.cur[f] = gh.pre[s][f]
             THEN "unchanged" ELSE "changed"
\* §6.6: the outcome after an engine death, a pure function of the records and file hashes
DiskOutcome(s) == CASE jr[s].st \in CL -> "saved"
                    [] jr[s].st = "resolved" -> "notsaved"
                    [] jr[s].st \in OpenSt -> "unfinished"
                    [] OTHER -> Rehash(s)
\* §4: another job's Cancel ends the running job only when nobody waits for it, the user did
\* not confirm it, and it has not reached its commit point
Endable(e) ==
    CASE MUTANT = "r4_only" -> e.k = "r4"
      [] MUTANT = "cancel_ends_writing_job" ->
            e.k = "r4" \/ (Writing(e.k) /\ ~e.past) \/ (e.k \in Reads \cup {"open"} /\ e.req = "gone")
      [] OTHER -> e.k = "r4" \/ (e.k = "save" /\ ~e.conf /\ ~e.past)
                  \/ (e.k \in Reads \cup {"open"} /\ e.req = "gone")
\* §3: during a switch, a Reopen and the boot, after the current workspace has closed, only its own jobs start
\* §3 (F4): a switch that stops on its failed or blocked screen has done its own work, like the boot
SwitchExclusive == ses.app = "boot"
                   \/ (ses.app = "switch" /\ ses.stage # "quiesce"
                       /\ ~(MUTANT # "screen_holds_queue" /\ ses.stage = "screen"))
Elig(j) == IF SwitchExclusive THEN j.own = "switch" \/ (MUTANT = "app_scoped_during_recovery" /\ j.w = 0)
           ELSE (j.own = "switch" /\ MUTANT = "recovery_while_open")
                \/ (j.own # "switch" /\ (j.w = 0 \/ j.w \in ses.opened))
EligIdx   == {i \in 1..Len(q) : Elig(q[i])}
EligWfIdx == {i \in EligIdx : q[i].wf}
\* §3: the oldest queued waited-for job; if none, the oldest background job
Max(S) == CHOOSE x \in S : \A y \in S : x >= y
PickIdx == CASE MUTANT = "newest_first" -> (IF EligWfIdx = {} THEN Max(EligIdx) ELSE Max(EligWfIdx))
             [] MUTANT = "dispatch_by_arrival" \/ EligWfIdx = {} -> Min(EligIdx)
             [] OTHER -> Min(EligWfIdx)
Blocked(t) == \E j \in JIds :
                 \/ jr[j].st = "topen"
                 \/ /\ jr[j].st = "open" /\ jr[j].files \cap LS(t) # {}
                    /\ (MUTANT = "recover_target_only" => jr[j].lin = t)
RecUntried == {j \in JIds : jr[j].st \in OpenSt /\ j \notin ctl.tried
                           /\ (MUTANT = "recover_target_only" => jr[j].lin = ses.target)}
FirstJ(S) == JOrder[Min({i \in 1..Len(JOrder) : JOrder[i] \in S})]
\* job ids are canonical: the lowest id not in use (a job's id is free again once it has left)
UsedIds == {q[k].id : k \in 1..Len(q)} \cup {r.id : r \in runs}
NewId == CHOOSE i \in 1..(JobBound + 1) : i \notin UsedIds /\ \A k \in 1..i - 1 : k \in UsedIds
NewR4(w) == Job(NewId, "r4", w, FALSE, FALSE, "app", "none", ZeroFP)
NeedR4(w) == ClosedJ(w) # {} /\ ~QHas(LAMBDA j : j.k = "r4" /\ j.w = w)
QueueR4(w) == IF NeedR4(w) THEN q \o <<NewR4(w)>> ELSE q
\* the user's budget of exits (liveness worlds only; UserBudget = 0 leaves them unbounded)
CanAct == UserBudget = 0 \/ n.ua < UserBudget
Spent == IF UserBudget = 0 THEN n ELSE [n EXCEPT !.ua = @ + 1]
NoteAfterKeep(J) == {nt \in pst.notes : ~(nt.k = "save" /\ nt.s \in J)}

----------------------------------------------------------------------------
(* The job ends (final reply); the engine is retired if it ran user code (§5.1) *)
JobEnd(e) == runs' = {} /\ ctl' = NoCtl /\ eng' = e
EndSesAdv == [ses EXCEPT !.advice = "none"]

\* §8.2: a stop before the manifest; the folder is listed when the job stops
CrWrote == tgt.c["a"] = "ours" \/ tgt.c["m"] = "ours"
CrOnlyOwn == \A x \in Names : tgt.c[x] \in {"no","ours"}
CrStopGh(cause) ==
  IF RJ.k = "create" THEN
    gh' = [gh EXCEPT !.crOut = cause,
                     !.crMsg = IF ~CrWrote THEN "not_created"
                               ELSE IF MUTANT = "delete_message_always" \/ CrOnlyOwn THEN "delete_ok"
                               ELSE "check_first"]
  ELSE UNCHANGED gh

\* The outcome of a running job that our end (Cancel, quiesce, close of its tab) ended.
\* Used by every path that signals the engine with reason `cancel` (§4, §9.1 step 2).
CancelEffects(by) ==
  LET r == RJ IN
  /\ JobEnd(CancelEng(by))
  /\ CASE r.k = "save" ->
            /\ gh' = [gh EXCEPT !.told[r.s] = Rehash(r.s),
                                !.cxl[r.s] = TRUE]
            /\ UNCHANGED <<ses, pst, q, n>>
       [] r.k = "open" ->
            /\ ses' = IF ses.app = "boot"
                      THEN [ses EXCEPT !.app = "run", !.stage = "none", !.screen = "failed",
                                       !.sctx = "boot", !.advice = "none"]
                      ELSE [ses EXCEPT !.stage = "screen", !.screen = "failed",
                                       !.sctx = IF ses.from = 0 THEN "boot" ELSE "switch", !.advice = "none"]
            /\ UNCHANGED <<gh, pst, q, n>>
       [] r.k \in {"create","copy"} ->
            /\ CrStopGh("cancel")
            /\ UNCHANGED <<ses, pst, q, n>>
       [] OTHER -> UNCHANGED <<gh, ses, pst, q, n>>
  /\ UNCHANGED <<fs, jr, tgt>>

\* recover_all has ended: every journal recovered or blocking. At boot the quit note is read.
\* §9.3: each note (local/quit-notes/<job>.json) answered from the durable record or target
SaveNoteMsg(sv) ==
  LET st == jr[sv].st IN
  CASE MUTANT = "quit_says_undone" -> "q_undone"
    [] st \in CL -> "q_saved"
    [] MUTANT = "quit_note_two_branches" -> "q_undone"
    [] st \in {"resolved","none","part","tpart"} -> "q_undone"
    [] st = "kept" -> "none"
    [] OTHER -> "q_notyet"
SaveNotes == {nt.s : nt \in {x \in pst.notes : x.k = "save"}}
NoteNotes == {nt \in pst.notes : nt.k = "save" /\ SaveNoteMsg(nt.s) = "q_notyet"}   \* kept until answered
NoteGh ==
  [gh EXCEPT !.told = [sv \in SaveIds |-> IF sv \in SaveNotes /\ SaveNoteMsg(sv) # "none"
                                          THEN SaveNoteMsg(sv) ELSE gh.told[sv]],
             !.crTold = IF \E nt \in pst.notes : nt.k = "create"
                        THEN (IF tgt.c["m"] = "ours" THEN "q_created" ELSE "q_notcreated") ELSE @,
             !.cpTold = IF \E nt \in pst.notes : nt.k = "copy"
                        THEN (IF gh.cpMan THEN "q_created" ELSE "q_notcreated") ELSE @]
\* §6.6, §8.1 (F1): whichever way recover_all ends, the journal directories it lists are synced
\* before the quit note or the open reads a record
SyncCL(J) == [j \in JIds |-> IF MUTANT # "outcome_unsynced" /\ J[j].st \in CL
                               THEN [J[j] EXCEPT !.pl = {J[j].st}] ELSE J[j]]
RecoverEndEffects(e, J) ==
  /\ jr' = SyncCL(J)
  /\ JobEnd(e)
  /\ ses' = [ses EXCEPT !.stage = "assess", !.locks = {}]
  /\ IF ses.app = "boot"
     THEN /\ pst' = [pst EXCEPT !.notes = NoteNotes]
          /\ gh' = NoteGh
     ELSE UNCHANGED <<pst, gh>>
  /\ UNCHANGED <<fs, tgt, q, n>>

----------------------------------------------------------------------------
(* USER ACTIONS                                                            *)

\* a confirmed save of W1, planned on the published mirror (the preview read it)
UserSave ==
  /\ ses.app = "run" /\ 1 \in ses.opened /\ n.save < MaxSaves
  /\ LET s == SId(n.save + n.bg + 1) IN
     /\ q' = q \o <<Job(NewId, "save", 1, TRUE, TRUE, "user", s, pst.mirror[1].fp)>>
     /\ n' = [n EXCEPT !.save = @ + 1]
     /\ gh' = [gh EXCEPT !.pre[s] = pst.mirror[1].fp]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, runs, ctl, eng>>

\* preview or BQL: one transient load, waited-for
UserRead(w) ==
  /\ ses.app = "run" /\ w \in ses.opened /\ n.read < MaxReads
  /\ q' = q \o <<Job(NewId, "read", w, TRUE, FALSE, "user", "none", ZeroFP)>>
  /\ n' = [n EXCEPT !.read = @ + 1]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, runs, ctl, eng, gh>>

UserCreate ==
  /\ Alive /\ ses.app \notin {"quit","update"} /\ CreateTarget # "none" /\ n.create = 0
  /\ q' = q \o <<Job(NewId, "create", 0, TRUE, TRUE, "user", "none", ZeroFP)>>
  /\ n' = [n EXCEPT !.create = 1]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, runs, ctl, eng, gh>>

UserCopy ==
  /\ Alive /\ ses.app \notin {"quit","update"} /\ DoCopy /\ n.copy = 0
  /\ q' = q \o <<Job(NewId, "copy", 0, TRUE, TRUE, "user", "none", ZeroFP)>>
  /\ n' = [n EXCEPT !.copy = 1]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, runs, ctl, eng, gh>>

\* §4: Cancel on a queued job; for the job running ahead of it, the one rule
CancelableQ(i) == q[i].wf /\ q[i].req = "present" /\ q[i].own \in {"user","app"}
UserCancelQueued(i) ==
  /\ Alive /\ i \in 1..Len(q) /\ CancelableQ(i)
  /\ LET j  == q[i]
         q1 == RemoveAt(q, i)
         g1 == IF j.k = "save" THEN [gh EXCEPT !.told[j.s] = "notsaved", !.cxl[j.s] = TRUE] ELSE gh
     IN
     IF Busy /\ Endable(Ended(RJ)) /\ RJ.k # "recover"
     THEN \* ends the running job: an R4 load, a background save before its commit point,
          \* a read whose requester has gone
          LET r == RJ IN
          /\ JobEnd(CancelEng("other"))
          /\ q' = q1
          /\ gh' = IF r.k = "save" THEN [g1 EXCEPT !.told[r.s] = Rehash(r.s), !.cxl[r.s] = TRUE] ELSE g1
          /\ ses' = EndSesAdv
          /\ UNCHANGED <<fs, jr, pst, tgt, n>>
     ELSE
          /\ q' = q1 /\ gh' = g1
          /\ ses' = IF Busy /\ ~(RJ.k \in Reads \cup {"open","recover"} /\ RJ.req = "present")
                       /\ MUTANT # "no_quit_message_for_writing"
                    THEN [ses EXCEPT !.advice = "cancel"]   \* "Your request is cancelled. The earlier ..."
                    ELSE ses
          /\ UNCHANGED <<fs, jr, pst, tgt, runs, ctl, eng, n>>

\* §4: Cancel by the running job's own requester
UserCancelRunning ==
  /\ Alive /\ Busy /\ (eng.st = "busy" \/ ctl.pc \in SpawnPcs) /\ RJ.wf /\ RJ.req = "present"
  /\ ~ctl.past \/ MUTANT = "cancel_after_commit"
  \* recover_all waiting for a new engine holds none: nothing to cancel (the timer advice covers a stuck slot)
  /\ RJ.k = "recover" => eng.st = "busy"
  /\ IF RJ.k = "recover"
     THEN \* §8.1: the journal being recovered counts as blocking (cancelled); go on in a new engine
          IF MUTANT = "cancel_stops_all"
          THEN RecoverEndEffects(CancelEng("own"),
                                 IF ctl.rj # "none" THEN [jr EXCEPT ![ctl.rj].why = "cancelled"] ELSE jr)
          ELSE \* the journal it is on, or, before one is in hand, the next one it would try
               \* (with nothing in hand and nothing left to try, recover_all is done: it ends)
               LET cj == IF ctl.rj # "none" THEN ctl.rj
                         ELSE IF RecUntried # {} THEN FirstJ(RecUntried) ELSE "none"
               IN
               IF cj = "none"
               THEN RecoverEndEffects(CancelEng("own"), jr)
               ELSE /\ jr' = [jr EXCEPT ![cj].why = "cancelled"]
                    /\ eng' = CancelEng("own")
                    /\ ctl' = [ctl EXCEPT !.pc = "rc_spawn", !.rj = "none", !.tried = @ \cup {cj}]
                    /\ UNCHANGED <<fs, pst, tgt, ses, q, runs, n, gh>>
     ELSE CancelEffects("own")

\* closing a tab or aborting a request: cancels a preview, rebuild or BQL; never a save (§4)
UserCloseTab(id) ==
  /\ Alive /\ n.close < MaxClose
 /\ \/ /\ CloseQueued
        /\ \E i \in 1..Len(q) :
             /\ q[i].id = id /\ q[i].req = "present" /\ q[i].own \in {"user","app"}
             /\ IF q[i].k \in Reads THEN q' = RemoveAt(q, i)
                ELSE q' = [q EXCEPT ![i].req = "gone"]
        /\ n' = [n EXCEPT !.close = @ + 1]
        /\ UNCHANGED <<fs, jr, pst, tgt, ses, runs, ctl, eng, gh>>
     \/ /\ Busy /\ RJ.id = id /\ RJ.req = "present" /\ (RJ.own \in {"user","app"} \/ RJ.k = "open")
        /\ n' = [n EXCEPT !.close = @ + 1]
        /\ IF RJ.k \in Reads /\ eng.st = "busy"
           THEN /\ JobEnd(SigEng("cancel", "own"))
                /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, gh>>
           ELSE /\ runs' = {[RJ EXCEPT !.req = "gone"]}
                /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, ctl, eng, gh>>

\* §9.1 step 2: the quiesce of the open workspace A (switch, Reopen, quit, update)
QuiesceKeep(A, j) == j.w # A \/ (j.k = "save" /\ j.conf /\ MUTANT # "switch_drops_confirmed_save")
DroppedTold(A) ==
  LET D == {i \in 1..Len(q) : ~QuiesceKeep(A, q[i]) /\ q[i].k = "save" /\ q[i].conf}
  IN [gh EXCEPT !.told = [s \in SaveIds |->
                            IF \E i \in D : q[i].s = s THEN "notopen" ELSE gh.told[s]]]
QuiesceEndsRunning(A) ==
  /\ Busy /\ RJ.w = A /\ eng.st = "busy"
  /\ (RJ.k \in Reads \cup {"r4"} \/ (RJ.k = "save" /\ ~RJ.conf /\ ~ctl.past))
Quiesce(A, newSes, extra) ==
  LET q1 == SelectSeq(q, LAMBDA j : QuiesceKeep(A, j)) IN
  /\ q' = q1 \o extra
  /\ IF A # 0 /\ QuiesceEndsRunning(A)
     THEN LET r == RJ IN
          /\ JobEnd(SigEng("cancel", "quiesce"))
          /\ gh' = IF r.k = "save" THEN [DroppedTold(A) EXCEPT !.told[r.s] = Rehash(r.s), !.cxl[r.s] = TRUE]
                   ELSE DroppedTold(A)
     ELSE /\ gh' = DroppedTold(A) /\ UNCHANGED <<runs, ctl, eng>>
  /\ ses' = newSes

OpenW == IF ses.opened = {} THEN 0 ELSE CHOOSE w \in ses.opened : TRUE

\* §9.1: the switch (t = the open workspace is Reopen); from no open workspace, an ordinary switch
UserSwitch(t) ==
  /\ ses.app = "run" /\ ses.screen = "none" /\ n.sw < MaxSwitch /\ t \in WS
  /\ IF ses.opened = {}
     THEN /\ ses' = [ses EXCEPT !.app = "switch", !.stage = "recover", !.from = 0, !.target = t]
          /\ q' = q \o <<RecJob(NewId)>>
          /\ n' = [n EXCEPT !.sw = @ + 1]
          /\ UNCHANGED <<fs, jr, pst, tgt, runs, ctl, eng, gh>>
     ELSE LET A == OpenW IN
          IF MUTANT = "open_before_close"
          THEN /\ ses' = [ses EXCEPT !.app = "switch", !.stage = "recover", !.from = A, !.target = t]
               /\ q' = q \o <<RecJob(NewId)>>
               /\ n' = [n EXCEPT !.sw = @ + 1]
               /\ UNCHANGED <<fs, jr, pst, tgt, runs, ctl, eng, gh>>
          ELSE LET extra == IF MUTANT = "recovery_while_open" THEN <<RecJob(NewId)>> ELSE <<>> IN
               /\ Quiesce(A, [ses EXCEPT !.app = "switch", !.stage = "quiesce", !.from = A, !.target = t], extra)
               /\ n' = [n EXCEPT !.sw = @ + 1]
               /\ UNCHANGED <<fs, jr, pst, tgt>>

\* §9.1 step 3: Stay cancels the switch until A has closed
GStay == ses.app = "switch" /\ ses.stage = "quiesce"
UserStay ==
  /\ GStay /\ MUTANT # "stay_disabled" /\ CanAct
  /\ ses' = [ses EXCEPT !.app = "run", !.stage = "none", !.target = ses.from]
  /\ n' = Spent
  /\ UNCHANGED <<fs, jr, pst, tgt, q, runs, ctl, eng, gh>>

\* the failed or blocked screen (§8.1, §9.1 step 6, §9.2)
GRetry  == ses.screen # "none" /\ ~(MUTANT = "boot_no_actions" /\ ses.sctx = "boot")
GBack   == ses.screen # "none" /\ ses.sctx = "switch" /\ ses.from # 0
GOther  == ses.screen # "none" /\ ses.sctx = "boot" /\ MUTANT # "boot_no_actions"
GKeep   == ses.screen = "blocked" /\ ~(MUTANT = "boot_no_actions" /\ ses.sctx = "boot")
UserRetry ==
  /\ GRetry /\ MUTANT # "retry_disabled" /\ CanAct
  /\ n' = Spent
  /\ IF ses.screen = "blocked"
     THEN /\ ses' = [ses EXCEPT !.app = "switch", !.stage = "recover", !.screen = "none"]
          /\ q' = q \o <<RecJob(NewId)>>
     ELSE /\ ses' = [ses EXCEPT !.app = "switch", !.stage = "assess", !.screen = "none"]
          /\ UNCHANGED q
  /\ UNCHANGED <<fs, jr, pst, tgt, runs, ctl, eng, gh>>
UserBack ==
  /\ GBack /\ CanAct
  /\ ses' = [ses EXCEPT !.app = "switch", !.stage = "assess", !.screen = "none", !.target = ses.from]
  /\ n' = Spent
  /\ UNCHANGED <<fs, jr, pst, tgt, q, runs, ctl, eng, gh>>
UserOpenAnother(t) ==
  /\ GOther /\ t \in WS /\ t # ses.target /\ CanAct
  /\ ses' = [ses EXCEPT !.app = "switch", !.stage = "recover", !.screen = "none",
                        !.from = 0, !.target = t]
  /\ q' = q \o <<RecJob(NewId)>>
  /\ n' = Spent
  /\ UNCHANGED <<fs, jr, pst, tgt, runs, ctl, eng, gh>>
\* "Keep the files as they are now" (§8.1): writes nothing to the ledger files; first durable_syncs every
\* file and folder the journal names (N1), then renames the journal .kept (its directory synced)
KeepSet == {j \in JIds : jr[j].st = "topen" \/ (jr[j].st = "open" /\ jr[j].files \cap LS(ses.target) # {})}
UserKeep ==
  /\ GKeep /\ CanAct
  /\ jr' = [j \in JIds |-> IF j \in KeepSet THEN [jr[j] EXCEPT !.st = "kept", !.pl = {"kept"}] ELSE jr[j]]
  /\ pst' = [pst EXCEPT !.notes = NoteAfterKeep(KeepSet),
                        !.lnk = IF \E j \in KeepSet : "R" \in jr[j].files THEN FALSE ELSE @]
  /\ ses' = [ses EXCEPT !.app = "switch", !.stage = "assess", !.screen = "none"]
  /\ n' = Spent
  \* N1: "as they are now" made durable before the journal is renamed .kept
  /\ fs' = IF MUTANT = "keep_no_sync" THEN fs
           ELSE LET F == UNION {jr[j].files : j \in KeepSet} IN
                [fs EXCEPT !.pl = [f \in Files |-> IF f \in F THEN {fs.cur[f]} ELSE fs.pl[f]],
                           !.cp = [f \in Files |-> IF f \in F THEN {} ELSE fs.cp[f]],
                           !.cpn = [f \in Files |-> IF f \in F THEN {} ELSE fs.cpn[f]],
                           !.cpj = [f \in Files |-> IF f \in F THEN "none" ELSE fs.cpj[f]],
                           !.tpl = [x \in TSlots |-> IF x[1] \in KeepSet THEN {fs.tc[x]} ELSE fs.tpl[x]]]
  /\ UNCHANGED <<tgt, q, runs, ctl, eng, gh>>

\* §6.5: Resume saving renames a .mismatch record .done (with a confirmation)
UserResume ==
  /\ ses.app = "run" /\ 1 \in ses.opened /\ MismJ(1) # {} /\ n.resume < MaxResume
  /\ \E j \in MismJ(1) : jr' = [jr EXCEPT ![j].st = "done", ![j].pl = {"done"}]
  /\ gh' = [gh EXCEPT !.resumed = @ + 1]
  /\ n' = [n EXCEPT !.resume = @ + 1]
  /\ UNCHANGED <<fs, pst, tgt, ses, q, runs, ctl, eng>>

\* §13: W1 forgotten and added again under a new registry key
UserForgetReadd ==
  /\ ses.app = "run" /\ 1 \notin ses.opened /\ ses.screen = "none" /\ n.forget < MaxForget
  /\ pst' = [pst EXCEPT !.reg1 = 4]
  /\ n' = [n EXCEPT !.forget = @ + 1]
  /\ UNCHANGED <<fs, jr, tgt, ses, q, runs, ctl, eng, gh>>

\* "Try again" when the engine is Unavailable (§5.3)
UserTryAgain ==
  /\ Alive /\ Unavail /\ CanAct
  /\ ses' = [ses EXCEPT !.stops = 0]
  /\ n' = Spent
  /\ UNCHANGED <<fs, jr, pst, tgt, q, runs, ctl, eng, gh>>

\* §9.3: quit (graceful) and update both start with the quiesce
UserQuit(kind) ==
  /\ DoQuit /\ ses.app = "run" /\ ses.screen = "none"
  /\ kind = "quit" \/ (kind = "update" /\ n.upd < MaxUpdate)
  /\ n' = IF kind = "update" THEN [n EXCEPT !.upd = @ + 1] ELSE n
  /\ Quiesce(OpenW, [ses EXCEPT !.app = kind, !.stage = "quiesce"], <<>>)
  /\ UNCHANGED <<fs, jr, pst, tgt>>

\* What the engine leaves behind when the app goes (quit, Quit anyway, app crash):
\* a process stuck in the kernel keeps its locks (an orphan, §5.2); a live one may linger (§15)
OrphanAfter(lingers) ==
  IF eng.p > 0 /\ eng.stuck
  THEN [pst EXCEPT !.orphan = @ \cup ses.locks, !.ostuck = TRUE]
  ELSE IF lingers /\ eng.p > 0 /\ ses.locks # {}
       THEN [pst EXCEPT !.orphan = @ \cup ses.locks]
       ELSE pst
DeadSesBy(how) == [ses EXCEPT !.app = "dead", !.endedBy = how, !.stage = "none", !.opened = {}, !.screen = "none",
                       !.advice = "none", !.locks = {}, !.mem = {}]

\* §9.3 step 3: "Quit anyway": always enabled, always ends the app
GQuitAnyway == Alive
UserQuitAnyway ==
  /\ GQuitAnyway
  /\ \E lingers \in (IF n.orph < MaxOrphan THEN BOOLEAN ELSE {FALSE}) :
     LET p1 == OrphanAfter(lingers)
         \* §9.3: a note naming the running save, create or copy, one file per job
         notes == IF Busy /\ RJ.k \in {"save","create","copy"}
                  THEN pst.notes \cup {[k |-> RJ.k, s |-> IF RJ.k = "save" THEN RJ.s ELSE "none"]}
                  ELSE pst.notes
         QS == {q[i].s : i \in {i \in 1..Len(q) : q[i].k = "save" /\ q[i].conf}}
     IN
     /\ pst' = [p1 EXCEPT !.notes = notes]
     /\ gh' = [gh EXCEPT !.told = [s \in SaveIds |-> IF s \in QS THEN "notsaved" ELSE gh.told[s]]]
     /\ n' = [n EXCEPT !.orph = @ + (IF lingers THEN 1 ELSE 0)]
  /\ ses' = DeadSesBy("quit") /\ q' = <<>> /\ runs' = {} /\ ctl' = NoCtl /\ eng' = NoEng
  /\ UNCHANGED <<fs, jr, tgt>>

\* the next start after the app ended
\* the next start; after a quit only in worlds that check what a quit leaves (RestartAfterQuit): a
\* bound on what follows the exit, never on the exit (Quit anyway) itself
Relaunch ==
  /\ ses.app = "dead" /\ n.start < MaxStarts /\ (ses.endedBy = "crash" \/ RestartAfterQuit)
  /\ ses' = [ses EXCEPT !.app = "boot", !.stage = "recover", !.opened = {}, !.from = 0,
                        !.target = pst.last, !.screen = "none", !.sctx = "none",
                        !.stops = 0, !.advice = "none", !.locks = {}, !.mem = {}]
  /\ q' = <<RecJob(NewId)>>
  /\ n' = [n EXCEPT !.start = @ + 1]
  /\ UNCHANGED <<fs, jr, pst, tgt, runs, ctl, eng, gh>>

----------------------------------------------------------------------------
(* APP ACTIONS (the sidecar)                                               *)

\* a background save of W1 (prices); at most one queued per workspace (§3)
AppBgSave ==
  /\ ses.app = "run" /\ 1 \in ses.opened /\ n.bg < MaxBg
  /\ ~QHas(LAMBDA j : j.k = "save" /\ ~j.wf)
  /\ LET s == SId(n.save + n.bg + 1) IN
     /\ q' = q \o <<Job(NewId, "save", 1, FALSE, FALSE, "app", s, pst.mirror[1].fp)>>
     /\ n' = [n EXCEPT !.bg = @ + 1]
     /\ gh' = [gh EXCEPT !.pre[s] = pst.mirror[1].fp]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, runs, ctl, eng>>

\* the focus check saw an outside change: queue a rebuild (ADR-0015)
AppFocusCheck(w) ==
  /\ ses.app = "run" /\ w \in ses.opened /\ n.rebuild < MaxRebuild
  /\ FP(w) # pst.mirror[w].fp
  /\ ~QHas(LAMBDA j : j.k = "rebuild" /\ j.w = w)
  /\ q' = q \o <<Job(NewId, "rebuild", w, TRUE, FALSE, "app", "none", ZeroFP)>>
  /\ n' = [n EXCEPT !.rebuild = @ + 1]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, runs, ctl, eng, gh>>

\* §9.1 step 4: A closes when none of its jobs is queued or running; then recover_all
CloseA ==
  /\ ses.app = "switch" /\ ses.stage = "quiesce"
  /\ ~QHas(LAMBDA j : j.w = ses.from) /\ ~(Busy /\ RJ.w = ses.from)
  /\ MUTANT = "switch_waits_for_r4" => ClosedJ(ses.from) = {}
  /\ ses' = [ses EXCEPT !.opened = {}, !.stage = "recover"]
  /\ q' = IF MUTANT = "recovery_while_open" THEN q ELSE q \o <<RecJob(NewId)>>
  /\ n' = n
  /\ UNCHANGED <<fs, jr, pst, tgt, runs, ctl, eng, gh>>

\* §9.3 step 4 (quit) and the update: after the quiesce
QuitClose ==
  /\ ses.app \in {"quit","update"} /\ q = <<>> /\ ~Busy
  /\ MUTANT = "quit_waits_reap" => eng.p = 0
  /\ IF ses.app = "update" /\ MUTANT # "installer_ignores_journal" /\ \E j \in JIds : jr[j].st \in OpenSt
     THEN \* an installer never runs while a .open journal exists (ADR-0012); the user is told to Reopen
          /\ ses' = [ses EXCEPT !.app = "run", !.stage = "none"]
          /\ UNCHANGED <<pst, eng>>
     ELSE /\ ses' = DeadSesBy("quit")
          /\ eng' = IF eng.p > 0 THEN NoEng ELSE eng   \* close() signals the group once, no wait
          /\ pst' = LET p1 == OrphanAfter(FALSE) IN
                    IF ses.app = "update" THEN [p1 EXCEPT !.build = @ + 1] ELSE p1
  /\ UNCHANGED <<fs, jr, tgt, q, runs, ctl, n, gh>>

OpenFailSes == IF ses.app = "boot"
               THEN [ses EXCEPT !.app = IF MUTANT = "boot_waits_for_success" THEN "boot" ELSE "run",
                                !.stage = IF MUTANT = "boot_waits_for_success" THEN "screen" ELSE "none",
                                !.screen = "failed", !.sctx = "boot", !.advice = "none"]
               ELSE [ses EXCEPT !.stage = "screen", !.screen = "failed",
                                !.sctx = IF ses.from = 0 THEN "boot" ELSE "switch", !.advice = "none"]
\* §9.1 step 6 / §9.2: assess the target after recover_all
OpenDone(t) ==
  /\ ses' = [ses EXCEPT !.app = "run", !.stage = "none", !.screen = "none", !.target = t,
                        !.opened = IF MUTANT = "open_before_close" THEN @ \cup {t} ELSE {t}]
  /\ pst' = [pst EXCEPT !.last = t]
  /\ q' = QueueR4(t)
  /\ n' = n
Assess ==
  /\ ses.app \in {"boot","switch"} /\ ses.stage = "assess" /\ ~Busy
  /\ ~QHas(LAMBDA j : j.own = "switch")
  /\ LET t == ses.target IN
     CASE t = 0 ->
            /\ ses' = [ses EXCEPT !.app = "run", !.stage = "none"]
            /\ UNCHANGED <<pst, q, n>>
       [] t = 3 /\ pst.unr ->     \* the assessment cannot read the target's files
            /\ ses' = OpenFailSes
            /\ UNCHANGED <<pst, q, n>>
       [] Blocked(t) /\ MUTANT = "boot_waits_for_success" /\ ses.app = "boot" ->
            \* seeded bug: the boot ends only when its open succeeds
            /\ ses' = [ses EXCEPT !.stage = "screen", !.screen = "blocked", !.sctx = "boot"]
            /\ UNCHANGED <<pst, q, n>>
       [] Blocked(t) ->
            /\ ses' = IF ses.app = "boot"
                      THEN [ses EXCEPT !.app = "run", !.stage = "none", !.screen = "blocked", !.sctx = "boot"]
                      ELSE [ses EXCEPT !.stage = "screen", !.screen = "blocked",
                                       !.sctx = IF ses.from = 0 THEN "boot" ELSE "switch"]
            /\ UNCHANGED <<pst, q, n>>
       [] pst.mirror[t].fp = FP(t) -> OpenDone(t)
       [] OTHER ->
            /\ q' = q \o <<Job(NewId, "open", t, TRUE, FALSE, "switch", "none", ZeroFP)>>
            /\ n' = n
            /\ ses' = [ses EXCEPT !.stage = "opening"]
            /\ UNCHANGED pst
  /\ UNCHANGED <<fs, jr, tgt, runs, ctl, eng, gh>>

\* §5.2/§5.4: after the display delay, the app shows the quit advice while the slot is held by a
\* process we signalled that has not ended, whether or not a job waits (N3): in the waiting job's
\* place, or as a notice
WaitingQ == \E i \in 1..Len(q) : q[i].wf /\ q[i].req = "present"
AdviceTimer ==
  /\ Alive /\ ses.advice = "none"
  /\ \/ /\ eng.st = "sig"
        \* seeded bug (the rule before N3): the advice only while a job waits
        /\ MUTANT = "advice_only_to_waiting" => (WaitingQ \/ (Busy /\ ctl.pc \in SpawnPcs))
     \/ /\ MUTANT = "advice_on_timer" /\ Busy /\ eng.st = "busy" /\ WaitingQ
        /\ ~Endable(Ended(RJ))
  /\ ses' = [ses EXCEPT !.advice = "timer"]
  /\ UNCHANGED <<fs, jr, pst, tgt, q, runs, ctl, eng, n, gh>>

\* the OS ends a signalled process that is not stuck; drain then reap (§5.2)
Drain ==
  /\ eng.st = "sig" /\ ~eng.stuck
  /\ eng' = [NoEng EXCEPT !.p = eng.p - 1]
  /\ ses' = [ses EXCEPT !.locks = {}, !.advice = "none"]
  /\ UNCHANGED <<fs, jr, pst, tgt, q, runs, ctl, n, gh>>

OrphanEnd ==
  /\ pst.orphan # {} /\ ~pst.ostuck
  /\ pst' = [pst EXCEPT !.orphan = {}]
  /\ UNCHANGED <<fs, jr, tgt, ses, q, runs, ctl, eng, n, gh>>
\* a process stuck in the kernel ends only when the disk answers (not fair)
OrphanEndStuck ==
  /\ pst.orphan # {} /\ pst.ostuck
  /\ pst' = [pst EXCEPT !.orphan = {}, !.ostuck = FALSE]
  /\ UNCHANGED <<fs, jr, tgt, ses, q, runs, ctl, eng, n, gh>>

WindowExpire ==
  /\ Alive /\ ses.stops > 0 /\ n.exp < MaxExpire
  /\ ses' = [ses EXCEPT !.stops = @ - 1]
  /\ n' = [n EXCEPT !.exp = @ + 1]
  /\ UNCHANGED <<fs, jr, pst, tgt, q, runs, ctl, eng, gh>>

----------------------------------------------------------------------------
(* DISPATCH (§3)                                                           *)
FirstPc(k) == CASE k = "save" -> "s_r4chk" [] k = "r4" -> "r4_a" [] k \in Reads -> "rd_load"
               [] k = "recover" -> "rc_init" [] k = "open" -> "op_load"
               [] k = "create" -> "cr_tgt" [] k = "copy" -> "cp_chk"

Dispatch ==
  /\ Alive /\ ses.app # "dead" /\ EligIdx # {}
  /\ ~Busy \/ MUTANT = "dispatch_while_running"
  /\ SlotFree
  /\ ~Unavail
  /\ LET i == PickIdx  j == q[i] IN
     /\ q' = RemoveAt(q, i)
     /\ runs' = runs \cup {j}
     /\ ctl' = [NoCtl EXCEPT !.pc = FirstPc(j.k), !.s = j.s, !.plan = j.plan,
                             !.r4ctx = IF j.k = "r4" THEN "job" ELSE "none"]
     /\ eng' = Fresh
     /\ ses' = [ses EXCEPT !.advice = "none"]
     /\ gh' = [gh EXCEPT !.edLoad = FALSE]
  /\ UNCHANGED <<fs, jr, pst, tgt, n>>

\* when the engine is Unavailable, a job is refused (ENGINE_UNAVAILABLE); recover_all marks
\* every journal not yet tried as blocking (not_tried, §8.1)
DispatchUnavail ==
  /\ Alive /\ ~Busy /\ EligIdx # {} /\ Unavail
  /\ LET i == PickIdx  j == q[i] IN
     /\ q' = RemoveAt(q, i)
     /\ CASE j.k = "recover" ->
               /\ jr' = [x \in JIds |-> IF jr[x].st \in OpenSt /\ MUTANT # "untried_unmarked"
                                        THEN [jr[x] EXCEPT !.why = "not_tried", !.old = TRUE]
                                        ELSE IF jr[x].st \in OpenSt THEN [jr[x] EXCEPT !.why = "", !.old = TRUE]
                                        ELSE jr[x]]
               /\ ses' = [ses EXCEPT !.stage = "assess"]
               /\ UNCHANGED <<gh, pst>>
          [] j.k = "save" ->
               /\ gh' = SetTold(gh, j.s, "notsaved") /\ UNCHANGED <<jr, ses, pst>>
          [] j.k = "open" ->
               /\ ses' = IF ses.app = "boot"
                         THEN [ses EXCEPT !.app = "run", !.stage = "none", !.screen = "failed", !.sctx = "boot"]
                         ELSE [ses EXCEPT !.stage = "screen", !.screen = "failed",
                                          !.sctx = IF ses.from = 0 THEN "boot" ELSE "switch"]
               /\ UNCHANGED <<gh, jr, pst>>
          [] j.k = "create" ->
               /\ gh' = [gh EXCEPT !.crOut = "unavailable", !.crMsg = "not_created"]
               /\ UNCHANGED <<jr, ses, pst>>
          [] OTHER -> UNCHANGED <<gh, jr, ses, pst>>
  /\ UNCHANGED <<fs, tgt, runs, ctl, eng, n>>

\* a job step that needs a fresh engine after one that ran user code (§5.1, §6.1, §6.7, §8.1)
SpawnStep ==
  /\ Alive /\ Busy /\ ctl.pc \in SpawnPcs
  /\ eng.p = 0 \/ (MUTANT = "spawn_before_reap" /\ eng.st = "sig") \/ (eng.st = "idle" /\ ~eng.user)
  /\ IF Unavail
     THEN CASE ctl.pc = "rc_spawn" ->
                 /\ RecoverEndEffects(eng,
                      [x \in JIds |-> IF jr[x].st \in OpenSt /\ x \notin ctl.tried /\ MUTANT # "untried_unmarked"
                                      THEN [jr[x] EXCEPT !.why = "not_tried"] ELSE jr[x]])
            [] OTHER -> \* the save (or its R4) could not get an engine: refused, nothing written
                 /\ gh' = IF RJ.k = "save" THEN SetTold(gh, ctl.s, "notsaved") ELSE gh
                 /\ JobEnd(eng)
                 /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, n>>
     ELSE /\ eng' = Fresh
          /\ ctl' = [ctl EXCEPT !.hung = FALSE,   \* a hang belonged to the engine that ended
                                !.pc = CASE ctl.pc = "s_spawn" -> "s_locks"
                                         [] ctl.pc = "r4_spawn" -> "r4_conf"
                                         [] ctl.rinit -> "rc_next"
                                         [] OTHER -> "rc_init"]
          /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, n, gh>>

FreshEnd ==   \* the engine that ran user code ends before the next part of the job
  /\ Busy /\ ctl.pc \in {"s_fresh","r4_fresh"} /\ EngOK
  /\ eng' = [eng EXCEPT !.st = "sig", !.reason = "retire"]
  /\ ctl' = [ctl EXCEPT !.pc = IF ctl.pc = "s_fresh" THEN "s_spawn" ELSE "r4_spawn"]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, n, gh>>

----------------------------------------------------------------------------
(* THE SAVE (§6.1)                                                         *)
SV  == ctl.s
PV  == PostV(ctl.s)
Pre(f) == ctl.plan[f]
PostFP == [f \in Files |-> IF f \in Tg THEN PV ELSE ctl.plan[f]]
CurF == WOrd[ctl.fi]

\* the save's rollback starts (§6.4); §6.4 (F2): first the temp rule of recovery's step 2
StartRB(msg, skip) == [ctl EXCEPT !.pc = IF MUTANT # "rollback_skips_temp_rule" THEN "rb_chk" ELSE "rs_w",
                                  !.rj = ctl.s, !.fi = 1, !.rbt = msg, !.skip = skip]

NextS(p) ==
  CASE p = "s_locks" -> "s_gate"
    [] p = "s_gate" -> "s_load"
    [] p = "s_load" -> IF MUTANT = "no_recheck" THEN (IF MUTANT = "gate_after_journal" THEN "s_bk" ELSE "s_commit")
                       ELSE "s_recheck"
    [] p = "s_recheck" -> IF MUTANT = "gate_after_journal" THEN "s_bk" ELSE "s_commit"
    [] p = "s_commit" -> IF MUTANT = "gate_after_journal" THEN "s_tw1" ELSE "s_bk"
    [] p = "s_bk" -> IF MUTANT = "no_backup_sync" THEN "s_jp" ELSE "s_bks"
    [] p = "s_bks" -> "s_jp"
    [] p = "s_jp" -> IF MUTANT = "no_journal_sync" THEN "s_jo" ELSE "s_jps"
    [] p = "s_jps" -> IF MUTANT = "direct_open" THEN "s_jds" ELSE "s_jo"
    [] p = "s_jo" -> IF MUTANT = "no_journal_sync" THEN "s_tw1" ELSE "s_jds"
    [] p = "s_jds" -> IF MUTANT = "gate_after_journal" THEN "s_commit" ELSE "s_tw1"
    [] p = "s_tw1" -> "s_tw2"
    [] p = "s_tw2" -> IF MUTANT = "no_temp_fsync" THEN "s_sw" ELSE "s_ts"
    [] p = "s_ts" -> "s_sw"
    [] p = "s_sw" -> "s_cmp"
    [] p = "s_cmp" -> "s_ds"
    [] p = "s_rbk" -> IF MUTANT = "temps_after_close" THEN "s_close" ELSE "s_rmt"
    [] p = "s_rmt" -> IF MUTANT = "temps_after_close" THEN "s_saved" ELSE "s_close"
    [] p = "s_close" -> IF MUTANT = "report_before_close_sync" THEN "s_saved" ELSE "s_csync"
    [] p = "s_csync" -> CASE MUTANT = "temps_after_close" -> "s_rmt"
                          [] MUTANT = "report_before_close_sync" -> "s_pub"
                          [] OTHER -> "s_saved"
    [] p = "s_saved" -> IF MUTANT = "report_before_close_sync" THEN "s_csync" ELSE "s_pub"
    [] p = "s_pub" -> "s_rel"
Adv(p) == [ctl EXCEPT !.pc = NextS(p)]

\* a refusal before anything is written: locks released, the job ends (§6.1 steps 1-6)
SaveRefuse(msg) ==
  /\ gh' = SetTold(gh, SV, msg)
  /\ JobEnd(Retired)
  /\ ses' = [ses EXCEPT !.locks = {}, !.advice = "none"]
  /\ UNCHANGED <<fs, jr, pst, tgt, q, n>>

\* step 1: R4 first, if owed (the closed record re-read from disk)
S_R4Check ==
  /\ Step("s_r4chk")
  /\ IF GateClosed(1) # {}
     THEN IF MUTANT = "save_waits_r4_forever"
          THEN ctl' = [ctl EXCEPT !.pc = "s_waitr4"]
          ELSE ctl' = [ctl EXCEPT !.pc = "r4_a", !.r4ctx = "save",
                                  !.r4j = CHOOSE j \in GateClosed(1) : TRUE]
     ELSE ctl' = [ctl EXCEPT !.pc = "s_locks"]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>
\* seeded bug only: the save waits for the queued background r4 instead of running it
S_WaitR4 ==
  /\ Step("s_waitr4") /\ ClosedJ(1) = {}
  /\ ctl' = [ctl EXCEPT !.pc = "s_locks"]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>

\* step 3: ledger-set locks, waiting while an orphaned engine holds one
S_Locks ==
  /\ Step("s_locks") /\ LS(1) \cap pst.orphan = {}
  /\ ses' = [ses EXCEPT !.locks = LS(1)]
  /\ ctl' = Adv("s_locks")
  /\ UNCHANGED <<fs, jr, pst, tgt, q, runs, eng, n, gh>>

\* step 4: the gate, under the locks, every record re-read from disk
S_Gate ==
  /\ Step("s_gate")
  /\ IF \/ FP(1) # ctl.plan                 \* LEDGER_EXTERNALLY_CHANGED
        \/ pst.mirror[1].fp # ctl.plan      \* WORKSPACE_NOT_READY {WARMING}
        \/ OpenJ(1) # {}                    \* WRITES_PAUSED {unfinished_save}
        \/ GateClosed(1) # {}               \* R4 owed
        \/ GateMism(1) # {}                 \* WRITES_PAUSED {check_failed}
     THEN SaveRefuse("notsaved")
     ELSE /\ ctl' = Adv("s_gate")
          /\ gh' = [gh EXCEPT !.edSeen = [f \in Files |-> IF f \in LS(1) THEN TRUE ELSE gh.edSeen[f]],
                              !.edLoad = FALSE]
          /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n>>

\* step 5: the commit's one full load (user code)
S_Load ==
  /\ Step("s_load")
  /\ eng' = [eng EXCEPT !.user = TRUE]
  /\ ctl' = Adv("s_load")
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, n, gh>>

\* step 6: the full fingerprint again, under the locks
S_Recheck ==
  /\ Step("s_recheck")
  /\ IF FP(1) # ctl.plan THEN SaveRefuse("notsaved")
     ELSE /\ ctl' = Adv("s_recheck")
          /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>

\* step 7: the commit point, decided in one table step (§4): a Cancel arriving before this step
\* has already ended the engine; one after it is "too late"
S_Commit ==
  /\ Step("s_commit")
  /\ ctl' = [Adv("s_commit") EXCEPT !.past = TRUE]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>

\* step 8.1: backups written, then synced with their directory (R only: N did not exist)
S_Backup ==
  /\ Step("s_bk")
  /\ fs' = [fs EXCEPT !.bk[SV] = Pre("R"), !.bkpl[SV] = {ABS, TORN, Pre("R")}]
  /\ ctl' = Adv("s_bk")
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n, gh>>
S_BackupSync ==
  /\ Step("s_bks")
  /\ fs' = [fs EXCEPT !.bkpl[SV] = {Pre("R")}]
  /\ ctl' = Adv("s_bks")
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n, gh>>

\* step 8.2: the journal as .part, synced, renamed .open, directory synced
NewJ == [NoJ EXCEPT !.lin = 1, !.key = pst.reg1, !.files = Tg,
           !.pre = [f \in Files |-> IF f \in Tg THEN ctl.plan[f] ELSE 0],
           !.post = [f \in Files |-> IF f \in Tg THEN PV ELSE 0],
           !.pfp = PostFP, !.old = FALSE, !.build = pst.build]
S_JPart ==
  /\ Step("s_jp")
  /\ jr' = [jr EXCEPT ![SV] = IF MUTANT = "direct_open"
                              THEN [NewJ EXCEPT !.st = "open", !.pl = {"none","topen","open"}]
                              ELSE [NewJ EXCEPT !.st = "part", !.pl = {"none","tpart","part"}]]
  /\ ctl' = Adv("s_jp")
  /\ UNCHANGED <<fs, pst, tgt, ses, q, runs, eng, n, gh>>
S_JPartSync ==
  /\ Step("s_jps")
  /\ jr' = [jr EXCEPT ![SV].pl = IF MUTANT = "direct_open" THEN {"none","open"} ELSE {"none","part"}]
  /\ ctl' = Adv("s_jps")
  /\ UNCHANGED <<fs, pst, tgt, ses, q, runs, eng, n, gh>>
S_JOpen ==
  /\ Step("s_jo")
  /\ jr' = [jr EXCEPT ![SV].st = "open", ![SV].pl = @ \cup {"open"}]
  /\ ctl' = Adv("s_jo")
  /\ UNCHANGED <<fs, pst, tgt, ses, q, runs, eng, n, gh>>
S_JDirSync ==
  /\ Step("s_jds")
  /\ jr' = [jr EXCEPT ![SV].pl = {jr[SV].st}]
  /\ ctl' = Adv("s_jds")
  /\ UNCHANGED <<fs, pst, tgt, ses, q, runs, eng, n, gh>>

\* step 8.3, per file, children first and the root last
TS == <<SV, CurF>>
S_TempStart ==     \* the temp beside the file is being written: cut off here, it is torn
  /\ Step("s_tw1")
  /\ fs' = [fs EXCEPT !.tc[TS] = TORN, !.tpl[TS] = {ABS, TORN}]
  /\ ctl' = Adv("s_tw1")
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n, gh>>
S_TempDone ==
  /\ Step("s_tw2")
  /\ fs' = [fs EXCEPT !.tc[TS] = PV, !.tpl[TS] = {ABS, TORN, PV}]
  /\ ctl' = Adv("s_tw2")
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n, gh>>
S_TempSync ==
  /\ Step("s_ts")
  /\ fs' = [fs EXCEPT !.tpl[TS] = {ABS, PV}]
  /\ ctl' = Adv("s_ts")
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n, gh>>

\* An exchange or rename in the ledger folder is atomic, and the folder's metadata operations reach
\* the disk in order: until the folder is synced, a power loss leaves the file and its temp as a
\* pair from before or after each pending operation (cp: every possible pair; cpn: the pairs in
\* which the latest operation persisted). Without a pending operation they are independent.
\* a coupling left by another slot (an earlier save) is dissolved into independent outcomes
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

\* a replace by atomic swap (R), or an exclusive create (N) that fails if a file appeared
S_Swap ==
  /\ Step("s_sw")
  /\ LET f == CurF  t == TS IN
     IF Pre(f) # ABS
     THEN /\ LET P == Pairs(f, t)
                  New == {<<a, b>> : a \in Seconds(P) \ {ABS}, b \in Firsts(P) \ {ABS}}
                  f1 == Couple(f, t, P, New)
              IN fs' = [f1 EXCEPT !.cur[f] = fs.tc[t], !.tc[t] = fs.cur[f]]
          /\ ctl' = Adv("s_sw")
          /\ UNCHANGED gh
     ELSE IF fs.cur[f] # ABS /\ MUTANT # "plain_create"
          THEN \* a file appeared: the create fails; roll back, "your editor saved this file..."
               /\ ctl' = StartRB("changed", f)
               /\ UNCHANGED <<fs, gh>>
          ELSE /\ LET P == Pairs(f, t)
                       New == {<<a, ABS>> : a \in Seconds(P) \ {ABS}}
                       f1 == Couple(f, t, P, New)
                   IN fs' = [f1 EXCEPT !.cur[f] = fs.tc[t], !.tc[t] = ABS]
               /\ ctl' = [Adv("s_sw") EXCEPT !.pc = "s_ds"]
               /\ UNCHANGED gh
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n>>

\* §6.4: every swap hashes what it displaced; other bytes are swapped back and kept
S_Compare ==
  /\ Step("s_cmp")
  /\ LET f == CurF  t == TS IN
     IF fs.tc[t] = Pre(f) \/ MUTANT = "no_displaced_check"
     THEN /\ ctl' = Adv("s_cmp") /\ UNCHANGED fs
     ELSE /\ LET New == {<<p[2], p[1]>> : p \in fs.cpn[f]}
                  f1 == Couple(f, t, Pairs(f, t), New)
                  f2 == [f1 EXCEPT !.cur[f] = fs.tc[t], !.tc[t] = fs.cur[f]]
              \* §6.4 (F3): the swap-back is synced at once: the pair is durable as it now stands
              IN fs' = IF MUTANT = "swap_back_unsynced" THEN f2
                       ELSE [f2 EXCEPT !.pl[f] = {fs.tc[t]}, !.tpl[t] = {fs.cur[f]},
                                       !.cp[f] = {}, !.cpn[f] = {}, !.cpj[f] = "none"]
          /\ ctl' = StartRB("changed", f)
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n, gh>>

S_DirSync ==
  /\ Step("s_ds")
  /\ LET f == CurF  t == TS IN
     /\ fs' = [fs EXCEPT !.pl[f] = IF fs.cp[f] = {} THEN @ ELSE Firsts(fs.cpn[f]),
                         !.tpl[t] = IF fs.cp[f] = {} THEN @ ELSE Seconds(fs.cpn[f]),
                         !.cp[f] = {}, !.cpn[f] = {}, !.cpj[f] = "none"]
     /\ ctl' = IF ctl.fi < Len(WOrd) THEN [ctl EXCEPT !.fi = @ + 1, !.pc = "s_tw1"]
               ELSE [ctl EXCEPT !.pc = "s_rbk"]
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n, gh>>

\* step 8.4: read-back, then our temps removed and their directories synced
S_ReadBack ==
  /\ Step("s_rbk")
  /\ IF \A f \in Tg : fs.cur[f] = PV
     THEN ctl' = Adv("s_rbk")
     ELSE ctl' = StartRB("changed", "none")
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>
S_RemoveTemps ==
  /\ Step("s_rmt")
  /\ fs' = [fs EXCEPT !.tc = [x \in TSlots |-> IF x[1] = SV THEN ABS ELSE fs.tc[x]],
                      !.tpl = [x \in TSlots |-> IF x[1] = SV THEN {ABS} ELSE fs.tpl[x]]]
  /\ ctl' = Adv("s_rmt")
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n, gh>>

\* step 8.5: the journal close; the save has landed exactly when the closed record is durable
S_Close ==
  /\ Step("s_close")
  /\ jr' = [jr EXCEPT ![SV].st = "closed", ![SV].pl = {"open","closed"}]
  /\ ses' = IF MUTANT = "gate_in_memory" THEN [ses EXCEPT !.mem = @ \cup {SV}] ELSE ses
  /\ ctl' = Adv("s_close")
  /\ gh' = IF MUTANT = "report_before_close_sync" THEN SetTold(gh, SV, "saved") ELSE gh
  /\ UNCHANGED <<fs, pst, tgt, q, runs, eng, n>>
S_CloseSync ==
  /\ Step("s_csync")
  /\ jr' = [jr EXCEPT ![SV].pl = {jr[SV].st}]
  /\ ctl' = Adv("s_csync")
  /\ UNCHANGED <<fs, pst, tgt, ses, q, runs, eng, n, gh>>

\* step 9: "saved"
S_Saved ==
  /\ Step("s_saved")
  /\ gh' = SetTold(gh, SV, "saved")
  /\ ctl' = Adv("s_saved")
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n>>

\* step 10: publish the overlay's result if the disk equals the post fingerprint and no .open
\* journal exists; otherwise queue a rebuild
S_Publish ==
  /\ Step("s_pub")
  /\ IF FP(1) = PostFP /\ OpenJ(1) = {}
     THEN /\ pst' = [pst EXCEPT !.mirror[1] = [fp |-> FP(1), src |-> "overlay", build |-> pst.build]]
          /\ UNCHANGED <<q, n>>
     ELSE /\ q' = IF ses.app = "run" THEN q \o <<Job(NewId, "rebuild", 1, TRUE, FALSE, "app", "none", ZeroFP)>> ELSE q
          /\ n' = n
          /\ UNCHANGED pst
  /\ ctl' = Adv("s_pub")
  /\ UNCHANGED <<fs, jr, tgt, ses, runs, eng, gh>>

\* step 11: release the locks, final reply, retire, queue the r4 as background work
S_Release ==
  /\ Step("s_rel")
  /\ JobEnd(Retired)
  /\ ses' = [ses EXCEPT !.locks = {}, !.advice = "none"]
  \* while A is being closed (switch, quit, update) a new job naming A is refused (§9.1 step 2):
  \* the closed record stays on disk for the next open
  /\ q' = IF ses.app = "run" THEN QueueR4(1) ELSE q
  /\ n' = n
  /\ UNCHANGED <<fs, jr, pst, tgt, gh>>

----------------------------------------------------------------------------
(* RESTORE: the save's rollback (§6.4) and recovery's step 3 (§8.1)        *)
RJn  == ctl.rj
RF   == ROrd(RJn)[ctl.fi]
JPre(f)  == jr[RJn].pre[f]
JPost(f) == jr[RJn].post[f]
InRollback == RJ.k = "save"
RestNext == IF ctl.fi < Len(ROrd(RJn)) THEN [ctl EXCEPT !.fi = @ + 1, !.pc = "rs_w"]
            ELSE [ctl EXCEPT !.pc = "rs_tmp"]
\* the path guard (§6.3): a recorded path that no longer resolves to its canonical path
PathMoved(f) == pst.lnk /\ f = "R"

\* the journal stops: the rollback leaves it .open (unfinished); recovery marks it blocking
RestoreStop(why) ==
  IF InRollback
  THEN /\ gh' = SetTold(gh, SV, "unfinished")
       /\ JobEnd(Retired)
       /\ ses' = [ses EXCEPT !.locks = {}, !.advice = "none"]
       /\ UNCHANGED jr
  ELSE /\ jr' = [jr EXCEPT ![RJn].why = why]
       /\ ctl' = [ctl EXCEPT !.pc = "rc_next", !.tried = @ \cup {RJn}, !.rj = "none"]
       /\ ses' = [ses EXCEPT !.locks = {}]
       /\ UNCHANGED <<runs, eng, gh>>

\* §6.4 (F2): before it deletes the save's temps, the rollback applies recovery's temp rule: a temp
\* beside a file that holds the post bytes, holding other bytes, is a displaced outside save
RB_Check ==
  /\ Step("rb_chk")
  /\ IF \E f \in Tg : f # ctl.skip /\ fs.cur[f] = PV /\ fs.tc[<<SV, f>>] \notin {ABS, Pre(f), PV}
     THEN /\ RestoreStop("stopped") /\ UNCHANGED <<fs, pst, tgt, q, n>>
     ELSE /\ ctl' = [ctl EXCEPT !.pc = "rs_w"]
          /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>

RS_Write ==
  /\ Step("rs_w")
  /\ LET f == RF IN
     IF fs.cur[f] = JPre(f) \/ f = ctl.skip
     THEN /\ ctl' = RestNext /\ UNCHANGED fs          \* already at pre (idempotent), or the outside save put back
     ELSE /\ fs' = IF JPre(f) # ABS THEN [fs EXCEPT !.rt[f] = JPre(f)] ELSE fs
          /\ ctl' = [ctl EXCEPT !.pc = "rs_x"]
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n, gh>>

RS_Swap ==
  /\ Step("rs_x")
  /\ LET f == RF IN
     IF PathMoved(f)
     THEN \* reached only without the guard: the write goes through the link to a file no journal names
          /\ pst' = [pst EXCEPT !.outside = JPre(f)]
          /\ gh' = [gh EXCEPT !.outW = TRUE]
          /\ fs' = [fs EXCEPT !.rt[f] = ABS]
          /\ ctl' = RestNext
     ELSE /\ LET nv == IF JPre(f) # ABS THEN fs.rt[f] ELSE ABS
                  cp1 == IF fs.cp[f] # {} THEN fs.cp[f] \cup {<<nv, y>> : y \in Seconds(fs.cp[f])} ELSE {}
              IN fs' = [fs EXCEPT !.cur[f] = nv, !.rt[f] = fs.cur[f], !.pl[f] = @ \cup {nv},
                                  !.cp[f] = cp1]
          /\ ctl' = [ctl EXCEPT !.pc = "rs_c"]
          /\ UNCHANGED <<pst, gh>>
  /\ UNCHANGED <<jr, tgt, ses, q, runs, eng, n>>

RS_Compare ==
  /\ Step("rs_c")
  /\ LET f == RF IN
     IF fs.rt[f] = JPost(f) \/ MUTANT = "no_displaced_check"
     THEN /\ fs' = [fs EXCEPT !.rt[f] = ABS]
          /\ ctl' = RestNext
          /\ UNCHANGED <<jr, ses, runs, eng, gh>>
     ELSE \* other bytes: put back and kept; the journal stops (both versions shown)
          /\ LET cp1 == IF fs.cp[f] # {} THEN fs.cp[f] \cup {<<fs.rt[f], y>> : y \in Seconds(fs.cp[f])} ELSE {}
             IN fs' = [fs EXCEPT !.cur[f] = fs.rt[f], !.rt[f] = ABS,
                                 \* §6.4 (F3): the swap-back is synced at once
                                 !.pl[f] = IF MUTANT # "swap_back_unsynced" THEN {fs.rt[f]} ELSE @ \cup {fs.rt[f]},
                                 !.cp[f] = IF MUTANT # "swap_back_unsynced" THEN {} ELSE cp1]
          /\ RestoreStop("stopped")
  /\ UNCHANGED <<pst, tgt, q, n>>

\* the save's temps and our restore temps are deleted, whatever they hold
RS_Temps ==
  /\ Step("rs_tmp")
  /\ LET F == jr[RJn].files IN
     /\ fs' = [fs EXCEPT !.tc = [x \in TSlots |-> IF x[1] = RJn THEN ABS ELSE fs.tc[x]],
                         !.rt = [f \in Files |-> IF f \in F THEN ABS ELSE fs.rt[f]]]
     \* §15: an outside write displaced into a restore temp by a swap that a crash cut before its
     \* compare is lost (an honest limit); recorded so ExternalEditNeverSilentlyLost excludes it
     /\ gh' = [gh EXCEPT !.acc = [f \in Files |-> IF f \in F /\ fs.rt[f] # ABS
                                                 THEN gh.acc[f] \cup {fs.rt[f]} ELSE gh.acc[f]]]
  /\ ctl' = [ctl EXCEPT !.pc = IF MUTANT = "resolve_before_restores_synced" THEN "rs_res" ELSE "rs_sync"]
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n>>

\* only then: the journal renamed .resolved {not_saved}, its directory synced
RestoreDone ==
  IF InRollback
  THEN /\ gh' = SetTold(gh, SV, ctl.rbt)
       /\ JobEnd(Retired)
       /\ ses' = [ses EXCEPT !.locks = {}, !.advice = "none"]
       /\ UNCHANGED <<jr, pst, tgt, q, n>>
  ELSE /\ gh' = IF RJn \in SaveIds THEN SetTold(gh, RJn, "undone") ELSE gh
       /\ ctl' = [ctl EXCEPT !.pc = "rc_next", !.tried = @ \cup {RJn}, !.rj = "none"]
       /\ ses' = [ses EXCEPT !.locks = {}]
       /\ UNCHANGED <<jr, pst, tgt, q, runs, eng, n>>
RS_Sync ==
  /\ Step("rs_sync")
  /\ LET F == jr[RJn].files IN
     fs' = [fs EXCEPT !.pl = [f \in Files |-> IF f \in F THEN {fs.cur[f]} ELSE fs.pl[f]],
                      !.tpl = [x \in TSlots |-> IF x[1] = RJn THEN {ABS} ELSE fs.tpl[x]],
                      !.cp = [f \in Files |-> IF f \in F THEN {} ELSE fs.cp[f]],
                      !.cpn = [f \in Files |-> IF f \in F THEN {} ELSE fs.cpn[f]],
                      !.cpj = [f \in Files |-> IF f \in F THEN "none" ELSE fs.cpj[f]]]
  /\ IF MUTANT = "resolve_before_restores_synced"
     THEN RestoreDone
     ELSE /\ ctl' = [ctl EXCEPT !.pc = "rs_res"]
          /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, eng, n, gh>>

RS_Resolve ==
  /\ Step("rs_res")
  /\ jr' = [jr EXCEPT ![RJn].st = "resolved", ![RJn].pl = {"resolved"}]
  /\ IF MUTANT = "resolve_before_restores_synced"
     THEN /\ ctl' = [ctl EXCEPT !.pc = "rs_sync"]
          /\ UNCHANGED <<fs, pst, tgt, ses, q, runs, eng, n, gh>>
     ELSE /\ UNCHANGED fs
          /\ IF InRollback
             THEN /\ gh' = SetTold(gh, SV, ctl.rbt)
                  /\ JobEnd(Retired)
                  /\ ses' = [ses EXCEPT !.locks = {}, !.advice = "none"]
                  /\ UNCHANGED <<pst, tgt, q, n>>
             ELSE /\ gh' = IF RJn \in SaveIds THEN SetTold(gh, RJn, "undone") ELSE gh
                  /\ ctl' = [ctl EXCEPT !.pc = "rc_next", !.tried = @ \cup {RJn}, !.rj = "none"]
                  /\ ses' = [ses EXCEPT !.locks = {}]
                  /\ UNCHANGED <<pst, tgt, q, runs, eng, n>>

----------------------------------------------------------------------------
(* RECOVER_ALL (§8.1)                                                      *)
\* start: every journal now existing predates this recover_all (old); earlier judgements cleared; .part journals deleted (they never block)
RC_Init ==
  /\ Step("rc_init")
  /\ jr' = [j \in JIds |-> CASE jr[j].st \in {"part","tpart"} -> NoJ
                              [] jr[j].st \in OpenSt -> [jr[j] EXCEPT !.why = "", !.pl = {jr[j].st}, !.old = TRUE]
                              \* §6.6, §8.1 (F1): recover-all syncs the journal directories it lists, so the
                              \* quit note and the open read only durable records
                              [] MUTANT = "cnc_auto_lift" /\ jr[j].st = "mismatch" /\ jr[j].cnc ->
                                   [jr[j] EXCEPT !.st = "closed", !.pl = {"closed"}, !.att = 0]
                              [] MUTANT # "outcome_unsynced" /\ jr[j].st \in CL ->
                                   [jr[j] EXCEPT !.pl = {jr[j].st}]
                              [] OTHER -> jr[j]]
  /\ ctl' = [ctl EXCEPT !.pc = "rc_next", !.tried = {}, !.rinit = TRUE]
  /\ UNCHANGED <<fs, pst, tgt, ses, q, runs, eng, n, gh>>

Untried == RecUntried
RC_Next ==
  /\ Step("rc_next")
  /\ IF Untried = {}
     THEN RecoverEndEffects(Retired, jr)
     ELSE LET j == FirstJ(Untried) IN
          IF jr[j].st = "topen"
          THEN \* a journal whose file list cannot be read blocks every open
               /\ jr' = [jr EXCEPT ![j].why = "stopped"]
               /\ ctl' = [ctl EXCEPT !.tried = @ \cup {j}]
               /\ UNCHANGED <<fs, pst, tgt, ses, q, runs, eng, n, gh>>
          ELSE /\ ctl' = [ctl EXCEPT !.rj = j, !.pc = "rc_lock"]
               /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>

\* step 1: the locks, without waiting; held means a live writer of an earlier session
RC_Lock ==
  /\ Step("rc_lock")
  /\ IF jr[RJn].files \cap pst.orphan # {}
     THEN /\ jr' = [jr EXCEPT ![RJn].why = "in_use"]
          /\ ctl' = [ctl EXCEPT !.pc = "rc_next", !.tried = @ \cup {RJn}, !.rj = "none"]
          /\ UNCHANGED ses
     ELSE /\ ses' = [ses EXCEPT !.locks = jr[RJn].files]
          /\ ctl' = [ctl EXCEPT !.pc = "rc_chk"]
          /\ UNCHANGED jr
  /\ UNCHANGED <<fs, pst, tgt, q, runs, eng, n, gh>>

\* step 2: check every file it names
TempBad(j, f) ==
  LET t == fs.tc[<<j, f>>] IN
  CASE MUTANT = "no_temp_check" -> FALSE
    [] MUTANT = "old_temp_rule" -> t \notin {ABS, jr[j].pre[f], jr[j].post[f]}
    [] OTHER -> fs.cur[f] = jr[j].post[f] /\ t \notin {ABS, jr[j].pre[f], jr[j].post[f]}
BackupBad(j, f) == j \in SaveIds /\ f = "R" /\ fs.cur[f] = jr[j].post[f] /\ fs.bk[j] # jr[j].pre[f]
FileBad(j, f) ==
  \/ PathMoved(f) /\ MUTANT \notin {"no_path_guard", "fb_no_guard"}
  \/ pst.unr /\ f = "K"
  \/ fs.cur[f] \notin {jr[j].pre[f], jr[j].post[f]}
  \/ TempBad(j, f)
  \/ BackupBad(j, f)
  \/ MUTANT = "block_without_cause" /\ fs.cur[f] = jr[j].post[f] /\ fs.tc[<<j, f>>] = jr[j].pre[f]
RC_Check ==
  /\ Step("rc_chk")
  /\ IF \E f \in jr[RJn].files : FileBad(RJn, f)
     THEN /\ jr' = [jr EXCEPT ![RJn].why = "stopped"]
          /\ ctl' = [ctl EXCEPT !.pc = "rc_next", !.tried = @ \cup {RJn}, !.rj = "none"]
          /\ ses' = [ses EXCEPT !.locks = {}]
     ELSE /\ ctl' = [ctl EXCEPT !.pc = "rs_w", !.fi = 1]
          /\ UNCHANGED <<jr, ses>>
  /\ UNCHANGED <<fs, pst, tgt, q, runs, eng, n, gh>>

----------------------------------------------------------------------------
(* R4 (§6.7), as its own background job or inside a save (§6.1 step 1)     *)
R4J == ctl.r4j
\* the r4 job picks its record; idempotent: no closed record, nothing to do
R4_Pick ==
  /\ Step("r4_a") /\ ctl.r4j = "none"
  /\ IF ClosedJ(1) = {}
     THEN /\ JobEnd(Retired) /\ ses' = EndSesAdv
          /\ UNCHANGED <<fs, jr, pst, tgt, q, n, gh>>
     ELSE /\ ctl' = [ctl EXCEPT !.r4j = CHOOSE j \in ClosedJ(1) : TRUE]
          /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>

\* the R4 part ends: in the job, the job ends; in a save, the save goes on or is refused
R4Finish(out) ==
  IF ctl.r4ctx = "job"
  THEN /\ JobEnd(Retired) /\ ses' = EndSesAdv /\ UNCHANGED gh
  ELSE IF out = "mismatch"
       THEN /\ gh' = SetTold(gh, SV, "notsaved")    \* WRITES_PAUSED {check_failed}
            /\ JobEnd(Retired) /\ ses' = EndSesAdv
       ELSE /\ ctl' = [ctl EXCEPT !.pc = IF eng.user THEN "s_fresh" ELSE "s_locks",
                                  !.r4ctx = "none", !.r4j = "none"]
            /\ UNCHANGED <<runs, eng, ses, gh>>

\* step 1: the disk's fingerprint equals the record's post fingerprint, no .open journal
R4_Start ==
  /\ Step("r4_a") /\ ctl.r4j # "none"
  /\ IF jr[R4J].st # "closed"
     THEN /\ R4Finish("nothing") /\ UNCHANGED <<fs, jr, pst, tgt, q, n>>
     ELSE IF FP(1) # jr[R4J].pfp \/ OpenJ(1) # {}
          THEN /\ jr' = [jr EXCEPT ![R4J].st = "done", ![R4J].pl = {"done"}]
               /\ R4Finish("superseded")
               /\ UNCHANGED <<fs, pst, tgt, q, n>>
          ELSE /\ ctl' = [ctl EXCEPT !.pc = "r4_load", !.r4b = (jr[R4J].build # pst.build)]
               /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>

\* step 2: a full load_file of the disk and its digest (user code)
R4_Load ==
  /\ Step("r4_load")
  /\ \E o \in (IF ctl.r4b THEN {"na"} ELSE R4Outs) :
       ctl' = [ctl EXCEPT !.pc = "r4_c", !.r4o = o]
  /\ eng' = [eng EXCEPT !.user = TRUE]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, n, gh>>

PubLoadFile == [pst EXCEPT !.mirror[1] = [fp |-> FP(1), src |-> "load_file", build |-> pst.build]]
\* step 3: the fingerprint again
R4_Check ==
  /\ Step("r4_c")
  /\ CASE FP(1) # jr[R4J].pfp \/ OpenJ(1) # {} ->
            /\ jr' = [jr EXCEPT ![R4J].st = "done", ![R4J].pl = {"done"}]
            /\ R4Finish("superseded") /\ UNCHANGED <<pst, q, n>>
       [] ctl.r4b ->
            \* the engine build changed: publish the new build's load_file result (under the locks)
            /\ LS(1) \cap pst.orphan = {}
            /\ jr' = [jr EXCEPT ![R4J].st = "done", ![R4J].pl = {"done"}, ![R4J].sb = TRUE]
            /\ pst' = IF MUTANT = "supersede_publishes_nothing" THEN pst ELSE PubLoadFile
            /\ R4Finish("superseded") /\ UNCHANGED <<q, n>>
       [] ctl.r4o = "pass" ->
            /\ jr' = [jr EXCEPT ![R4J].st = "done", ![R4J].pl = {"done"}]
            /\ R4Finish("pass") /\ UNCHANGED <<pst, q, n>>
       [] OTHER ->
            /\ ctl' = [ctl EXCEPT !.pc = "r4_fresh"]
            /\ UNCHANGED <<jr, pst, ses, q, runs, eng, n, gh>>
  /\ UNCHANGED <<fs, tgt>>

\* step 4: the confirm in a fresh engine that runs no user code (parse only)
R4_Confirm ==
  /\ Step("r4_conf")
  \* §6.7 step 4 (O1): the fingerprint once more: moved since step 3 -> superseded
  /\ LET moved == FP(1) # jr[R4J].pfp \/ OpenJ(1) # {}
         out == CASE moved /\ MUTANT # "confirm_no_recheck" -> "superseded"
                  [] ctl.r4o = "nondet" /\ ~moved -> "nondet"
                  [] OTHER -> "mismatch"
         st == IF out = "mismatch" THEN "mismatch" ELSE "done"
     IN
     /\ jr' = [jr EXCEPT ![R4J].st = st, ![R4J].pl = {st}]
     /\ pst' = IF ~moved /\ out # "superseded" THEN PubLoadFile ELSE pst
     /\ R4Finish(out)
  /\ UNCHANGED <<fs, tgt, q, n>>

----------------------------------------------------------------------------
(* LOADS THAT READ THE LEDGER (§7): open, rebuild, preview, BQL            *)
JobW == RJ.w
RD_Load ==
  /\ Step("rd_load")
  /\ IF OpenJ(JobW) # {}
     THEN /\ MUTANT # "read_refusal_stuck"           \* seeded bug: the refusal cannot happen
          /\ JobEnd(Retired) /\ ses' = EndSesAdv      \* WRITES_PAUSED {unfinished_save}
     ELSE /\ ctl' = [ctl EXCEPT !.pc = "rd_chk", !.ofp = FP(JobW)]
          /\ eng' = [eng EXCEPT !.user = TRUE]
          /\ UNCHANGED <<runs, ses>>
  /\ UNCHANGED <<fs, jr, pst, tgt, q, n, gh>>
RD_Check ==
  /\ Step("rd_chk")
  /\ CASE FP(JobW) # ctl.ofp /\ ctl.tries < 2 ->
            /\ ctl' = [ctl EXCEPT !.pc = "rd_load", !.tries = @ + 1]
            /\ UNCHANGED <<pst, runs, eng, ses>>
       [] FP(JobW) # ctl.ofp ->                       \* LEDGER_CHANGING
            /\ JobEnd(Retired) /\ ses' = EndSesAdv /\ UNCHANGED pst
       [] RJ.k = "rebuild" ->
            /\ LS(JobW) \cap pst.orphan = {}
            /\ pst' = [pst EXCEPT !.mirror[JobW] = [fp |-> FP(JobW), src |-> "rebuild", build |-> pst.build]]
            /\ JobEnd(Retired) /\ ses' = EndSesAdv
       [] OTHER -> /\ JobEnd(Retired) /\ ses' = EndSesAdv /\ UNCHANGED pst
  /\ UNCHANGED <<fs, jr, tgt, q, n, gh>>

OP_Load ==
  /\ Step("op_load")
  /\ IF JobW = 3 /\ pst.unr
     THEN /\ JobEnd(Retired) /\ ses' = OpenFailSes
     ELSE /\ ctl' = [ctl EXCEPT !.pc = "op_chk", !.ofp = FP(JobW)]
          /\ eng' = [eng EXCEPT !.user = TRUE]
          /\ UNCHANGED <<runs, ses>>
  /\ UNCHANGED <<fs, jr, pst, tgt, q, n, gh>>
OP_Check ==
  /\ Step("op_chk")
  /\ CASE FP(JobW) # ctl.ofp /\ ctl.tries < 2 ->
            /\ ctl' = [ctl EXCEPT !.pc = "op_load", !.tries = @ + 1]
            /\ UNCHANGED <<pst, runs, eng, ses, q, n>>
       [] FP(JobW) # ctl.ofp \/ OpenJ(JobW) # {} ->
            /\ JobEnd(Retired) /\ ses' = OpenFailSes /\ UNCHANGED <<pst, q, n>>
       [] OTHER ->
            /\ LS(JobW) \cap pst.orphan = {}
            /\ JobEnd(Retired)
            /\ LET t == JobW IN
               /\ ses' = [ses EXCEPT !.app = "run", !.stage = "none", !.screen = "none", !.advice = "none",
                                     !.opened = IF MUTANT = "open_before_close" THEN @ \cup {t} ELSE {t}]
               /\ pst' = [pst EXCEPT !.mirror[t] = [fp |-> FP(t), src |-> "open", build |-> pst.build],
                                     !.last = t]
               /\ q' = QueueR4(t)
               /\ n' = n
  /\ UNCHANGED <<fs, jr, tgt, gh>>

----------------------------------------------------------------------------
(* COPY OF W2 (§8.2; app-scoped)                                           *)
CP_Check ==
  /\ Step("cp_chk")
  /\ IF \E j \in JIds : jr[j].st \in OpenSt /\ (jr[j].st = "topen" \/ jr[j].files \cap LS(2) # {})
                        /\ (MUTANT = "copy_checks_blocking_only" => jr[j].why # "")
     THEN /\ JobEnd(Retired) /\ ses' = EndSesAdv     \* RECOVERY_BLOCKED
     ELSE /\ ctl' = [ctl EXCEPT !.pc = "cp_load"] /\ UNCHANGED <<runs, eng, ses>>
  /\ UNCHANGED <<fs, jr, pst, tgt, q, n, gh>>
CP_Load ==            \* writes the copy and runs its "loads the same" check (user code)
  /\ Step("cp_load")
  /\ ctl' = [ctl EXCEPT !.pc = "cp_commit"] /\ eng' = [eng EXCEPT !.user = TRUE]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, n, gh>>
CP_Commit ==
  /\ Step("cp_commit")
  /\ ctl' = [ctl EXCEPT !.pc = "cp_man", !.past = TRUE]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>
CP_Manifest ==       \* the copy's manifest last (its target's names are the create's, §8.2)
  /\ Step("cp_man")
  /\ JobEnd(Retired) /\ ses' = EndSesAdv
  /\ gh' = [gh EXCEPT !.cpMan = TRUE]
  /\ UNCHANGED <<fs, jr, pst, tgt, q, n>>

----------------------------------------------------------------------------
(* CREATE (§8.2; app-scoped): the target folder and its names              *)
CrStop(cause) ==
  /\ CrStopGh(cause)
  /\ JobEnd(Retired) /\ ses' = EndSesAdv
  /\ UNCHANGED <<fs, jr, pst, tgt, q, n>>
OtherAppeared == tgt.c["o"] # "no"
\* step 1: the target must not exist or must be empty
CR_Target ==
  /\ Step("cr_tgt")
  /\ IF (\E x \in Names : tgt.c[x] # "no") /\ MUTANT # "no_refuse_nonempty"
     THEN /\ gh' = [gh EXCEPT !.crOut = "notempty", !.crMsg = "notempty"]
          /\ JobEnd(Retired) /\ ses' = EndSesAdv
          /\ UNCHANGED <<fs, jr, pst, tgt, q, n>>
     ELSE /\ tgt' = [tgt EXCEPT !.ex = TRUE]
          /\ ctl' = [ctl EXCEPT !.pc = IF MUTANT = "manifest_first" THEN "cr_mw" ELSE "cr_wa"]
          /\ UNCHANGED <<fs, jr, pst, ses, q, runs, eng, n, gh>>
\* step 3: each file created exclusively; only a collision with a name the job writes stops it
CR_WriteA ==
  /\ Step("cr_wa")
  /\ IF (tgt.c["a"] # "no" /\ MUTANT # "plain_file_write") \/ (MUTANT = "listing_check" /\ OtherAppeared)
     THEN CrStop("collision")
     ELSE /\ tgt' = [tgt EXCEPT !.c["a"] = "ours", !.pl["a"] = {"no","torn","ours"}]
          /\ ctl' = [ctl EXCEPT !.pc = "cr_ws"]
          /\ UNCHANGED <<fs, jr, pst, ses, q, runs, eng, n, gh>>
CR_SyncA ==
  /\ Step("cr_ws")
  /\ tgt' = [tgt EXCEPT !.pl["a"] = IF tgt.c["a"] = "ours" THEN {"ours"} ELSE @]
  /\ ctl' = [ctl EXCEPT !.pc = IF MUTANT = "no_readback" THEN "cr_load" ELSE "cr_rb"]
  /\ UNCHANGED <<fs, jr, pst, ses, q, runs, eng, n, gh>>
\* step 4: read back every file
CR_ReadBack ==
  /\ Step("cr_rb")
  /\ IF tgt.c["a"] # "ours"
     THEN CrStop("readback")
     ELSE /\ ctl' = [ctl EXCEPT !.pc = "cr_load"]
          /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>
\* step 5: the seeding's load gate on the files in the target (user code)
CR_Load ==
  /\ Step("cr_load")
  /\ ctl' = [ctl EXCEPT !.pc = IF MUTANT = "manifest_first" THEN "cr_reg" ELSE "cr_commit"]
  /\ eng' = [eng EXCEPT !.user = TRUE]
  /\ gh' = [gh EXCEPT !.crChecked = TRUE]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, n>>
\* step 6: the commit point (same table step as a save)
CR_Commit ==
  /\ Step("cr_commit")
  /\ ctl' = [ctl EXCEPT !.pc = "cr_mw", !.past = TRUE]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>
\* step 7: the manifest last: temp written and synced, exclusive rename, parent synced
CR_ManifestTemp ==
  /\ Step("cr_mw")
  /\ IF MUTANT = "listing_check" /\ OtherAppeared /\ ~ctl.past
     THEN CrStop("collision")
     ELSE /\ ctl' = [ctl EXCEPT !.pc = "cr_mr"]
          /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, n, gh>>
CR_ManifestRename ==
  /\ Step("cr_mr")
  /\ IF tgt.c["m"] # "no" /\ MUTANT # "manifest_overwrite"
     THEN \* §8.2 step 7: a .finzytrack appeared meanwhile: ours is removed, nothing registered,
          \* the foreign one is left as it is
          /\ gh' = [gh EXCEPT !.crOut = "manifest_collision", !.crMsg = "foreign_manifest"]
          /\ JobEnd(Retired) /\ ses' = EndSesAdv
          /\ UNCHANGED <<fs, jr, pst, tgt, q, n>>
     ELSE /\ tgt' = [tgt EXCEPT !.c["m"] = "ours", !.pl["m"] = {"no","ours"}]
          /\ ctl' = [ctl EXCEPT !.pc = "cr_ms"]
          /\ UNCHANGED <<fs, jr, pst, ses, q, runs, eng, n, gh>>
CR_ManifestSync ==
  /\ Step("cr_ms")
  /\ tgt' = [tgt EXCEPT !.pl["m"] = {"ours"}]
  /\ ctl' = [ctl EXCEPT !.pc = IF MUTANT = "manifest_first" THEN "cr_wa" ELSE "cr_reg"]
  /\ UNCHANGED <<fs, jr, pst, ses, q, runs, eng, n, gh>>
\* step 8: register
CR_Register ==
  /\ Step("cr_reg")
  /\ gh' = [gh EXCEPT !.crOut = "created", !.crMsg = "created"]
  /\ JobEnd(Retired) /\ ses' = EndSesAdv
  /\ UNCHANGED <<fs, jr, pst, tgt, q, n>>

----------------------------------------------------------------------------
(* ENVIRONMENT: the OS, the outside editor, the path changer               *)

\* an engine crash (any signal or exit we did not cause, a start failure): counted as a stop
R4Pcs == {"r4_a","r4_load","r4_c","r4_fresh","r4_spawn","r4_conf"}
R4Failed(j) == IF jr[j].st # "closed" THEN jr
               ELSE IF jr[j].att + 1 >= 3
                    THEN [jr EXCEPT ![j].att = @ + 1, ![j].st = "mismatch", ![j].pl = {"mismatch"}, ![j].cnc = TRUE]
                    ELSE [jr EXCEPT ![j].att = @ + 1]
CrashEng == [eng EXCEPT !.p = @ - 1, !.st = "none", !.user = FALSE, !.reason = "none",
                        !.endBy = "none", !.ended = NoEnded]
EngineCrash ==
  /\ Alive /\ Busy /\ eng.st = "busy" /\ ~eng.stuck /\ n.crash < MaxCrash
  /\ n' = [n EXCEPT !.crash = @ + 1]
  /\ LET r == RJ  s1 == [ses EXCEPT !.stops = @ + 1, !.locks = {}, !.advice = "none"] IN
     CASE r.k \in {"save","r4"} /\ ctl.pc \in R4Pcs ->
            \* an R4 that could not run: one more attempt counted on disk (§6.7)
            /\ jr' = IF R4J # "none" THEN R4Failed(R4J) ELSE jr
            /\ gh' = IF r.k = "save" THEN SetTold(gh, r.s, "notsaved") ELSE gh
            /\ JobEnd(CrashEng) /\ ses' = s1
            /\ UNCHANGED <<fs, pst, tgt, q>>
       [] r.k = "save" ->
            /\ gh' = SetTold(gh, r.s, DiskOutcome(r.s))
            \* §6.6, §8.1 (F1): the journal directory is synced before an outcome is reported from it
            /\ jr' = IF MUTANT # "outcome_unsynced" /\ jr[r.s].st # "none"
                     THEN [jr EXCEPT ![r.s].pl = {jr[r.s].st}] ELSE jr
            \* §6.6: saved, and the publish did not land: a rebuild is queued
            /\ q' = IF jr[r.s].st \in CL /\ pst.mirror[1].fp # FP(1) /\ ses.app = "run"
                       /\ ~QHas(LAMBDA j : j.k = "rebuild" /\ j.w = 1)
                    THEN q \o <<Job(NewId, "rebuild", 1, TRUE, FALSE, "app", "none", ZeroFP)>> ELSE q
            /\ JobEnd(CrashEng) /\ ses' = s1
            /\ UNCHANGED <<fs, pst, tgt>>
       [] r.k = "recover" ->
            /\ jr' = IF ctl.rj # "none" /\ MUTANT # "crash_unmarked"
                     THEN [jr EXCEPT ![ctl.rj].why = "failed"] ELSE jr
            /\ ctl' = [ctl EXCEPT !.pc = "rc_spawn", !.rj = "none",
                                  !.tried = IF ctl.rj # "none" THEN @ \cup {ctl.rj} ELSE @]
            /\ eng' = CrashEng /\ ses' = s1
            /\ UNCHANGED <<fs, pst, tgt, q, runs, gh>>
       [] r.k = "open" ->
            /\ JobEnd(CrashEng)
            /\ ses' = [OpenFailSes EXCEPT !.stops = @ + 1, !.locks = {}]
            /\ UNCHANGED <<fs, jr, pst, tgt, q, gh>>
       [] r.k = "create" ->
            /\ CrStopGh("crash") /\ JobEnd(CrashEng) /\ ses' = s1
            /\ UNCHANGED <<fs, jr, pst, tgt, q>>
       [] OTHER ->
            /\ JobEnd(CrashEng) /\ ses' = s1
            /\ UNCHANGED <<fs, jr, pst, tgt, q, gh>>

\* a hung job (slow or hung user code): its engine takes no step, but it answers a signal
Hang ==
  /\ Alive /\ Busy /\ eng.st = "busy" /\ ~ctl.hung /\ n.hang < MaxHang
  /\ ctl' = [ctl EXCEPT !.hung = TRUE]
  /\ n' = [n EXCEPT !.hang = @ + 1]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, gh>>

\* a member of the engine's group stuck in the kernel: it never ends after a signal
Stuck ==
  /\ Alive /\ eng.p > 0 /\ ~eng.stuck /\ n.stuck < MaxStuck
  /\ eng.st \in (IF StuckSigOnly THEN {"sig"} ELSE {"busy","sig"})
  /\ eng' = [eng EXCEPT !.stuck = TRUE]
  /\ n' = [n EXCEPT !.stuck = @ + 1]
  /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, ctl, gh>>

\* an I/O error after the first replace (rollback), or during a rollback or a restore
WritePcs == {"s_tw1","s_tw2","s_ts","s_sw","s_cmp","s_ds","s_rbk","s_rmt"}
RestPcs  == {"rb_chk","rs_w","rs_x","rs_c","rs_tmp","rs_sync","rs_res"}
IOFail ==
  /\ Alive /\ Busy /\ EngOK /\ n.io < MaxIO
  /\ n' = [n EXCEPT !.io = @ + 1]
  /\ \/ /\ RJ.k = "save" /\ ctl.pc \in WritePcs /\ \E f \in Tg : fs.cur[f] = PV
        /\ ctl' = StartRB("notsaved", "none")
        /\ UNCHANGED <<fs, jr, pst, tgt, ses, q, runs, eng, gh>>
     \/ /\ ctl.pc \in RestPcs /\ ctl.rj # "none"
        /\ RestoreStop("failed")
        /\ UNCHANGED <<fs, pst, tgt, q>>

\* the app (sidecar) crashes; the engine follows unless it is stuck or lingers (§5.2, §15)
AppCrash ==
  /\ Alive /\ n.ac < MaxAppCrash
  /\ \E lingers \in (IF n.orph < MaxOrphan THEN BOOLEAN ELSE {FALSE}) :
       /\ pst' = OrphanAfter(lingers)
       /\ n' = [n EXCEPT !.ac = @ + 1, !.orph = @ + (IF lingers THEN 1 ELSE 0)]
  /\ ses' = DeadSesBy("crash") /\ q' = <<>> /\ runs' = {} /\ ctl' = NoCtl /\ eng' = NoEng
  /\ UNCHANGED <<fs, jr, tgt, gh>>

\* a power loss: only synced steps count; every process dies
RECURSIVE ProdF(_, _)
ProdF(P, S) == IF S = {} THEN {<<>>}
               ELSE LET x == CHOOSE y \in S : TRUE IN
                    {[t \in DOMAIN g \cup {x} |-> IF t = x THEN v ELSE g[t]] : g \in ProdF(P, S \ {x}), v \in P[x]}
Choices(P) == {[x \in DOMAIN P |-> g[x]] : g \in ProdF(P, DOMAIN P)}
PairOr(f) == IF fs.cp[f] # {} THEN fs.cp[f] ELSE {<<0, 0>>}
PowerLoss ==
  /\ n.pw < MaxPower
  /\ \E cur0 \in Choices(fs.pl), tc0 \in Choices(fs.tpl), bk \in Choices(fs.bkpl),
        js \in Choices([j \in JIds |-> jr[j].pl]), tg \in Choices(tgt.pl),
        pR \in PairOr("R"), pN \in PairOr("N") :
     LET C  == {f \in {"R","N"} : fs.cp[f] # {}}
         pr(f) == IF f = "R" THEN pR ELSE pN
         cur == [f \in Files |-> IF f \in C THEN pr(f)[1] ELSE cur0[f]]
         tc  == [x \in TSlots |-> IF \E f \in C : x = <<fs.cpj[f], f>> THEN pr(x[2])[2] ELSE tc0[x]]
     IN
       /\ fs' = [fs EXCEPT !.cur = cur, !.pl = [f \in Files |-> {cur[f]}],
                           !.tc = tc, !.tpl = [x \in TSlots |-> {tc[x]}],
                           !.bk = bk, !.bkpl = [s \in SaveIds |-> {bk[s]}],
                           !.cp = [f \in Files |-> {}], !.cpn = [f \in Files |-> {}],
                           !.cpj = [f \in Files |-> "none"]]
       /\ jr' = [j \in JIds |-> IF js[j] = "none" THEN NoJ
                                ELSE [jr[j] EXCEPT !.st = js[j], !.pl = {js[j]}, !.why = ""]]   \* blocking is the session's judgement
       /\ tgt' = [tgt EXCEPT !.c = tg, !.pl = [x \in Names |-> {tg[x]}]]
       \* §13: an outside save a power loss dropped before that program synced it is its own loss
       /\ gh' = [gh EXCEPT !.ed = [f \in Files |->
                    IF ~EdDurable /\ gh.ed[f] # 0 /\ fs.pl[f] # {gh.ed[f]} /\ cur[f] # gh.ed[f]
                       /\ ~\E j \in JIds : f \in JFiles(j) /\ tc[<<j, f>>] = gh.ed[f]
                    THEN 0 ELSE gh.ed[f]]]
  /\ pst' = [pst EXCEPT !.orphan = {}, !.ostuck = FALSE]
  /\ n' = [n EXCEPT !.pw = @ + 1]
  /\ ses' = DeadSesBy("crash") /\ q' = <<>> /\ runs' = {} /\ ctl' = NoCtl /\ eng' = NoEng
  /\ TRUE

\* the outside editor: writes any file, durably, at any time
InLoadWindow == Busy /\ RJ.k = "save" /\ ctl.pc \in {"s_load","s_recheck"}
Editor(f) ==
  /\ f \in EdFiles /\ n.ed < MaxEdits
  /\ LET v == Frn(f, fs.cur[f])
         \* §13 file system: a durable save syncs its folder, which makes everything done before it
         \* there durable, our pending operations in W1's folder included
         C  == IF f \in LS(1) THEN {g \in {"R","N"} : fs.cp[g] # {}} ELSE {}
         T  == {<<fs.cpj[g], g>> : g \in C}
         f1 == [fs EXCEPT !.pl = [g \in Files |-> IF g \in C THEN Firsts(fs.cpn[g]) ELSE fs.pl[g]],
                          !.tpl = [x \in TSlots |-> IF x \in T THEN Seconds(fs.cpn[x[2]]) ELSE fs.tpl[x]],
                          !.cp = [g \in Files |-> IF g \in C THEN {} ELSE fs.cp[g]],
                          !.cpn = [g \in Files |-> IF g \in C THEN {} ELSE fs.cpn[g]],
                          !.cpj = [g \in Files |-> IF g \in C THEN "none" ELSE fs.cpj[g]]]
         \* not durable: kept or dropped by a power loss on its own; a pending exchange of ours on
         \* the same name stays kept-or-dropped whole
         cpE == IF fs.cp[f] # {} THEN fs.cp[f] \cup {<<v, y>> : y \in Seconds(fs.cp[f])} ELSE {}
         f2 == [fs EXCEPT !.cur[f] = v,
                          !.pl[f] = IF fs.cp[f] # {} THEN Firsts(cpE) ELSE @ \cup {v},
                          !.cp[f] = cpE,
                          !.cpn[f] = {<<v, p[2]>> : p \in fs.cpn[f]}]
     IN
     /\ fs' = IF EdDurable THEN [f1 EXCEPT !.cur[f] = v, !.pl[f] = {v}] ELSE f2
     /\ gh' = [gh EXCEPT !.ed[f] = v, !.edSeen[f] = FALSE,
                         !.edLoad = @ \/ (InLoadWindow /\ f \in LS(1) \ Tg)]
  /\ n' = [n EXCEPT !.ed = @ + 1]
  /\ UNCHANGED <<jr, pst, tgt, ses, q, runs, ctl, eng>>

\* into the create's target: a file under a name the job writes, under another name, or over
\* a file the job has already written
CrPc == IF Busy /\ RJ.k = "create" THEN ctl.pc ELSE "none"
EditorDrop(x) ==
  /\ tgt.ex /\ n.drop < MaxDrop /\ x \in {"a","m","o"} /\ tgt.c[x] = "no"
  /\ tgt' = [tgt EXCEPT !.c[x] = "frn", !.pl[x] = {"frn"}]
  /\ gh' = IF x \in {"a","m"} THEN [gh EXCEPT !.dropped = @ \cup {x}] ELSE gh
  /\ n' = [n EXCEPT !.drop = @ + 1]
  /\ UNCHANGED <<fs, jr, pst, ses, q, runs, ctl, eng>>
EditorOverwrite ==
  /\ tgt.c["a"] = "ours" /\ n.drop < MaxDrop
  /\ tgt' = [tgt EXCEPT !.c["a"] = "frn", !.pl["a"] = {"frn"}]
  /\ gh' = IF CrPc \in {"cr_ws","cr_rb"} THEN [gh EXCEPT !.ovBefore = TRUE] ELSE [gh EXCEPT !.ovAfter = TRUE]
  /\ n' = [n EXCEPT !.drop = @ + 1]
  /\ UNCHANGED <<fs, jr, pst, ses, q, runs, ctl, eng>>

\* the path changer: after a crash, a folder is replaced by a link (to a copy of the post bytes)
PathChange ==
  /\ ses.app = "dead" /\ n.link < MaxLink
  /\ \E j \in JIds : jr[j].st = "open" /\ "R" \in jr[j].files
  /\ pst' = [pst EXCEPT !.lnk = TRUE]
  /\ n' = [n EXCEPT !.link = @ + 1]
  /\ UNCHANGED <<fs, jr, tgt, ses, q, runs, ctl, eng, gh>>

\* the disk that holds K becomes unreachable, or answers again
Unreach ==
  /\ n.unr < MaxUnreach
  /\ pst' = [pst EXCEPT !.unr = ~@]
  /\ n' = [n EXCEPT !.unr = @ + 1]
  /\ UNCHANGED <<fs, jr, tgt, ses, q, runs, ctl, eng, gh>>

----------------------------------------------------------------------------
(* NEXT                                                                    *)
EngineStep ==
  \/ S_R4Check \/ S_WaitR4 \/ S_Locks \/ S_Gate \/ S_Load \/ S_Recheck \/ S_Commit
  \/ S_Backup \/ S_BackupSync \/ S_JPart \/ S_JPartSync \/ S_JOpen \/ S_JDirSync
  \/ S_TempStart \/ S_TempDone \/ S_TempSync \/ S_Swap \/ S_Compare \/ S_DirSync
  \/ S_ReadBack \/ S_RemoveTemps \/ S_Close \/ S_CloseSync \/ S_Saved \/ S_Publish \/ S_Release
  \/ RB_Check \/ RS_Write \/ RS_Swap \/ RS_Compare \/ RS_Temps \/ RS_Sync \/ RS_Resolve
  \/ RC_Init \/ RC_Next \/ RC_Lock \/ RC_Check
  \/ R4_Pick \/ R4_Start \/ R4_Load \/ R4_Check \/ R4_Confirm \/ FreshEnd
  \/ RD_Load \/ RD_Check \/ OP_Load \/ OP_Check
  \/ CP_Check \/ CP_Load \/ CP_Commit \/ CP_Manifest
  \/ CR_Target \/ CR_WriteA \/ CR_SyncA \/ CR_ReadBack \/ CR_Load \/ CR_Commit
  \/ CR_ManifestTemp \/ CR_ManifestRename \/ CR_ManifestSync \/ CR_Register

AppStep ==
  \/ Dispatch \/ DispatchUnavail \/ SpawnStep \/ CloseA \/ QuitClose \/ Assess
  \/ \E w \in WS : AppFocusCheck(w)
  \/ AppBgSave

UserStep ==
  \/ UserSave \/ \E w \in WS : UserRead(w) \/ UserCreate \/ UserCopy
  \/ \E i \in 1..Len(q) : UserCancelQueued(i)
  \/ UserCancelRunning
  \/ \E id \in 1..JobBound : UserCloseTab(id)
  \/ \E t \in WS : UserSwitch(t)
  \/ UserStay \/ UserRetry \/ UserBack \/ \E t \in WS : UserOpenAnother(t) \/ UserKeep
  \/ UserResume \/ UserForgetReadd \/ UserTryAgain
  \/ UserQuit("quit") \/ UserQuit("update") \/ UserQuitAnyway \/ Relaunch

EnvStep ==
  \/ EngineCrash \/ Hang \/ Stuck \/ IOFail \/ AppCrash \/ PowerLoss
  \/ \E f \in Files : Editor(f)
  \/ \E x \in {"a","m","o"} : EditorDrop(x) \/ EditorOverwrite
  \/ PathChange \/ Unreach \/ Drain \/ OrphanEnd \/ OrphanEndStuck \/ WindowExpire \/ AdviceTimer

Next == EngineStep \/ AppStep \/ UserStep \/ EnvStep

Spec == Init /\ [][Next]_vars

\* §13 liveness: weak fairness on engine and app steps, and on the OS ending a signalled
\* process that is not stuck (Drain, OrphanEnd); time passes (WindowExpire, AdviceTimer)
Fairness ==
  /\ WF_vars(EngineStep) /\ WF_vars(AppStep)
  /\ WF_vars(Drain) /\ WF_vars(OrphanEnd) /\ WF_vars(WindowExpire) /\ WF_vars(AdviceTimer)
FairSpec == Spec /\ Fairness
\* for EngineCanAlwaysBeFreed: the waiting user eventually presses Cancel on their own wait
\* §13 EngineCanAlwaysBeFreed's fairness: the waiting user's Cancel, a requester's Cancel of its own
\* job, and Quit anyway once the quit advice is shown
UserCancels == /\ WF_vars(\E i \in 1..Len(q) : UserCancelQueued(i)) /\ WF_vars(UserCancelRunning)
               /\ WF_vars(ses.advice # "none" /\ UserQuitAnyway)
FairUserSpec == FairSpec /\ UserCancels

----------------------------------------------------------------------------
(* SAFETY PROPERTIES (§13; names as in the spec)                           *)

\* engine and queue
AtMostOneEngine == eng.p <= 1
AtMostOneJobRunning == Cardinality(runs) <= 1
AtMostOneOpenWorkspace == Cardinality(ses.opened) <= 1
OnlyOpenWorkspaceRunsJobs ==
  \A r \in runs : /\ r.k = "recover" => ses.opened = {}
                  /\ r.k \notin {"recover","open","create","copy"} => r.w \in ses.opened
StartedId(id) == (\E r \in runs' : r.id = id) /\ ~(\E r \in runs : r.id = id)
\* §3: a job that starts is the oldest eligible waited-for job, or, with none, the oldest eligible job
WaitedForFirst ==
  [][\A i \in 1..Len(q) : StartedId(q[i].id) =>
        i = (IF EligWfIdx # {} THEN Min(EligWfIdx) ELSE Min(EligIdx))]_vars
NoOwnEndAfterCommitPoint ==   \* Quit anyway (the exception) leaves no session behind
  eng.st = "sig" /\ eng.reason = "cancel" => ~(eng.ended.past /\ Writing(eng.ended.k))
CancelReachIsTheOneRule ==
  /\ eng.endBy = "other" => (eng.ended.k = "r4" \/ (eng.ended.k = "save" /\ ~eng.ended.conf /\ ~eng.ended.past)
                             \/ (eng.ended.k \in Reads \cup {"open"} /\ eng.ended.req = "gone"))
  /\ (ses.advice = "cancel" /\ Busy /\ eng.st = "busy") =>
        ~(RJ.k = "r4" \/ (RJ.k = "save" /\ ~RJ.conf /\ ~ctl.past) \/ (RJ.k \in Reads \cup {"open"} /\ RJ.req = "gone"))

\* outcomes
CancelBeforeCommitLeavesDiskUnchanged ==
  \A s \in SaveIds : gh.cxl[s] => jr[s].st = "none" /\ \A f \in Tg : fs.cur[f] # PostV(s)
NotSaved == {"notsaved","unchanged","changed","undone","q_undone","notopen"}
TruthInv ==
  /\ \A s \in SaveIds :
       /\ gh.told[s] \in {"saved","q_saved"} => jr[s].st \in CL
       /\ gh.told[s] \in NotSaved => (jr[s].st \in {"none","part","tpart","resolved"} /\ \A f \in Tg : fs.cur[f] # PostV(s))
       /\ gh.told[s] \in {"unfinished","q_notyet"} => jr[s].st \in {"open","kept"}
       /\ gh.told[s] # "notopen"                       \* a confirmed save is never dropped by a switch
       /\ s \in SaveNotes => jr[s].st # "kept"
  /\ gh.crTold = "q_created" => tgt.c["m"] = "ours"
  /\ gh.crTold = "q_notcreated" => tgt.c["m"] # "ours"
  /\ gh.cpTold = "q_created" => gh.cpMan
  /\ gh.cpTold = "q_notcreated" => ~gh.cpMan
NoteRule ==   \* the note is deleted only once the record says saved or undone, or the user kept it
  \A nt \in pst.notes \ pst'.notes : (nt.k = "save" /\ ses'.app # "dead") =>
     jr'[nt.s].st \in CL \cup {"resolved","none","part","tpart","kept"}   \* a .part replaced nothing
OutcomeTruthful == []TruthInv /\ [][NoteRule]_vars
SavedSurvivesPowerLoss ==
  \A s \in SaveIds : gh.told[s] \in {"saved","q_saved"} =>
     /\ jr[s].pl \subseteq CL
     /\ \A f \in Tg : (fs.cur[f] = PostV(s) /\ ~\E t \in JIds : t # s /\ f \in jr[t].files
                                                         /\ jr[t].st \in OpenSt \cup {"part","tpart"})
                     => fs.pl[f] = {PostV(s)}
UnchangedOnlyAfterRehash ==
  [][\A s \in SaveIds : (gh'.told[s] = "unchanged" /\ gh.told[s] # "unchanged")
                         => \A f \in Tg : fs'.cur[f] = gh.pre[s][f]]_vars

\* write protocol
OnDisk(s, f) == fs.cur[f] = PostV(s) \/ PostV(s) \in fs.pl[f]
BeforeClose(s) == jr[s].st \notin CL \cup {"resolved","kept"}
BackupDurableBeforeReplace ==
  \A s \in SaveIds : OnDisk(s, "R") /\ BeforeClose(s) => fs.bkpl[s] = {gh.pre[s]["R"]}
JournalDurableBeforeFirstReplace ==
  \A s \in SaveIds : (\E f \in Tg : OnDisk(s, f)) /\ BeforeClose(s) => jr[s].pl = {"open"}
PartJournalNeverBlocks ==
  \A j \in JIds : jr[j].why # "" => jr[j].st \in {"open","kept","resolved"}
CommittedCombinationChecked ==
  Busy /\ RJ.k = "save" /\ ctl.past => ~gh.edLoad
ExternalEditNeverSilentlyLost ==
  \A f \in Files : gh.ed[f] # 0 /\ ~gh.edSeen[f] =>
     \/ gh.ed[f] = fs.cur[f] \/ gh.ed[f] = fs.rt[f]
     \/ \E j \in JIds : f \in JFiles(j) /\ fs.tc[<<j, f>>] = gh.ed[f]
     \/ gh.ed[f] \in gh.acc[f]

\* recovery
NoTornLedger == \A f \in Files : fs.cur[f] = TORN => \E j \in JIds : jr[j].st \in OpenSt /\ f \in jr[j].files
ResolveDurable ==
  \A j \in JIds : (jr[j].st # "resolved" /\ jr'[j].st = "resolved") =>
     \* every file durably holds something other than our post bytes: its pre bytes, or the
     \* outside bytes that were put back (§6.4)
     \A f \in jr[j].files : fs'.cur[f] \notin {jr[j].post[f], TORN}
                            /\ jr[j].post[f] \notin fs'.pl[f] /\ TORN \notin fs'.pl[f]
NoTornLedgerAfterRecovery == []NoTornLedger /\ [][ResolveDurable]_vars
\* ExternalEditNeverSilentlyLost for the files a journal names (the ones recovery and rollback swap)
RecoveryNeverClobbersForeignBytes ==
  \A f \in Files : (gh.ed[f] # 0 /\ ~gh.edSeen[f] /\ \E j \in JIds : f \in jr[j].files) =>
     \/ gh.ed[f] = fs.cur[f] \/ gh.ed[f] = fs.rt[f]
     \/ \E j \in JIds : f \in JFiles(j) /\ fs.tc[<<j, f>>] = gh.ed[f]
     \/ gh.ed[f] \in gh.acc[f]
RecoveryWritesOnlyRecordedPaths == ~gh.outW
\* a cut temp (torn, beside a file that does not hold post) is never the reason a journal stops,
\* and no temp of ours outlives its save once the journal is closed (§6.1 step 8.4)
OnlyCutTemps(j) ==
  /\ \A f \in jr[j].files :
        /\ ~(PathMoved(f)) /\ ~(pst.unr /\ f = "K")
        /\ fs.cur[f] \in {jr[j].pre[f], jr[j].post[f]} /\ ~BackupBad(j, f)
        /\ (fs.cur[f] = jr[j].post[f] => fs.tc[<<j, f>>] \in {ABS, jr[j].pre[f], jr[j].post[f]})
  /\ \E f \in jr[j].files : fs.tc[<<j, f>>] \notin {ABS, jr[j].pre[f], jr[j].post[f]}
CutTempNoBlock ==
  \A j \in JIds : (jr[j].why = "" /\ jr'[j].why = "stopped" /\ jr[j].st = "open" /\ fs'.rt = fs.rt)
                   => ~OnlyCutTemps(j)
NoTempOutlivesJournal ==
  \A j \in SaveIds, f \in Tg :
     fs.tc[<<j, f>>] # ABS => (jr[j].st \in {"open","topen","kept","part","tpart","none"} \/ (Busy /\ ctl.s = j))
CutTempNeverBlocks == [][CutTempNoBlock]_vars /\ []NoTempOutlivesJournal
EveryJournalRecoveredOrBlocking ==
  ((\E r \in runs : r.k = "open") \/ ses.opened # {}) =>
     \A j \in JIds : jr[j].st \in OpenSt /\ jr[j].old => jr[j].why # ""
NoOpenOverUnresolvedJournal ==
  \A w \in ses.opened \cup {r.w : r \in {x \in runs : x.k = "open"}} :
     \A j \in JIds : jr[j].st \in OpenSt /\ jr[j].old =>
        jr[j].st = "open" /\ jr[j].files \cap LS(w) = {}
NoCopyOverOpenJournal ==
  \A r \in runs : r.k = "copy" /\ ctl.pc # "cp_chk" =>
     ~\E j \in JIds : jr[j].st \in OpenSt /\ (jr[j].st = "topen" \/ jr[j].files \cap LS(2) # {})
Cause(j, why) ==
  CASE why = "in_use" -> jr[j].files \cap pst.orphan # {}
    [] why = "stopped" -> jr[j].st = "topen"
                          \/ (\E f \in jr[j].files :
                                \/ PathMoved(f) \/ (pst.unr /\ f = "K")
                                \/ fs.cur[f] \notin {jr[j].pre[f], jr[j].post[f]}
                                \/ (fs.cur[f] = jr[j].post[f] /\ fs.tc[<<j, f>>] \notin {ABS, jr[j].pre[f], jr[j].post[f]})
                                \/ BackupBad(j, f)
                                \/ fs.rt[f] \notin {ABS, jr[j].post[f]})   \* displaced foreign bytes at a restore
    [] why = "cancelled" -> eng'.reason = "cancel"
    [] why = "failed" -> n'.crash > n.crash \/ n'.io > n.io
    [] why = "not_tried" -> ses.stops >= StopBound
    [] OTHER -> FALSE
BlockedOnlyForCause ==
  [][\A j \in JIds : (jr[j].why = "" /\ jr'[j].why # "") => Cause(j, jr'[j].why)]_vars

\* R4
SaveWriting == Busy /\ RJ.k = "save" /\ jr[ctl.s].st \in {"part","tpart","open","topen"}
NoSaveWhileR4Owed ==
  SaveWriting => ~\E t \in JIds : t # ctl.s /\ jr[t].lin = 1 /\ jr[t].st \in {"closed","mismatch"}
RecordsMatchByLineage ==
  SaveWriting => ~\E t \in JIds : t # ctl.s /\ jr[t].lin = 1 /\ jr[t].key # pst.reg1
                                   /\ jr[t].st \in {"closed","mismatch"}
R4GateLiftedOnlyByUser ==
  [][\A j \in JIds : (jr[j].st = "mismatch" /\ jr'[j].st # "mismatch") => gh'.resumed = gh.resumed + 1]_vars
BuildChangePublishesRealLoad ==
  [][\A j \in JIds : (jr[j].st # "done" /\ jr'[j].st = "done" /\ jr'[j].sb) =>
        pst'.mirror[1].src = "load_file" /\ pst'.mirror[1].build = pst.build]_vars

\* create and copy
NoManifestOverIncompleteFolder ==
  /\ tgt.c["m"] = "ours" => (tgt.c["a"] = "ours" \/ gh.ovAfter) /\ gh.crChecked
  /\ "ours" \in tgt.pl["m"] => (tgt.pl["a"] = {"ours"} \/ gh.ovAfter)
NonEmptyTargetUntouched ==
  CreateTarget = "nonempty" => \A x \in {"a","m"} : tgt.c[x] # "ours" /\ "ours" \notin tgt.pl[x]
DroppedFileNeverReplaced == \A x \in gh.dropped : tgt.c[x] = "frn"
OtherNamesDoNotStop ==
  /\ gh.crOut = "collision" => tgt.c["a"] = "frn"
  /\ gh.crOut = "manifest_collision" => tgt.c["m"] = "frn"
ForeignOverwriteCaughtByReadBack ==
  gh.ovBefore => tgt.c["m"] # "ours" /\ CrPc \notin {"cr_load","cr_commit","cr_mw","cr_mr","cr_ms","cr_reg"}
DeleteAdviceOnlyForOwnFiles ==
  [][(gh'.crMsg = "delete_ok" /\ gh.crMsg # "delete_ok") => \A x \in Names : tgt.c[x] \in {"no","ours"}]_vars

\* update
InstallerNeverOverOpenJournal ==
  [][pst'.build # pst.build => \A j \in JIds : jr[j].st \notin OpenSt]_vars

\* advice and exits
NoAdviceOnTimerForSlowWork == ses.advice = "timer" => eng.st = "sig"
OwnRunWait == Busy /\ eng.st = "busy" /\ RJ.wf /\ RJ.req = "present" /\ RJ.own \in {"user","app","switch"}
\* §13: in every state where the user waits, the action that ends the wait is itself enabled
\* (the actions carry no counter or bound of the model's own)
OwnQueuedWait == \E i \in 1..Len(q) : CancelableQ(i)
EveryWaitHasAnExit ==
  /\ OwnQueuedWait => ENABLED (\E i \in 1..Len(q) : UserCancelQueued(i))                 \* Cancel
  /\ (OwnRunWait /\ ~ctl.past) => ENABLED UserCancelRunning                              \* Cancel
  /\ (OwnRunWait /\ ctl.past) => ENABLED UserQuitAnyway                     \* "Finishing your change…"
  /\ (Busy /\ ctl.pc \in SpawnPcs) => ENABLED UserQuitAnyway      \* a slot a stuck process holds
  /\ (ses.app = "switch" /\ ses.stage = "quiesce") => ENABLED UserStay                   \* Stay
  /\ ses.screen # "none" => /\ ENABLED UserRetry                                          \* Retry
                            /\ ses.sctx = "switch" => ENABLED UserBack                    \* Back
                            /\ ses.sctx = "boot" => ENABLED (\E t \in WS : UserOpenAnother(t))
                            /\ ses.screen = "blocked" => ENABLED UserKeep
  /\ ses.app \in {"quit","update"} => ENABLED UserQuitAnyway                           \* Quit anyway
QuitAnywayAlwaysEnabled == Alive => ENABLED UserQuitAnyway

----------------------------------------------------------------------------
(* LIVENESS (§13)                                                          *)
JobIds == 1..JobBound
CreateWaiting(id) == \E i \in 1..Len(q) : q[i].id = id /\ q[i].k = "create"
EveryBootEnds ==
  /\ [](ses.app = "boot" ~> ses.app # "boot")
  /\ \A id \in JobIds : [](CreateWaiting(id) ~> (~CreateWaiting(id) \/ ses.app = "dead"))
WaitedJob(id) == (\E i \in 1..Len(q) : q[i].id = id /\ q[i].wf) \/ (\E r \in runs : r.id = id /\ r.wf)
EveryWaitedForJobEnds == \A id \in JobIds : [](WaitedJob(id) ~> ~WaitedJob(id))
EverySwitchEnds ==
  [](ses.app = "switch" /\ ses.stage # "screen" ~> (ses.app # "switch" \/ ses.stage = "screen"))
\* §13: every quit ends, or Quit anyway is offered; Quit anyway, when taken, always ends the app.
\* "Offered" is the quit's "Finishing your last change…" state (§9.3 step 3): a confirmed save,
\* create or copy is still queued or running, with Quit anyway enabled. A quit with nothing left to
\* finish must end by itself (close does not wait for the reap, §9.3 step 4).
Finishing == (QHas(LAMBDA j : Writing(j.k) /\ j.conf) \/ (Busy /\ Writing(RJ.k) /\ RJ.conf))
QuitAnywayOffered == Finishing /\ ENABLED UserQuitAnyway
EveryQuitEndsOrOffersQuitAnyway ==
  /\ [](ses.app \in {"quit","update"} ~> (ses.app \notin {"quit","update"} \/ QuitAnywayOffered))
  /\ [][UserQuitAnyway => ses'.app = "dead"]_vars
OwedR4BeforeNextSave ==
  [][\A s \in SaveIds : (jr[s].st # "closed" /\ jr'[s].st = "closed") =>
        ~\E t \in JIds : t # s /\ jr[t].lin = 1 /\ jr[t].st \in {"closed","mismatch"}]_vars
\* §13, as stated: (a waited-for job is queued) ~> (the engine slot is free, or the quit advice is
\* shown, or the app has quit), under FairUserSpec; Quit anyway is always enabled
WaitedQueued == \E i \in 1..Len(q) : q[i].wf
EngineSlotFree == ~Busy /\ eng.st \in {"none","idle"}
EngineCanAlwaysBeFreed ==
  /\ [](WaitedQueued ~> (EngineSlotFree \/ ses.advice # "none" \/ ses.app = "dead"))
  /\ []QuitAnywayAlwaysEnabled

\* model sanity: a running engine step is never stuck without cause (an orphan's lock is the only wait)
NoEngineStall == (Alive /\ Busy /\ EngOK /\ pst.orphan = {}) => ENABLED EngineStep
\* O1 (§6.7 step 4): a mismatch only with the disk still at the record's post fingerprint
NoFalseMismatch ==
  [][\A j \in JIds : (jr[j].st = "closed" /\ jr'[j].st = "mismatch" /\ ~jr'[j].cnc) => FP(1) = jr[j].pfp]_vars
\* §13 "Also safety" (N2): no create or copy starts while recover_all runs, or between a switch's close
\* and its target's open or screen; one started earlier runs on (§3, §9.1 step 2)
RecoveryWindow == ses.app = "boot" \/ (ses.app = "switch" /\ ses.stage \in {"recover","assess","opening"})
                  \/ (Busy /\ RJ.k = "recover")
AppScopedStarts == \A i \in 1..Len(q) : (StartedId(q[i].id) /\ q[i].k \in {"create","copy"}) => ~RecoveryWindow
\* the wording before N2 ("no create or copy runs" in the window). Mutant appscoped_runs checks it instead:
\* not a seeded bug but a reachability check; it must fail, so the model still lets a create or copy that
\* started before a switch's close run on through the window, as §9.1 step 2 says
AppScopedRuns == (Busy /\ RJ.k \in {"create","copy"}) => ~RecoveryWindow
AppScopedWaitsForRecovery ==
  [][IF MUTANT = "appscoped_runs" THEN AppScopedRuns' ELSE AppScopedStarts]_vars
JobIdsBounded == Cardinality(UsedIds) <= JobBound
=============================================================================
