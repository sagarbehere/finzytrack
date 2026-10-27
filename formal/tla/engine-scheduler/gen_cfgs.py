#!/usr/bin/env python3
"""Write every TLC configuration of the engine-scheduler model, and configs.txt.

Run from anywhere: python3 gen_cfgs.py. The .cfg files and configs.txt it writes are committed;
edit this table, not the files (a test of the folder checks they are in step: --check).

A configuration = a world (constants), the specification (Spec, FairSpec, FairUserSpec), the
properties to check and the result it must have. Tiers: S (small, seconds to minutes), M (medium),
L (large). Every mutant runs on a small world and checks only the property it must break.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_DIR = os.path.join(HERE, "cfg")

DEFAULTS = {
    "MUTANT": '"none"', "Fixes": '{"sync_before_report", "rollback_checks_temps", "sync_after_swap_back", "switch_screen_frees_queue"}', "BootJ": "{}", "BootTemps": "FALSE", "BootTarget": "1",
    "BootOrphan": "FALSE", "BootLink": "FALSE", "EdFiles": "{}", "R4Outs": '{"pass"}',
    "StopBound": "3",
    "MaxSaves": "0", "MaxBg": "0", "MaxReads": "0", "MaxRebuild": "0", "MaxEdits": "0",
    "MaxCrash": "0", "MaxAppCrash": "0", "MaxPower": "0", "MaxStarts": "0", "MaxSwitch": "0",
    "MaxHang": "0", "MaxStuck": "0", "StuckSigOnly": "FALSE", "MaxOrphan": "0",
    "MaxUnreach": "0", "MaxLink": "0", "MaxForget": "0", "MaxIO": "0", "MaxClose": "0",
    "MaxUpdate": "0", "MaxRetry": "0", "MaxExpire": "0", "MaxDrop": "0",
    "MaxQuitAnyway": "0", "MaxResume": "0",
    "DoCopy": "FALSE", "CreateTarget": '"none"', "DoQuit": "FALSE", "CloseQueued": "TRUE", "JobBound": "12",
}

# State invariants (INVARIANT) and temporal safety formulas (PROPERTY) of §13.
INVARIANTS = [
    "AtMostOneEngine", "AtMostOneJobRunning", "AtMostOneOpenWorkspace", "OnlyOpenWorkspaceRunsJobs",
    "NoOwnEndAfterCommitPoint", "CancelReachIsTheOneRule",
    "CancelBeforeCommitLeavesDiskUnchanged", "SavedSurvivesPowerLoss",
    "BackupDurableBeforeReplace", "JournalDurableBeforeFirstReplace", "PartJournalNeverBlocks",
    "CommittedCombinationChecked", "ExternalEditNeverSilentlyLost",
    "RecoveryNeverClobbersForeignBytes", "RecoveryWritesOnlyRecordedPaths",
    "EveryJournalRecoveredOrBlocking", "NoOpenOverUnresolvedJournal", "NoCopyOverOpenJournal",
    "NoSaveWhileR4Owed", "RecordsMatchByLineage",
    "NoManifestOverIncompleteFolder", "NonEmptyTargetUntouched", "DroppedFileNeverReplaced",
    "OtherNamesDoNotStop", "ForeignOverwriteCaughtByReadBack",
    "NoAdviceOnTimerForSlowWork", "EveryWaitHasAnExit", "QuitAnywayAlwaysEnabled", "JobIdsBounded",
]
SAFETY_PROPS = [
    "WaitedForFirst", "OutcomeTruthful", "UnchangedOnlyAfterRehash", "NoTornLedgerAfterRecovery",
    "CutTempNeverBlocks", "BlockedOnlyForCause", "R4GateLiftedOnlyByUser",
    "BuildChangePublishesRealLoad", "DeleteAdviceOnlyForOwnFiles", "InstallerNeverOverOpenJournal",
    "OwedR4BeforeNextSave",
]
LIVENESS = ["EveryBootEnds", "EveryWaitedForJobEnds", "EverySwitchEnds", "EveryQuitEnds",
            "EngineCanAlwaysBeFreed"]


def S(**kw):
    return {k: (str(v).upper() if isinstance(v, bool) else str(v)) for k, v in kw.items()}


def strs(*xs):
    return "{" + ", ".join('"%s"' % x for x in xs) + "}"


ALLR4 = strs("pass", "nondet", "differ")

# name: (tier, world overrides, specification, liveness properties or None for all safety)
WORLDS = {
    # --- the save (§6.1-§6.6, §10) ---
    "S_save_crash": ("S", S(MaxSaves=1, MaxCrash=1, MaxIO=1, MaxEdits=1, EdFiles=strs("R", "N", "I"),
                            MaxStarts=1, MaxAppCrash=1, MaxReads=1)),
    "S_save_power": ("S", S(MaxSaves=1, MaxPower=1, MaxStarts=1, MaxEdits=1, EdFiles=strs("R"))),
    "S_save_quit": ("S", S(MaxSaves=1, MaxQuitAnyway=1, MaxStarts=2, MaxStuck=1, MaxOrphan=1,
                           MaxRetry=1, MaxClose=1)),
    "S_cancel_commit": ("S", S(MaxSaves=1, MaxEdits=1, EdFiles=strs("R", "I"), MaxCrash=1)),
    # --- R4 and records (§6.5, §6.7) ---
    "S_r4_outcomes": ("S", S(MaxSaves=2, R4Outs=ALLR4, MaxEdits=1, EdFiles=strs("I"), MaxResume=1)),
    "S_r4_attempts": ("S", S(MaxSaves=2, MaxCrash=3, MaxExpire=3, MaxResume=1, MaxStarts=1,
                             MaxAppCrash=1)),
    "S_r4_build": ("S", S(MaxSaves=1, DoQuit=True, MaxUpdate=1, MaxStarts=1, MaxCrash=1)),
    "S_forget": ("S", S(MaxSaves=2, MaxSwitch=2, MaxForget=1, MaxAppCrash=1, MaxStarts=1)),
    # --- recover-all, boot, blocking (§8.1, §9.2) ---
    "S_recover": ("S", S(BootJ=strs("b2", "b3"), MaxCrash=1, MaxUnreach=1,
                         MaxRetry=1, MaxEdits=1, EdFiles=strs("R"))),
    "S_recover_bound": ("S", S(BootJ=strs("b2", "b3"), MaxCrash=2, StopBound=2, MaxRetry=1)),
    "S_recover_lock": ("S", S(BootJ=strs("b2"), BootTemps=True, BootOrphan=True, BootLink=True,
                              MaxRetry=2, MaxStarts=1, MaxAppCrash=1)),
    "S_recover_power": ("S", S(BootJ=strs("b2"), MaxPower=1, MaxStarts=1, MaxIO=1, MaxEdits=1,
                               EdFiles=strs("R"), MaxLink=1, MaxRetry=1)),
    "S_boot": ("S", S(BootJ=strs("b2"), BootTarget=2, MaxCrash=1, MaxRetry=2, CreateTarget='"absent"',
                      MaxUnreach=1)),
    # --- copy and create (§8.2) ---
    "S_copy": ("S", S(BootJ=strs("b2"), DoCopy=True, MaxSaves=1, MaxCrash=1, MaxRetry=1)),
    "S_create_absent": ("S", S(BootTarget=0, CreateTarget='"absent"', MaxDrop=2, MaxCrash=1,
                               MaxQuitAnyway=1, MaxStarts=1)),
    "S_create_empty": ("S", S(BootTarget=0, CreateTarget='"empty"', MaxDrop=2, MaxPower=1,
                              MaxStarts=1)),
    "S_create_nonempty": ("S", S(BootTarget=0, CreateTarget='"nonempty"', MaxDrop=1)),
    # --- queue, Cancel's one rule, the engine (§3, §4, §5) ---
    "S_cancel": ("S", S(MaxSaves=1, MaxBg=1, MaxReads=1, MaxHang=1, MaxStuck=1, MaxClose=1,
                        MaxQuitAnyway=1)),
    "S_queue": ("S", S(MaxSaves=1, MaxBg=1, MaxReads=1, MaxCrash=1, MaxRebuild=1, MaxEdits=1,
                       EdFiles=strs("I"))),
    "S_switch_fail": ("S", S(MaxSwitch=2, MaxUnreach=1, MaxRetry=2, MaxCrash=1, MaxEdits=1,
                             EdFiles=strs("J"))),
    # --- switch, quit, update (§9) ---
    "S_switch": ("S", S(MaxSaves=1, MaxBg=1, MaxReads=1, MaxSwitch=2, CreateTarget='"empty"')),
    "S_quit": ("S", S(DoQuit=True, MaxSaves=1, MaxUpdate=1, MaxStarts=1, MaxQuitAnyway=1,
                      MaxCrash=1)),
    # --- medium: two actors combined ---
    "L_save_all": ("L", S(JobBound=30, MaxSaves=1, MaxCrash=1, MaxIO=1, MaxEdits=2, EdFiles=strs("R", "N", "I"),
                          MaxPower=1, MaxStarts=2, MaxAppCrash=1, MaxQuitAnyway=1)),
    "M_recover_temps": ("M", S(BootJ=strs("b2", "b3"), BootTemps=True, MaxCrash=1, MaxUnreach=1,
                               MaxRetry=1, MaxEdits=1, EdFiles=strs("R"))),
    "L_recover_all": ("L", S(BootJ=strs("b2", "b3"), BootTemps=True, BootOrphan=True, MaxCrash=2,
                             MaxUnreach=1, MaxRetry=2, MaxEdits=1, EdFiles=strs("R", "J", "K"),
                             DoCopy=True, MaxPower=1, MaxStarts=1)),
    "L_r4_switch": ("L", S(JobBound=30, MaxSaves=2, MaxBg=1, R4Outs=ALLR4, MaxSwitch=2, MaxForget=1,
                           MaxCrash=2, MaxResume=1, MaxEdits=1, EdFiles=strs("I"))),
    "L_session": ("L", S(JobBound=30, MaxSaves=1, MaxBg=1, MaxReads=1, MaxRebuild=1, MaxSwitch=2, MaxHang=1,
                         MaxStuck=1, MaxClose=1, MaxCrash=1, DoQuit=True, MaxQuitAnyway=1,
                         MaxStarts=1, CreateTarget='"empty"', MaxEdits=1, EdFiles=strs("R"))),
    "M_r4_switch": ("M", S(MaxSaves=2, R4Outs=ALLR4, MaxSwitch=1, MaxForget=1, MaxCrash=1,
                           MaxResume=1)),
    # --- large: the whole world at once ---
    "L_world": ("L", S(JobBound=30, BootJ=strs("b2", "b3"), BootTemps=True, MaxSaves=2, MaxBg=1, MaxReads=1,
                       R4Outs=ALLR4, MaxEdits=2, EdFiles=strs("R", "N", "I", "K"), MaxCrash=2,
                       MaxAppCrash=1, MaxPower=1, MaxStarts=2, MaxSwitch=2, MaxHang=1, MaxStuck=1,
                       MaxOrphan=1, MaxUnreach=1, MaxForget=1, MaxIO=1, MaxClose=1, MaxUpdate=1,
                       MaxRetry=2, MaxDrop=1, MaxQuitAnyway=1, MaxResume=1, DoCopy=True,
                       CreateTarget='"empty"', DoQuit=True)),
    "L_save_deep": ("L", S(JobBound=30, MaxSaves=2, MaxBg=1, MaxCrash=2, MaxIO=1, MaxEdits=3,
                           EdFiles=strs("R", "N", "I"), MaxPower=2, MaxStarts=3, MaxAppCrash=1,
                           MaxQuitAnyway=1, MaxOrphan=1, R4Outs=ALLR4, MaxResume=1, MaxRetry=1)),
}

# liveness configurations: world, spec, properties
LIVE = {
    "LS_boot": ("S", S(BootJ=strs("b2"), BootTarget=2, MaxCrash=1, MaxRetry=1, CreateTarget='"absent"',
                       JobBound=6), "FairSpec", ["EveryBootEnds"]),
    "LS_jobs": ("S", S(MaxSaves=1, MaxBg=1, MaxReads=1, MaxCrash=1, MaxSwitch=1, MaxExpire=2,
                       JobBound=8), "FairSpec",
                ["EveryWaitedForJobEnds", "EverySwitchEnds"]),
    "LS_quit": ("S", S(DoQuit=True, MaxSaves=1, MaxReads=1, MaxStuck=1, StuckSigOnly=True, MaxUpdate=1,
                       JobBound=6), "FairSpec", ["EveryQuitEnds"]),
    "LS_free": ("S", S(MaxSaves=1, MaxBg=1, MaxReads=1, MaxHang=1, MaxStuck=1, MaxClose=1,
                       MaxQuitAnyway=1, CloseQueued=False, JobBound=6), "FairUserSpec", ["EngineCanAlwaysBeFreed"]),
    "LM_free": ("M", S(MaxSaves=1, MaxBg=1, MaxReads=1, MaxHang=1, MaxStuck=1, MaxClose=1,
                       MaxQuitAnyway=1, CreateTarget='"empty"', CloseQueued=False, JobBound=8),
                "FairUserSpec", ["EngineCanAlwaysBeFreed"]),
    "LL_jobs": ("L", S(MaxSaves=2, MaxBg=1, MaxReads=1, MaxRebuild=1, MaxCrash=2, MaxSwitch=2,
                       MaxExpire=2, BootJ=strs("b2"), MaxRetry=1, MaxEdits=1, EdFiles=strs("R"),
                       CreateTarget='"empty"', JobBound=14), "FairSpec",
                ["EveryBootEnds", "EveryWaitedForJobEnds", "EverySwitchEnds"]),
}

# findings: the spec as written, without one of the model's fixes; must violate (MAPPING.md, findings)
FINDINGS = [
    ("F1_outcome_unsynced", "S_save_power", "SavedSurvivesPowerLoss",
     S(Fixes=strs("rollback_checks_temps", "sync_after_swap_back", "switch_screen_frees_queue"), MaxCrash=1, MaxEdits=0, EdFiles="{}")),
    ("F2_rollback_deletes_displaced", "S_save_crash", "ExternalEditNeverSilentlyLost",
     S(Fixes=strs("sync_before_report", "sync_after_swap_back", "switch_screen_frees_queue"), MaxCrash=0, MaxAppCrash=0, MaxReads=0, MaxStarts=0,
       EdFiles=strs("R"))),
    ("F3_swap_back_unsynced", "S_save_power", "ExternalEditNeverSilentlyLost",
     S(Fixes=strs("sync_before_report", "rollback_checks_temps", "switch_screen_frees_queue"))),
    ("F4_switch_screen_holds_queue", "LS_boot", "EveryBootEnds",
     S(Fixes=strs("sync_before_report", "rollback_checks_temps", "sync_after_swap_back"))),
]

# mutant: (world it runs on, property it must break, extra overrides)
# The first 35 are §13's "thirty-five mutants of the two executable models"; then §13's five more;
# then one per property §13 names that had none (PROCESS.md §3: each property has a mutant).
MUTANTS = [
    # model 1 (16)
    ("report_before_close_sync", "S_save_power", "SavedSurvivesPowerLoss", {}),
    ("no_journal_sync", "S_save_crash", "JournalDurableBeforeFirstReplace", {}),
    ("no_backup_sync", "S_save_crash", "BackupDurableBeforeReplace", {}),
    ("no_temp_fsync", "S_save_power", "NoTornLedgerAfterRecovery", {}),
    ("gate_in_memory", "S_save_crash", "NoSaveWhileR4Owed", S(MaxSaves=2, MaxIO=0, MaxEdits=0, MaxReads=0)),
    ("no_path_guard", "S_recover_lock", "RecoveryWritesOnlyRecordedPaths", S(BootOrphan=False)),
    ("recover_target_only", "S_recover", "NoOpenOverUnresolvedJournal", {}),
    ("cancel_after_commit", "S_cancel_commit", "NoOwnEndAfterCommitPoint", {}),
    ("resolve_before_restores_synced", "S_recover_power", "NoTornLedgerAfterRecovery", {}),
    ("no_temp_check", "S_save_crash", "RecoveryNeverClobbersForeignBytes", {}),
    ("plain_create", "S_save_crash", "ExternalEditNeverSilentlyLost", S(EdFiles=strs("N"))),
    ("temps_after_close", "S_save_crash", "CutTempNeverBlocks", {}),
    ("old_temp_rule", "S_recover_lock", "CutTempNeverBlocks", S(BootLink=False, BootOrphan=False)),
    ("quit_says_undone", "S_save_quit", "OutcomeTruthful", {}),
    ("gate_by_key", "S_forget", "RecordsMatchByLineage", {}),
    ("quit_note_two_branches", "S_save_quit", "OutcomeTruthful", {}),
    # model 2 (19)
    ("cancel_stops_all", "S_recover", "EveryJournalRecoveredOrBlocking", {}),
    ("crash_unmarked", "S_recover", "EveryJournalRecoveredOrBlocking", {}),
    ("untried_unmarked", "S_recover_bound", "EveryJournalRecoveredOrBlocking", {}),
    ("no_displaced_check", "S_save_crash", "ExternalEditNeverSilentlyLost", {}),
    ("direct_open", "S_save_power", "PartJournalNeverBlocks", {}),
    ("no_recheck", "S_save_crash", "CommittedCombinationChecked", {}),
    ("manifest_first", "S_create_empty", "NoManifestOverIncompleteFolder", {}),
    ("no_refuse_nonempty", "S_create_nonempty", "NonEmptyTargetUntouched", {}),
    ("copy_checks_blocking_only", "S_copy", "NoCopyOverOpenJournal", {}),
    ("plain_file_write", "S_create_absent", "DroppedFileNeverReplaced", {}),
    ("no_readback", "S_create_absent", "ForeignOverwriteCaughtByReadBack", {}),
    ("boot_waits_for_success", "LS_boot", "EveryBootEnds", {}),
    ("boot_no_actions", "S_boot", "EveryWaitHasAnExit", {}),
    ("cancel_ends_writing_job", "S_cancel", "CancelReachIsTheOneRule", {}),
    ("no_quit_message_for_writing", "LS_free", "EngineCanAlwaysBeFreed", {}),
    ("r4_only", "S_cancel", "CancelReachIsTheOneRule", {}),
    ("advice_on_timer", "S_cancel", "NoAdviceOnTimerForSlowWork", {}),
    ("listing_check", "S_create_absent", "OtherNamesDoNotStop", {}),
    ("delete_message_always", "S_create_absent", "DeleteAdviceOnlyForOwnFiles", {}),
    # §13's five more
    ("dispatch_by_arrival", "S_queue", "WaitedForFirst", {}),
    ("switch_drops_confirmed_save", "S_switch", "OutcomeTruthful", {}),
    ("recovery_while_open", "S_switch", "OnlyOpenWorkspaceRunsJobs", {}),
    ("spawn_before_reap", "S_queue", "AtMostOneEngine", {}),
    ("supersede_publishes_nothing", "S_r4_build", "BuildChangePublishesRealLoad", {}),
    # one per property that §13 names without a mutant (added by the model, MAPPING.md)
    ("dispatch_while_running", "S_queue", "AtMostOneJobRunning", {}),
    ("open_before_close", "S_switch", "AtMostOneOpenWorkspace", {}),
    ("gate_after_journal", "S_cancel_commit", "CancelBeforeCommitLeavesDiskUnchanged", {}),
    ("unchanged_without_rehash", "S_cancel_commit", "UnchangedOnlyAfterRehash", {}),
    ("block_without_cause", "S_recover_lock", "BlockedOnlyForCause", S(BootLink=False, BootOrphan=False)),
    ("installer_ignores_journal", "S_quit", "InstallerNeverOverOpenJournal", {}),
    ("save_waits_r4_forever", "LS_jobs", "EveryWaitedForJobEnds", S(MaxSaves=2, MaxBg=0, MaxReads=0,
                                                                   MaxSwitch=0, MaxCrash=0)),
    ("switch_waits_for_r4", "LS_jobs", "EverySwitchEnds", S(MaxBg=0, MaxReads=0, MaxCrash=0)),
    ("quit_waits_reap", "LS_quit", "EveryQuitEnds", {}),
    ("cnc_auto_lift", "S_r4_attempts", "R4GateLiftedOnlyByUser", S(MaxSaves=3)),
    ("r4_skipped_before_save", "S_r4_outcomes", "OwedR4BeforeNextSave", {}),
]


def world_of(name):
    if name in WORLDS:
        tier, w = WORLDS[name]
        return tier, w, "Spec", None
    tier, w, spec, props = LIVE[name]
    return tier, w, spec, props


def render(name, consts, spec, invariants, properties, comment):
    lines = ["\\* generated by gen_cfgs.py: %s" % name, "\\* %s" % comment,
             "SPECIFICATION %s" % spec, "CONSTANTS"]
    for k in DEFAULTS:
        lines.append("    %s = %s" % (k, consts[k]))
    for i in invariants:
        lines.append("INVARIANT %s" % i)
    for p in properties:
        lines.append("PROPERTY %s" % p)
    lines.append("CHECK_DEADLOCK FALSE")
    return "\n".join(lines) + "\n"


def build():
    out = {}
    rows = []
    for name in list(WORLDS) + list(LIVE):
        tier, w, spec, live = world_of(name)
        consts = dict(DEFAULTS)
        consts.update(w)
        if live is None:
            invs, props = INVARIANTS, SAFETY_PROPS
            comment = "safety: every state invariant and temporal safety property of section 13"
        else:
            invs, props = ["JobIdsBounded"], live
            comment = "liveness under %s" % spec
        out[name + ".cfg"] = render(name, consts, spec, invs, props, comment)
        rows.append((name, "PASS", tier, "base"))
    for mut, base, prop, extra in MUTANTS:
        tier, w, spec, live = world_of(base)
        consts = dict(DEFAULTS)
        consts.update(w)
        consts.update(extra)
        consts["MUTANT"] = '"%s"' % mut
        if prop in INVARIANTS:
            invs, props = [prop], []
        else:
            invs, props = [], [prop]
        name = "MUT_" + mut
        out[name + ".cfg"] = render(name, consts, spec, invs, props,
                                    "mutant %s on %s: TLC must report %s violated" % (mut, base, prop))
        rows.append((name, "VIOLATE:" + prop, "S", "mutant"))
    for fname, base, prop, extra in FINDINGS:
        tier, w, spec, live = world_of(base)
        consts = dict(DEFAULTS)
        consts.update(w)
        consts.update(extra)
        invs, props = ([prop], []) if prop in INVARIANTS else ([], [prop])
        out[fname + ".cfg"] = render(fname, consts, spec, invs, props,
                                     "finding: the spec as written; TLC must report %s violated" % prop)
        rows.append((fname, "VIOLATE:" + prop, "S", "finding"))
    lst = ["# Generated by gen_cfgs.py; do not edit by hand.",
           "# <config> <expected: PASS | VIOLATE:<property>> <tier: S | M | L> <kind: base | mutant | finding>"]
    for r in rows:
        lst.append("%-36s %-48s %s %s" % r)
    out["configs.txt"] = "\n".join(lst) + "\n"
    return out


def main():
    check = "--check" in sys.argv
    files = build()
    os.makedirs(CFG_DIR, exist_ok=True)
    stale = []
    for fname, text in files.items():
        path = os.path.join(HERE, fname) if fname == "configs.txt" else os.path.join(CFG_DIR, fname)
        old = open(path).read() if os.path.exists(path) else None
        if old != text:
            stale.append(fname)
            if not check:
                with open(path, "w") as f:
                    f.write(text)
    extra = sorted(set(os.listdir(CFG_DIR)) - {f for f in files if f.endswith(".cfg")})
    if check:
        if stale or extra:
            print("out of date: " + ", ".join(stale + extra))
            return 1
        print("configs up to date (%d files)" % len(files))
        return 0
    for f in extra:
        os.remove(os.path.join(CFG_DIR, f))
    print("wrote %d files (%d changed, %d removed)" % (len(files), len(stale), len(extra)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
