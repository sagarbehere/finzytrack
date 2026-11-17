"""The reasons a test skips on Windows until the Windows port lands (S0127, BL-0279; ADR-0069's split), each naming the
backlog item that turns it on. Written as the macOS-only skip is (`tests/macos_only.py`), so pyright checks the code
after it for the other platforms only:

    if sys.platform == "win32":
        raise pytest.skip.Exception(BL_0280, allow_module_level=True)  # a whole module, before its POSIX imports

and the same `raise` (without `allow_module_level`) inside one test. The engine-windows CI job runs pytest with `-rs`,
so each skip prints its one line there.
"""

from __future__ import annotations

from typing import Final

BL_0280: Final = "BL-0280: the real-disk write path on Windows (the engine refuses file operations until its adapter)"
BL_0281: Final = "BL-0281: the engine's process group and signals on Windows (the Job Object)"
NO_FIFO: Final = "Windows has no FIFO in its file system: the non-blocking read it guards against cannot arise there"
NO_UID: Final = "Windows has no POSIX user and group ids or permission bits on files"
BL_0282: Final = "BL-0282: Beancount's own paths, globs and messages on Windows (the engine's read side there)"

POSIX_PATH_MODULES: Final = frozenset({"test_journal_record.py", "test_copy_layout.py"})
"""Pure tests written in POSIX paths: on Windows they run with the POSIX path model, as the tests over the fake do
(`tests/conftest.py`); the Windows model has its own (`tests/fs/test_pathmodel.py`, `tests/ledger/
test_journal_windows.py`, the Windows case of `test_copy_layout.py`, which simulates it inside)."""

PENDING: Final = {
    # module: (reason, the tests that fail on the Windows runner; the 2026-11-17 run 3, S0127)
    "test_contract.py": (
        BL_0282,
        frozenset(
            {
                "test_a_booking_error_is_listed_and_never_refuses",
                "test_a_path_that_is_not_absolute_and_normal_is_refused",
                "test_a_plugin_error_is_listed_and_never_refuses",
                "test_a_recorded_hash_that_is_not_the_candidate_s_is_refused",
                "test_a_second_copy_of_an_old_error_is_new",
                "test_an_insert_a_delete_and_a_token_edit_in_one_plan_are_prepared",
                "test_an_old_error_is_not_new_after_a_splice_shifts_its_line",
            }
        ),
    ),
    "test_copy.py": (
        BL_0282,
        frozenset(
            {
                "test_a_journal_that_is_not_open_blocks_nothing",
                "test_a_ledger_with_its_own_plugin_that_loads_the_same_is_copied",
                "test_a_lock_named_twice_is_tried_once",
                "test_an_open_journal_of_another_file_blocks_nothing",
                "test_the_outcome_of_a_copy_is_ours_only_for_our_manifest",
            }
        ),
    ),
    "test_create_conformance.py": (
        BL_0282,
        frozenset(
            {
                "test_the_engine_copy_follows_the_model",
                "test_the_replay_walks_the_create_and_the_copy",
                "test_the_stops_are_counted",
            }
        ),
    ),
    "test_fingerprint.py": (
        BL_0282,
        frozenset(
            {
                "test_a_dotdot_through_a_link_observes_unchanged",
                "test_a_file_appearing_behind_a_dotdot_through_a_link_changes_it",
            }
        ),
    ),
    "test_ledger_set.py": (
        BL_0282,
        frozenset(
            {
                "test_a_document_a_plugin_makes_is_recorded",
                "test_a_document_directive_keeps_the_answer_asked_before_the_plugins",
                "test_a_documents_plugin_listed_with_a_config_checks_nothing",
                "test_a_glob_that_matches_nothing_is_recorded_with_no_matches",
                "test_a_plugin_document_beancount_never_checks_is_not_recorded",
            }
        ),
    ),
    "test_overlay.py": (BL_0282, frozenset({"test_a_plugin_that_decrypts_while_it_is_imported_refuses_the_load"})),
    "test_parity_corpus.py": (
        BL_0282,
        frozenset(
            {
                "test_a_false_claim_an_unknown_feature_and_an_unclaimed_one_are_found",
                "test_a_feature_a_case_has_but_does_not_claim_is_found",
                "test_beancount_gives_each_load_case_the_errors_it_is_written_for",
                "test_every_feature_is_claimed_and_found_where_it_is_claimed",
            }
        ),
    ),
    "test_plugin_fingerprint.py": (
        BL_0282,
        frozenset(
            {
                "test_a_plugin_reads_its_package_data_as_under_beancount",
                "test_every_load_that_runs_plugins_records_its_imports",
            }
        ),
    ),
    "test_precision.py": (BL_0282, frozenset({"test_a_carry_past_28_digits_warns"})),
    "test_write_sequence_model.py": (
        BL_0282,
        frozenset(
            {
                "test_each_job_s_steps_fit_its_crash_range",
                "test_inv_history_fails_on_a_lost_backup_and_a_lost_record",
                "test_inv_recovered_fails_on_an_open_journal_recovery_did_not_report",
                "test_inv_saved_kept_fails_when_a_saved_change_is_gone",
                "test_the_history_keeps_the_newest_16_over_18_saves",
                "test_the_sequence_model_holds_and_reaches_every_rule",
            }
        ),
    ),
    "test_checksums.py": (
        BL_0282,
        frozenset(
            {
                "test_the_case_folder_becomes_a_placeholder_in_every_text_value",
                "test_the_same_rows_in_another_order_or_folder_give_the_same_checksums",
            }
        ),
    ),
    "test_no_resident_ledger.py": (
        BL_0280,
        frozenset(
            {
                "test_a_cache_on_the_engine_object_is_seen",
                "test_a_kept_load_result_is_seen",
                "test_no_directive_outlives_its_job",
                "test_one_directive_kept_in_an_engine_module_is_seen",
            }
        ),
    ),
}
"""The tests that fail on the Windows runner for a reason a later slice owns, each skipped there by name
(`tests/conftest.py`): an inventory from one run, so a test that starts failing on Windows later is not hidden."""
