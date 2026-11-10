from resources.lib.constants import FALLBACK_RECHECKS
from resources.lib.engine.maturity import (
    FETCH_NOW, SKIP_NOT_DUE, FINAL,
    apply_fallback_recheck, compute_next_due, decide_field, fallback_recheck_due,
)

THRESH = 365


def test_first_encounter_fetches():
    assert decide_field(None, 0, None, "2026-06-19", THRESH) == FETCH_NOW


def test_old_content_after_one_sample_is_final():
    # content_age 400 >= 365, has been sampled once
    assert decide_field(400, 1, None, "2026-06-19", THRESH) == FINAL


def test_young_on_schedule_due():
    assert decide_field(50, 1, "2026-06-19", "2026-06-19", THRESH) == FETCH_NOW


def test_young_on_schedule_not_due():
    assert decide_field(50, 1, "2026-12-01", "2026-06-19", THRESH) == SKIP_NOT_DUE


def test_next_due_on_schedule_uses_interval():
    # fetch_count 1 -> schedule[0]=7, fixed jitter 0
    assert compute_next_due(1, (7, 30, 90), "2026-01-01", THRESH, "2026-06-19", jitter=0) == "2026-06-26"


def test_next_due_settle_capture_at_threshold():
    # schedule exhausted (fetch_count 4 > len 3) -> premiered + 365
    assert compute_next_due(4, (7, 30, 90), "2026-01-01", THRESH, "2026-06-19") == "2027-01-01"


# --- Fallback recheck (spec section 8) ---

TODAY = "2026-06-19"


def test_fallback_final_rechecked_until_limit():
    # Due (next_retry reached) -> fetched; not yet due -> skipped, not final.
    for checks in range(FALLBACK_RECHECKS):
        assert apply_fallback_recheck(FINAL, True, checks, "2026-06-19", TODAY) == FETCH_NOW
        assert apply_fallback_recheck(FINAL, True, checks, "2026-07-19", TODAY) == SKIP_NOT_DUE


def test_fallback_without_retry_date_is_due():
    assert apply_fallback_recheck(FINAL, True, 0, None, TODAY) == FETCH_NOW


def test_fallback_after_limit_is_final():
    assert apply_fallback_recheck(FINAL, True, FALLBACK_RECHECKS, "2026-01-01", TODAY) == FINAL
    assert apply_fallback_recheck(FINAL, True, FALLBACK_RECHECKS + 3, None, TODAY) == FINAL


def test_non_fallback_final_unchanged():
    assert apply_fallback_recheck(FINAL, False, 0, "2026-01-01", TODAY) == FINAL


def test_non_final_decisions_pass_through():
    for decision in (FETCH_NOW, SKIP_NOT_DUE):
        assert apply_fallback_recheck(decision, True, 0, "2099-01-01", TODAY) == decision


def test_fallback_recheck_due_is_a_month_out_when_schedule_is_past_or_none():
    assert fallback_recheck_due(None, TODAY) == "2026-07-19"
    assert fallback_recheck_due("2025-01-01", TODAY) == "2026-07-19"
    assert fallback_recheck_due(TODAY, TODAY) == "2026-07-19"


def test_fallback_recheck_due_never_later_than_a_month():
    assert fallback_recheck_due("2027-01-01", TODAY) == "2026-07-19"


def test_fallback_recheck_due_keeps_an_earlier_schedule_date():
    assert fallback_recheck_due("2026-06-26", TODAY) == "2026-06-26"


def test_decide_certification():
    from resources.lib.constants import FALLBACK_RECHECKS
    from resources.lib.engine.maturity import (
        FETCH_NOW,
        FINAL,
        SKIP_NOT_DUE,
        decide_certification,
    )

    today = "2026-11-10"
    assert decide_certification(0, False, 0, None, today) == FETCH_NOW
    assert decide_certification(1, False, 0, "2000-01-01", today) == FINAL  # real answer
    assert decide_certification(1, True, 3, "2026-11-01", today) == FETCH_NOW
    assert decide_certification(1, True, 3, "2026-12-01", today) == SKIP_NOT_DUE
    assert decide_certification(5, True, FALLBACK_RECHECKS, "2026-11-01", today) == FINAL
    assert decide_certification(1, True, 0, None, today) == FETCH_NOW
