"""Exercise the shipped node output component with real DOM and journal deltas."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = (
    "real_delta_appends_without_replacing_output_nodes",
    "identical_snapshots_do_not_write_text_or_rebuild_tabs",
    "full_completed_output_is_immediate_and_not_quality_approval",
    "manual_request_selection_stays_fixed_until_follow_latest",
    "scrolling_up_pauses_and_resume_scrolls_to_real_latest_text",
    "context_switch_clears_old_task_and_node_content",
    "reasoning_is_separate_and_user_collapse_survives_deltas",
    "retry_and_nonprefix_snapshots_never_mix_answers",
    "interrupted_and_partial_status_are_honest",
    "missing_pinned_request_never_shows_a_different_answer",
    "untrusted_content_and_foreign_messages_are_inert",
    "labels_keyboard_navigation_and_frame_height_are_stable",
    "journal_deltas_accumulate_full_output_in_stable_text_nodes",
    "duplicate_and_old_delta_polls_never_replay_tokens_or_terminal_status",
    "new_mount_with_nonzero_cursor_requests_prefix_and_preserves_manual_selection",
    "cursor_gaps_overlaps_and_invalid_events_resync_instead_of_mixing",
    "selection_ack_rejects_late_snapshots_and_follow_latest_can_resume",
    "switching_away_and_back_reloads_prefix_without_an_old_request_cache",
    "context_change_drops_pending_selection_and_reused_id_requires_prefix",
    "terminal_metadata_waits_until_all_real_events_have_been_read",
    "legacy_journal_fallback_shows_real_snapshot_and_partial_warning",
    "delta_scroll_pause_survives_new_tokens_and_reasoning_toggle",
    "missing_delta_request_clears_cursor_and_requests_prefix_when_it_returns",
    "consumed_cursor_receipt_is_exact_and_retries_are_throttled_without_replay",
    "receipt_does_not_lock_selection_and_uses_the_accepted_selection_epoch",
    "empty_terminal_delta_completes_and_invalid_cursors_never_get_receipts",
    "reader_height_override_restores_default_without_touching_pinned_content",
    "reader_height_changes_preserve_delta_prefix_request_pin_and_receipts",
)


@pytest.fixture(scope="module")
def results():
    node = shutil.which("node")
    if not node or not (ROOT / "node_modules" / "jsdom").is_dir():
        pytest.skip("Node and installed jsdom are required")
    process = subprocess.run(
        [node, str(ROOT / "tests/workflow_stream_component_harness.mjs")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    lines = [line for line in process.stdout.splitlines() if line.startswith("{")]
    assert lines, process.stdout + process.stderr
    observed = json.loads(lines[-1])["scenarios"]
    assert set(observed) == set(SCENARIOS)
    return observed


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_node_stream_interaction(results, scenario):
    assert results[scenario]["ok"], results[scenario].get("error", scenario)
