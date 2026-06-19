from resources.lib.engine.maturity import (
    FETCH_NOW, SKIP_NOT_DUE, FINAL, FINAL_EMPTY,
    decide_field, compute_next_due,
)

THRESH = 365


def test_first_encounter_fetches():
    assert decide_field(None, 0, None, "2026-06-19", THRESH, False) == FETCH_NOW


def test_structural_absence_is_final_empty():
    assert decide_field(10, 0, None, "2026-06-19", THRESH, True) == FINAL_EMPTY


def test_old_content_after_one_sample_is_final():
    # content_age 400 >= 365, has been sampled once
    assert decide_field(400, 1, None, "2026-06-19", THRESH, False) == FINAL


def test_young_on_schedule_due():
    assert decide_field(50, 1, "2026-06-19", "2026-06-19", THRESH, False) == FETCH_NOW


def test_young_on_schedule_not_due():
    assert decide_field(50, 1, "2026-12-01", "2026-06-19", THRESH, False) == SKIP_NOT_DUE


def test_next_due_on_schedule_uses_interval():
    # fetch_count 1 -> schedule[0]=7, fixed jitter 0
    assert compute_next_due(1, (7, 30, 90), "2026-01-01", THRESH, "2026-06-19", jitter=0) == "2026-06-26"


def test_next_due_settle_capture_at_threshold():
    # schedule exhausted (fetch_count 4 > len 3) -> premiered + 365
    assert compute_next_due(4, (7, 30, 90), "2026-01-01", THRESH, "2026-06-19") == "2027-01-01"
