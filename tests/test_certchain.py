"""Tests for the certification chain and the per-cycle site gate."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from resources.lib.data.presets import load_preset
from resources.lib.engine import certchain
from resources.lib.engine.breaker import CircuitBreaker
from resources.lib.engine.certchain import CertQuery, SiteGate, resolve_certification
from resources.lib.engine.chain import TRANSIENT
from resources.lib.utils import ApiKeyError, TransientProviderError


def _site(name, answers, media=("movie", "tvshow")):
    """A fake site module; answers maps title -> rating, or an exception."""
    calls = []

    def lookup(title, original_title, year, media_type, imdb_id=None, pace=None):
        # Like a real site module: every request is paced first.
        if pace is not None and not pace():
            raise TransientProviderError("paced out")
        calls.append(title)
        answer = answers.get(title) if isinstance(answers, dict) else answers
        if isinstance(answer, Exception):
            raise answer
        return answer

    return SimpleNamespace(SITE=name, MEDIA_TYPES=frozenset(media), lookup=lookup, calls=calls)


@pytest.fixture
def sites(monkeypatch):
    fakes = {"fsk": _site("fsk", {}), "bbfc": _site("bbfc", {}),
             "medieraadet": _site("medieraadet", {}, media=("movie",))}
    monkeypatch.setattr(certchain, "SITES", fakes)
    return fakes


class _Monitor:
    def __init__(self, abort=False, abort_after=None):
        self.abort = abort
        self.abort_after = abort_after  # abortRequested() turns True after N calls
        self.checks = 0
        self.waits = []

    def waitForAbort(self, seconds):
        self.waits.append(seconds)
        return self.abort

    def abortRequested(self):
        self.checks += 1
        return self.abort_after is not None and self.checks > self.abort_after


def _gate(enabled=("fsk", "bbfc", "medieraadet"), monitor=None, **kw):
    return SiteGate(frozenset(enabled), CircuitBreaker(), monitor or _Monitor(),
                    min_interval=0.0, **kw)


def _q(title="Film", certs=None, media="movie", year=2014):
    return CertQuery(title=title, original_title="", year=year, media_type=media,
                     imdb_id=None, tmdb_certs={} if certs is None else certs)


NL = load_preset("NL")
GB = load_preset("GB")
DK = load_preset("DK")


def test_tmdb_direct_is_real_and_asks_no_site(sites):
    gate = _gate()
    answer = resolve_certification(_q(certs={"NL": "12", "US": "R"}), NL, gate)
    assert (answer.kind, answer.rating, answer.source) == ("real", "12", "tmdb")
    assert gate.requests == 0


def test_native_site_before_tmdb_mapping(sites):
    sites["bbfc"] = _site("bbfc", {"Film": "15"})
    certchain.SITES.update(sites)
    answer = resolve_certification(_q(certs={"US": "PG-13"}), GB, _gate())
    assert (answer.kind, answer.rating, answer.source) == ("real", "15", "bbfc")


def test_tmdb_mapping_without_io(sites):
    gate = _gate()
    answer = resolve_certification(_q(certs={"US": "PG-13"}), NL, gate)
    assert (answer.kind, answer.rating, answer.source) == ("derived", "12", "tmdb-us")
    assert gate.requests == 0


def test_site_mapping_when_tmdb_has_nothing(sites):
    sites["fsk"].lookup = _site("fsk", {"Film": "16"}).lookup
    answer = resolve_certification(_q(), NL, _gate())
    assert (answer.kind, answer.rating, answer.source) == ("derived", "16", "fsk")


def test_site_asked_once_per_item(sites):
    gate = _gate()
    answer = resolve_certification(_q(), NL, gate)
    assert answer.kind == "none"
    assert all(len(s.calls) <= 1 for s in sites.values())


def test_ride_along_never_calls_sites(sites):
    answer = resolve_certification(_q(), NL, None)
    assert answer.kind == "not_asked"
    assert not any(s.calls for s in sites.values())


def test_none_needs_clean_chain(sites):
    sites["fsk"].lookup = _site("fsk", TransientProviderError("http 503", status=503)).lookup
    assert resolve_certification(_q(), NL, _gate()).kind == "transient"


def test_found_rating_saved_despite_outage(sites):
    sites["fsk"].lookup = _site("fsk", TransientProviderError("http 503", status=503)).lookup
    sites["bbfc"].lookup = _site("bbfc", {"Film": "15"}).lookup
    answer = resolve_certification(_q(), NL, _gate())
    assert (answer.kind, answer.rating, answer.source) == ("derived", "16", "bbfc")


def test_tmdb_unavailable_and_nothing_found_is_transient(sites):
    answer = resolve_certification(_q(certs=TRANSIENT), NL, _gate())
    assert answer.kind == "transient"


def test_invalid_tmdb_value_skipped(sites):
    answer = resolve_certification(_q(certs={"NL": "PG-13", "US": "R"}), NL, None)
    assert (answer.kind, answer.rating) == ("derived", "16")


def test_tvshow_medieraadet_not_unclean(sites):
    answer = resolve_certification(_q(media="tvshow"), DK, _gate())
    assert answer.kind == "none"
    assert sites["medieraadet"].calls == []


def test_disabled_site_is_not_unclean(sites):
    gate = _gate(enabled=("bbfc",))
    assert resolve_certification(_q(), NL, gate).kind == "none"
    assert sites["fsk"].calls == []


def test_memo_shares_answers_not_transients(sites):
    sites["fsk"].lookup = _site("fsk", {"Film": "16"}).lookup
    gate = _gate()
    resolve_certification(_q(), NL, gate)
    resolve_certification(_q(), NL, gate)
    assert gate.requests == 1


def test_cap_makes_sites_unavailable(sites):
    gate = _gate(cap=1)
    with patch.object(certchain, "log") as log:
        assert gate.ask("fsk", _q()) is None
        assert gate.ask("bbfc", _q()) is TRANSIENT
        assert gate.ask("bbfc", _q("Other")) is TRANSIENT
    caps = [c for c in log.info.call_args_list if c.kwargs.get("event") == "cert.cap_reached"]
    assert len(caps) == 1


def test_429_trips_site_breaker(sites):
    sites["fsk"].lookup = _site("fsk", TransientProviderError("http 429", status=429)).lookup
    gate = _gate()
    assert gate.ask("fsk", _q()) is TRANSIENT
    assert gate.ask("fsk", _q("Other")) is TRANSIENT
    assert gate.requests == 1


def test_site_401_does_not_stop_the_scan(sites):
    sites["bbfc"].lookup = _site("bbfc", ApiKeyError("bbfc")).lookup
    gate = _gate()
    assert gate.ask("bbfc", _q()) is TRANSIENT
    assert gate.ask("bbfc", _q("Other")) is TRANSIENT
    assert gate.requests == 1


def test_abort_during_wait(sites):
    monitor = _Monitor(abort=True)
    gate = SiteGate(frozenset({"fsk"}), CircuitBreaker(), monitor, min_interval=5.0,
                    clock=iter([0.0, 1.0, 2.0]).__next__)
    assert gate.ask("fsk", _q("A")) is None  # first request needs no wait
    assert gate.ask("fsk", _q("B")) is TRANSIENT
    assert gate.aborted and monitor.waits == [4.0]
    assert len(sites["fsk"].calls) == 1


def test_budget_exceeded_is_unclean(sites):
    ticks = iter([0.0] + [100.0] * 20)
    answer = resolve_certification(_q(), NL, _gate(), budget_sec=60, clock=lambda: next(ticks))
    assert answer.kind == "transient"
    assert not any(s.calls for s in sites.values())


def test_gate_logs_each_answer(sites):
    with patch.object(certchain, "log") as log:
        _gate().ask("fsk", _q())
    assert any(c.kwargs.get("event") == "cert.site" for c in log.debug.call_args_list)


def test_unexpected_error_disables_site(sites):
    sites["fsk"].lookup = _site("fsk", ValueError("parse bug")).lookup
    gate = _gate()
    with patch.object(certchain, "log"):
        assert gate.ask("fsk", _q()) is TRANSIENT
    assert gate.ask("fsk", _q("Other")) is TRANSIENT
    assert gate.requests == 1


def test_monitor_none_still_paces(sites):
    gate = SiteGate(frozenset({"fsk"}), CircuitBreaker(), None, min_interval=0.0)
    assert gate.ask("fsk", _q()) is None


def test_abort_checked_before_every_request(sites):
    monitor = _Monitor(abort_after=1)
    gate = _gate(monitor=monitor)
    assert gate.ask("fsk", _q("A")) is None
    assert gate.ask("bbfc", _q("A")) is TRANSIENT  # another site, no wait pending
    assert gate.aborted and sites["bbfc"].calls == []


def test_chain_stops_on_abort(sites):
    de = load_preset("DE")
    gate = _gate(monitor=_Monitor(abort_after=0))
    answer = resolve_certification(_q(certs={"US": "R"}), de, gate)
    assert answer.kind == "transient"  # not the TMDB-mapped US rating


def test_every_request_of_a_lookup_counts(sites):
    def two_requests(title, original_title, year, media_type, imdb_id=None, pace=None):
        for _ in range(2):
            if not pace():
                raise TransientProviderError("paced out")
        return None

    sites["medieraadet"].lookup = two_requests
    gate = _gate()
    assert gate.ask("medieraadet", _q()) is None
    assert gate.requests == 2
    capped = _gate(cap=1)
    assert capped.ask("medieraadet", _q()) is TRANSIENT


def test_memo_key_includes_original_title_and_imdb(sites):
    sites["fsk"].lookup = _site("fsk", {"Film": "16"}).lookup
    gate = _gate()
    gate.ask("fsk", _q())
    other = CertQuery(title="Film", original_title="Other", year=2014, media_type="movie",
                      imdb_id="tt1", tmdb_certs={})
    gate.ask("fsk", other)
    assert gate.requests == 2
