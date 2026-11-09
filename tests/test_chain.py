"""Per-field provider chain."""
from resources.lib.engine.chain import TRANSIENT, FieldOutcome, Step, run_chain


class Probe:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value


def _step(value, fallback=False):
    probe = Probe(value)
    return Step(fetch=probe, fallback=fallback), probe


def test_first_real_value_wins_and_later_steps_not_called():
    first, p1 = _step("Sterrenkijken")
    second, p2 = _step("Other")
    out = run_chain([first, second], None)
    assert out == FieldOutcome(transient=False, value="Sterrenkijken", from_fallback=False)
    assert p1.calls == 1 and p2.calls == 0


def test_transient_stops_chain_no_write():
    first, _ = _step(TRANSIENT)
    second, p2 = _step("English", fallback=True)
    out = run_chain([first, second], None)
    assert out == FieldOutcome(transient=True, value=None, from_fallback=False)
    assert p2.calls == 0


def test_empty_and_placeholder_fall_through():
    first, _ = _step("Aflevering 3")
    second, _ = _step("Geknoei")
    out = run_chain([first, second], None)
    assert out.value == "Geknoei" and not out.transient


def test_whitespace_and_dash_are_empty():
    steps = [_step("   ")[0], _step("-")[0], _step(None)[0], _step("Echt")[0]]
    assert run_chain(steps, None).value == "Echt"


def test_fallback_fills_empty_current():
    step, _ = _step("English", fallback=True)
    assert run_chain([step], None).value == "English"
    assert run_chain([step], "  ").value == "English"


def test_fallback_fills_placeholder_current():
    step, _ = _step("English", fallback=True)
    assert run_chain([step], "Aflevering 3").value == "English"


def test_fallback_never_replaces_real_current():
    step, probe = _step("English", fallback=True)
    out = run_chain([step], "Sterrenkijken")
    assert out == FieldOutcome(transient=False, value=None, from_fallback=False)
    assert probe.calls == 0


def test_all_empty_is_answered_empty():
    out = run_chain([_step(None)[0], _step("-", fallback=True)[0]], None)
    assert out == FieldOutcome(transient=False, value=None, from_fallback=False)


def test_fallback_value_marks_from_fallback():
    out = run_chain([_step(None)[0], _step("English", fallback=True)[0]], "")
    assert out == FieldOutcome(transient=False, value="English", from_fallback=True)


def test_non_text_values_pass_through():
    assert run_chain([_step([])[0], _step(["Drama"])[0]], None).value == ["Drama"]
    assert run_chain([_step(None)[0]], None).value is None


def test_no_steps_is_answered_empty():
    assert run_chain([], None) == FieldOutcome(False, None, False)


def test_transient_fallback_ignored_when_current_is_real():
    step, probe = _step(TRANSIENT, fallback=True)
    out = run_chain([step], "Sterrenkijken")
    assert out == FieldOutcome(transient=False, value=None, from_fallback=False)
    assert probe.calls == 0


def test_transient_fallback_is_transient_when_current_is_empty():
    step, _ = _step(TRANSIENT, fallback=True)
    assert run_chain([step], None).transient is True


def test_skipped_fallback_does_not_block_later_native_step():
    fallback, _ = _step("English", fallback=True)
    native, _ = _step("Nederlands")
    out = run_chain([fallback, native], "Sterrenkijken")
    assert out == FieldOutcome(transient=False, value="Nederlands", from_fallback=False)
