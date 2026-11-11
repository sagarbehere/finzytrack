#!/usr/bin/env python3
"""Write every TLC configuration of the engine-jobs model, and configs.txt.

Run from anywhere: python3 gen_cfgs.py (formal/tla/engine-jobs). The .cfg files and configs.txt it writes are committed;
edit this table, not the files (a test of the folder checks they are in step: --check).

A configuration = a world (constants), the specification (Spec), the properties to check and the result it
must have. Durability only since S0072 (ADR-0068 D5): no liveness worlds. Tiers: S (small, seconds to minutes), M (medium),
L (large). Every mutant runs on a small world and checks only the property it must break.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_DIR = os.path.join(HERE, "cfg")

DEFAULTS = {
    "MUTANT": '"none"', "BootJ": "{}", "BootTemps": "FALSE", "BootTorn": "FALSE", "BootLink": "FALSE",
    "EdFiles": "{}", "EdDurable": "TRUE",
    "MaxSaves": "0", "MaxEdits": "0", "MaxCrash": "0", "MaxAppCrash": "0", "MaxPower": "0", "MaxStarts": "0",
    "MaxUnreach": "0", "MaxLink": "0", "MaxIO": "0", "MaxDrop": "0",
    "DoCopy": "FALSE", "CreateTarget": '"none"',
}

# State invariants (INVARIANT) and temporal safety formulas (PROPERTY) of §13: durability only (EP-19, S0072).
INVARIANTS = [
    "SavedSurvivesPowerLoss", "BackupDurableBeforeReplace", "JournalDurableBeforeFirstReplace",
    "PartJournalNeverBlocks", "ExternalEditNeverSilentlyLost",
    "RecoveryNeverClobbersForeignBytes", "RecoveryWritesOnlyRecordedPaths",
    "EveryJournalRecoveredOrBlocking", "NoCopyOverOpenJournal",
    "NoManifestOverIncompleteFolder", "NonEmptyTargetUntouched", "DroppedFileNeverReplaced",
    "ForeignOverwriteCaughtByReadBack",
]
SAFETY_PROPS = ["NoTornLedgerAfterRecovery", "CutTempNeverBlocks", "BlockedOnlyForCause", "KeptFilesDurable"]


def S(**kw):
    return {k: (str(v).upper() if isinstance(v, bool) else str(v)) for k, v in kw.items()}


def strs(*xs):
    return "{" + ", ".join('"%s"' % x for x in xs) + "}"


# name: (tier, world overrides)
WORLDS = {
    # --- the save (§6.1-§6.6, §10) ---
    "S_save_crash": ("S", S(MaxSaves=1, MaxCrash=1, MaxIO=1, MaxEdits=1, EdFiles=strs("R", "N", "I"),
                            MaxStarts=1, MaxAppCrash=1)),
    "S_save_power": ("S", S(MaxSaves=1, MaxPower=1, MaxStarts=1, MaxEdits=1, EdFiles=strs("R"))),
    # two saves: the second replaces the file the first created, so its rollback restores both
    "S_save_two": ("S", S(MaxSaves=2, MaxCrash=1, MaxIO=1, MaxPower=1, MaxStarts=1)),
    # an in-session journal left .open, recovered in the session, kept, then a power loss
    "S_keep": ("S", S(MaxSaves=2, MaxCrash=1, MaxIO=1, MaxEdits=1, EdFiles=strs("R"), MaxPower=1, MaxStarts=1)),
    # --- recover-all (§8.1) ---
    "S_recover": ("S", S(BootJ=strs("b2", "b3"), MaxCrash=1, MaxUnreach=1, MaxEdits=1, EdFiles=strs("R"))),
    # the path guard, and what a crashed save left beside the files
    "S_recover_link": ("S", S(BootJ=strs("b2"), BootTemps=True, BootLink=True, MaxStarts=1, MaxAppCrash=1)),
    "S_recover_power": ("S", S(BootJ=strs("b2"), MaxPower=1, MaxStarts=1, MaxIO=1, MaxEdits=1,
                               EdFiles=strs("R"), MaxLink=1)),
    # a journal whose file list cannot be read blocks every copy; Keep
    "S_recover_torn": ("S", S(BootJ=strs("b3"), BootTorn=True, DoCopy=True)),
    # --- copy and create (§8.2) ---
    "S_copy": ("S", S(BootJ=strs("b2"), DoCopy=True, MaxSaves=1, MaxCrash=1)),
    "S_create_absent": ("S", S(CreateTarget='"absent"', MaxDrop=2, MaxCrash=1, MaxAppCrash=1, MaxStarts=1)),
    "S_create_empty": ("S", S(CreateTarget='"empty"', MaxDrop=2, MaxPower=1, MaxStarts=1)),
    "S_create_nonempty": ("S", S(CreateTarget='"nonempty"', MaxDrop=1)),
    # --- §13's file-system assumptions: an outside save not durable, each unsynced operation of ours
    #     kept or dropped on its own ---
    "S_fs_weak": ("S", S(EdDurable=False, MaxSaves=1, MaxPower=1, MaxStarts=1, MaxIO=1, MaxEdits=2,
                         EdFiles=strs("R", "I"))),
    "S_fs_weak_recover": ("S", S(EdDurable=False, BootJ=strs("b2"), BootTemps=True, MaxPower=1,
                                 MaxStarts=1, MaxEdits=1, EdFiles=strs("R"), MaxIO=1)),
    # --- medium and large: actors combined ---
    "M_recover_temps": ("M", S(BootJ=strs("b2", "b3"), BootTemps=True, MaxCrash=1, MaxUnreach=1, MaxEdits=1,
                               EdFiles=strs("R"))),
    "L_save_all": ("L", S(MaxSaves=1, MaxCrash=1, MaxIO=1, MaxEdits=2, EdFiles=strs("R", "N", "I"),
                          MaxPower=1, MaxStarts=2, MaxAppCrash=1)),
    "L_recover_all": ("L", S(BootJ=strs("b2", "b3"), BootTemps=True, MaxCrash=2, MaxUnreach=1, MaxEdits=1,
                             EdFiles=strs("R", "J", "K"), DoCopy=True, MaxPower=1, MaxStarts=1)),
    "L_save_deep": ("L", S(MaxSaves=2, MaxCrash=2, MaxIO=1, MaxEdits=3, EdFiles=strs("R", "N", "I"),
                           MaxPower=2, MaxStarts=3, MaxAppCrash=1)),
    # the whole world at once is beyond exhaustive checking: a bounded random simulation (mode "simulate")
    "L_world": ("L", S(BootJ=strs("b2", "b3"), BootTemps=True, MaxSaves=3, MaxEdits=2,
                       EdFiles=strs("R", "N", "I", "K"), MaxCrash=2, MaxAppCrash=1, MaxPower=1, MaxStarts=2,
                       MaxUnreach=1, MaxIO=1, MaxLink=1, DoCopy=True, CreateTarget='"empty"', MaxDrop=1)),
}

# mutant: (world it runs on, property it must break, extra overrides). Durability only since S0072 (ADR-0068 D5):
# MAPPING.md §6 lists each mutant, and §5 each one that left with the reason.
MUTANTS = [
    # the save
    ("report_before_close_sync", "S_save_power", "SavedSurvivesPowerLoss", {}),
    ("outcome_unsynced", "S_save_power", "SavedSurvivesPowerLoss", S(MaxCrash=1, MaxEdits=0, EdFiles="{}")),
    ("keep_no_sync", "S_keep", "KeptFilesDurable", {}),
    ("no_backup_sync", "S_save_crash", "BackupDurableBeforeReplace", {}),
    ("no_journal_sync", "S_save_crash", "JournalDurableBeforeFirstReplace", {}),
    ("direct_open", "S_save_power", "PartJournalNeverBlocks", {}),
    ("no_displaced_check", "S_save_crash", "ExternalEditNeverSilentlyLost", {}),
    ("plain_create", "S_save_crash", "ExternalEditNeverSilentlyLost", S(EdFiles=strs("N"))),
    ("rollback_skips_temp_rule", "S_save_crash", "ExternalEditNeverSilentlyLost",
     S(MaxCrash=0, MaxAppCrash=0, MaxStarts=0, EdFiles=strs("R"))),
    # verifier S0072 r1 F4: the save keeps the outside save it met at the swap, then carries on to "saved"
    ("x_keep_and_go_on", "S_save_crash", "ExternalEditNeverSilentlyLost",
     S(MaxCrash=0, MaxAppCrash=0, MaxStarts=0, EdFiles=strs("R"))),
    ("no_temp_fsync", "S_save_power", "NoTornLedgerAfterRecovery", {}),
    ("temps_after_close", "S_save_crash", "CutTempNeverBlocks", {}),
    # recovery
    ("no_temp_check", "S_save_crash", "RecoveryNeverClobbersForeignBytes", {}),
    ("no_path_guard", "S_recover_link", "RecoveryWritesOnlyRecordedPaths", {}),
    ("fb_no_guard", "S_recover_link", "RecoveryWritesOnlyRecordedPaths", {}),
    ("resolve_before_restores_synced", "S_recover_power", "NoTornLedgerAfterRecovery", {}),
    ("no_restore_temp_fsync", "S_recover_power", "NoTornLedgerAfterRecovery", {}),
    # S0101 (BL-0243): the restore resolves without flushing the data of a file found at pre (an unsynced revert)
    ("rs_no_file_sync", "S_fs_weak_recover", "NoTornLedgerAfterRecovery", {}),
    ("old_temp_rule", "S_recover_link", "CutTempNeverBlocks", S(BootLink=False)),
    ("block_without_cause", "S_recover_link", "BlockedOnlyForCause", S(BootLink=False)),
    ("crash_unmarked", "S_recover", "EveryJournalRecoveredOrBlocking", {}),
    ("rv_boot_skips_recovery", "S_recover", "EveryJournalRecoveredOrBlocking", S(BootJ=strs("b3"))),
    ("copy_checks_blocking_only", "S_copy", "NoCopyOverOpenJournal", {}),
    # create
    ("manifest_first", "S_create_empty", "NoManifestOverIncompleteFolder", {}),
    ("rv_create_no_sync", "S_create_empty", "NoManifestOverIncompleteFolder", {}),
    ("no_refuse_nonempty", "S_create_nonempty", "NonEmptyTargetUntouched", {}),
    ("plain_file_write", "S_create_absent", "DroppedFileNeverReplaced", {}),
    ("manifest_overwrite", "S_create_empty", "DroppedFileNeverReplaced", {}),
    ("no_readback", "S_create_absent", "ForeignOverwriteCaughtByReadBack", {}),
]


# witness: (the predicate whose negation a base world must reach, the world). Vacuity (MAPPING.md §6.2): each
# configuration checks the witness as an invariant and must report it violated, so a base world that passes does
# so with the branch reached.
WITNESSES = [
    ("W_Saved", "S_save_crash"),
    ("W_RollbackResolved", "S_save_crash"),
    ("W_RollbackUnfinished", "S_save_crash"),
    ("W_KeptCopySave", "S_save_crash"),
    ("W_CreatedPathTaken", "S_save_crash"),
    ("W_RollbackPutsBack", "S_save_crash"),
    ("W_RecoverResolves", "S_recover"),
    ("W_RecoverKeptCopy", "S_recover"),
    ("W_RecoverFailed", "S_recover"),
    ("W_Keep", "S_keep"),
    ("W_KeepTorn", "S_recover_torn"),
    ("W_CutStagingDeleted", "S_save_power"),
    ("W_StagingCutSeen", "S_save_power"),
    ("W_CopyDone", "S_copy"),
    ("W_CopyRefusedInsession", "S_copy"),
    ("W_CreateDone", "S_create_absent"),
    ("W_SecondSaved", "S_save_two"),
    ("W_SecondRollbackTwoFiles", "S_save_two"),
    ("W_PowerAfterSaveRecovered", "S_save_power"),
    ("W_LinkStop", "S_recover_link"),
    ("W_WeakLossAccepted", "S_fs_weak"),
    ("W_WeakRecoverKept", "S_fs_weak_recover"),
    ("W_RevertFlushed", "S_fs_weak_recover"),
    ("W_EditorAfterGate", "S_save_crash"),
]


SIMULATE = {"L_world"}   # run with TLC -simulate (a bounded random walk), not exhaustively


def world_of(name):
    tier, w = WORLDS[name]
    return tier, w, "Spec", None


def render(name, consts, spec, invariants, properties, comment):
    lines = ["\\* generated by gen_cfgs.py: %s" % name, "\\* %s" % comment,
             "SPECIFICATION %s" % spec, "CONSTANTS"]
    for k in DEFAULTS:
        lines.append("    %s = %s" % (k, consts[k]))
    for i in invariants:
        lines.append("INVARIANT %s" % i)
    for p in properties:
        lines.append("PROPERTY %s" % p)
    # The app may legitimately end with nothing left to do (no restart left), which TLC would call a
    # deadlock.
    lines.append("CHECK_DEADLOCK FALSE")
    return "\n".join(lines) + "\n"


def build():
    out = {}
    rows = []
    for name in WORLDS:
        tier, w, spec, live = world_of(name)
        invs, props = INVARIANTS, SAFETY_PROPS
        comment = "every durability invariant and temporal safety property of section 13"
        mode = "simulate" if name in SIMULATE else "check"
        consts = dict(DEFAULTS)
        consts.update(w)
        out[name + ".cfg"] = render(name, consts, spec, invs, props, comment)
        rows.append((name, "PASS", tier, "base", mode))
    for m in MUTANTS:
        mut, base, prop, extra = m[:4]
        suffix = ("_" + m[4]) if len(m) > 4 else ""
        tier, w, spec, live = world_of(base)
        consts = dict(DEFAULTS)
        consts.update(w)
        consts.update(extra)
        consts["MUTANT"] = '"%s"' % mut
        if prop in INVARIANTS:
            invs, props = [prop], []
        else:
            invs, props = [], [prop]
        name = "MUT_" + mut + suffix
        out[name + ".cfg"] = render(name, consts, spec, invs, props,
                                    "mutant %s on %s: TLC must report %s violated" % (mut, base, prop))
        rows.append((name, "VIOLATE:" + prop, "S", "mutant", "check"))
    for wit, base in WITNESSES:
        tier, w, spec, live = world_of(base)
        consts = dict(DEFAULTS)
        consts.update(w)
        name = "WIT_" + wit[2:]
        out[name + ".cfg"] = render(name, consts, spec, [wit], [],
                                    "witness %s on %s: TLC must report it violated (the branch is reached)" % (wit, base))
        rows.append((name, "VIOLATE:" + wit, "S", "witness", "check"))
    lst = ["# Generated by gen_cfgs.py; do not edit by hand.",
           "# <config> <expected: PASS | VIOLATE:<property>> <tier: S | M | L> <kind: base | mutant | witness> <mode: check | simulate>"]
    for r in rows:
        lst.append("%-36s %-52s %s %-6s %s" % r)
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
