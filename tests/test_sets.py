"""Tests for movie set planning (engine/sets.py): which library a set
follows, which TMDB collection it stands for, managed, duplicate and shared
sets, and which movies move."""
from __future__ import annotations

from typing import Any

from resources.lib.data.libraries import Library, LibraryConfig
from resources.lib.engine.sets import (
    LEFT_DUPLICATE,
    LEFT_NO_COLLECTION,
    LEFT_NO_MAJORITY,
    LEFT_NO_MEMBERS,
    LEFT_OFF,
    LEFT_SHARED,
    LEFT_TIE_LIBRARY,
    Move,
    describe_set,
    plan_sets,
    user_moved,
)

MOVIES = "smb://nas/Movies/"
KIDS = "smb://nas/Movies/Kids/"
EXCLUDED = "smb://nas/Movies/Skip/"
SERIES = "smb://nas/Series/"
ELSEWHERE = "smb://other/"

_SETS_ON = frozenset({"movie_sets", "plots"})


def _config(movies_toggles: frozenset[str] = _SETS_ON,
            series_toggles: frozenset[str] = _SETS_ON) -> LibraryConfig:
    """Movies (English) with a Dutch Kids subfolder and an excluded folder,
    and a second library Series. Both have Movie sets ticked by default."""
    return LibraryConfig([
        Library(path=MOVIES, label="Movies", included=True, language="en-US",
                fallback_language="", toggles=movies_toggles,
                subfolders=(
                    {"path": KIDS, "language": "nl-NL", "fallback_language": "en-US"},
                    {"path": EXCLUDED, "enabled": False},
                )),
        Library(path=SERIES, label="Series", included=True, language="en-US",
                fallback_language="", toggles=series_toggles),
    ])


def M(setid: int, title: str) -> dict[str, Any]:
    """A Kodi set as GetMovieSets lists it."""
    return {"setid": setid, "title": title, "plot": "", "art": {}}


D = M


def _movie(movieid: int, setid: int, folder: str, tmdb: str = "",
           premiered: str = "2010-01-01") -> dict[str, Any]:
    return {
        "movieid": movieid, "title": "Film {}".format(movieid), "set": "",
        "setid": setid, "file": "{}film{:03d}.mkv".format(folder, movieid),
        "uniqueid": {"tmdb": tmdb or str(1000 + movieid)}, "premiered": premiered,
    }


def _plan(sets=None, members=None, libraries=None, collections=None, holders=None,
          names=None, set_moves=None):
    if members is None:
        members = [_movie(i, 1, KIDS) for i in range(1, 4)]
    if collections is None:
        collections = {m["uniqueid"]["tmdb"]: "86066" for m in members}
    return plan_sets(
        sets if sets is not None else [M(1, "Set")],
        members,
        libraries or _config(),
        collections,
        holders or {},
        names or {},
        set_moves or {},
    )


def _only(**kwargs):
    plans, _ = _plan(**kwargs)
    assert len(plans) == 1
    return plans[0]


# ---------------------------------------------------------------------------
# Which library a set follows
# ---------------------------------------------------------------------------


def test_majority_library_by_effective_folder_settings():
    # One Kodi library, but the Kids subfolder is Dutch: its 8 members beat
    # the 1 English member, whose file sorts first.
    members = [_movie(1, 1, MOVIES)] + [_movie(i, 1, KIDS) for i in range(2, 10)]
    plan = _plan(members=members)[0][0]
    assert plan.reason is None and plan.file.startswith(KIDS)
    assert plan.file == members[1]["file"]
    assert plan.collection_id == "86066"
    assert plan.member_files == tuple(sorted(m["file"] for m in members))


def test_library_tie_leaves_set_alone():
    members = [_movie(1, 1, MOVIES), _movie(2, 1, KIDS)]
    assert _only(members=members).reason == LEFT_TIE_LIBRARY


def test_excluded_members_do_not_count():
    members = [_movie(1, 1, EXCLUDED), _movie(2, 1, EXCLUDED), _movie(3, 1, SERIES),
               _movie(4, 1, ELSEWHERE)]
    plan = _only(members=members)
    assert plan.reason is None
    assert plan.file == members[2]["file"]
    assert plan.member_files == (members[2]["file"],)


def test_excluded_library_does_not_count():
    config = _config()
    config.libraries[1] = Library(path=SERIES, label="Series", included=False,
                                  language="en-US", fallback_language="",
                                  toggles=_SETS_ON)
    members = [_movie(1, 1, SERIES), _movie(2, 1, SERIES), _movie(3, 1, KIDS)]
    plan = _only(members=members, libraries=config)
    assert plan.reason is None and plan.file == members[2]["file"]


def test_no_counted_member():
    members = [_movie(1, 1, EXCLUDED), _movie(2, 1, ELSEWHERE)]
    assert _only(members=members).reason == LEFT_NO_MEMBERS


def test_set_without_any_member_has_no_members():
    assert _only(members=[], sets=[M(1, "Empty")]).reason == LEFT_NO_MEMBERS


def test_winning_library_without_movie_sets_tick():
    members = [_movie(1, 1, SERIES), _movie(2, 1, SERIES), _movie(3, 1, KIDS)]
    plan = _only(members=members, libraries=_config(series_toggles=frozenset({"plots"})))
    assert plan.reason == LEFT_OFF
    assert plan.collection_id is None


# ---------------------------------------------------------------------------
# Which collection a set stands for
# ---------------------------------------------------------------------------


def _with_collections(*collection_ids: str):
    members = [_movie(i, 1, KIDS) for i in range(1, len(collection_ids) + 1)]
    collections = {}
    for member, cid in zip(members, collection_ids):
        if cid is not None:
            collections[member["uniqueid"]["tmdb"]] = cid
    return _only(members=members, collections=collections)


def test_collection_needs_more_than_half():
    plan = _with_collections("C1", "C1", "C2")
    assert plan.reason is None and plan.collection_id == "C1"
    plan = _with_collections("C1", "C2")
    assert plan.reason == LEFT_NO_MAJORITY and plan.collection_id is None
    # A hand-made "Favourites": a member without a collection counts against.
    assert _with_collections("C1", "C1", "C2", "").reason == LEFT_NO_MAJORITY
    plan = _with_collections("", "")
    assert plan.reason == LEFT_NO_COLLECTION and plan.collection_id is None


def test_member_without_stored_collection_counts_against():
    assert _with_collections("C1", None).reason == LEFT_NO_MAJORITY
    assert _with_collections(None, None).reason == LEFT_NO_COLLECTION
    assert _with_collections("C1", "C1", None).collection_id == "C1"


def test_collection_vote_counts_every_counted_member():
    # The winning Kids group alone is all C1, but the English member counts
    # too: two of three is still more than half.
    members = [_movie(1, 1, MOVIES), _movie(2, 1, KIDS), _movie(3, 1, KIDS)]
    collections = {"1001": "C2", "1002": "C1", "1003": "C1"}
    assert _only(members=members, collections=collections).collection_id == "C1"


def test_newest_member_is_the_set_age():
    members = [_movie(1, 1, KIDS, premiered="2010-07-09"),
               _movie(2, 1, KIDS, premiered="2024-06-01"),
               _movie(3, 1, KIDS, premiered=""),
               _movie(4, 1, ELSEWHERE, premiered="2030-01-01")]
    assert _only(members=members).premiered == "2024-06-01"


# ---------------------------------------------------------------------------
# Managed, duplicate and shared sets
# ---------------------------------------------------------------------------


def _two_sets(second_title: str, names=None, holders=None, dup_folder: str = KIDS,
              set_moves=None, libraries=None):
    managed = [_movie(i, 1, KIDS) for i in range(1, 4)]
    duplicate = [_movie(7, 2, dup_folder)]
    return _plan(
        sets=[M(1, "Verschrikkelijke Ikke Collectie"), D(2, second_title)],
        members=managed + duplicate,
        collections={m["uniqueid"]["tmdb"]: "86066" for m in managed + duplicate},
        names=names, holders=holders, set_moves=set_moves, libraries=libraries,
    )


def test_duplicate_by_known_name_is_merged():
    plans, moves = _two_sets(
        "Despicable Me Collection",
        names={"collection:86066": frozenset({"despicable me collection"})},
        holders={"collection:86066": 1})
    assert [p.reason for p in plans] == [None, LEFT_DUPLICATE]
    assert [m.to_setid for m in moves] == [1]
    assert plans[1].managed_setid == 1
    assert plans[1].collection_id == "86066"
    assert moves == [Move(movieid=7, identity="tmdb:1007", from_setid=2,
                          from_name="Despicable Me Collection", to_setid=1)]


def test_known_name_matches_trimmed_and_any_case():
    plans, moves = _two_sets(
        "  DESPICABLE Me Collection ",
        names={"collection:86066": frozenset({"despicable me collection"})},
        holders={"collection:86066": 1})
    assert plans[1].reason == LEFT_DUPLICATE and len(moves) == 1


def test_user_named_set_sharing_a_collection_is_shared_not_duplicate():
    names = {"collection:86066": frozenset({"despicable me collection"})}
    plans, moves = _two_sets("Favourites", names=names, holders={"collection:86066": 1})
    assert plans[1].reason == LEFT_SHARED
    assert plans[1].managed_setid is None
    assert not moves


def test_shared_set_is_never_a_merge_target():
    # The tracker records the scraper-named set as the collection's set, so
    # it stays managed although Favourites has more members.
    names = {"collection:86066": frozenset({"despicable me collection"})}
    managed = [_movie(i, 1, KIDS) for i in range(1, 3)]
    favourites = [_movie(i, 3, KIDS) for i in range(10, 14)]
    plans, moves = _plan(
        sets=[M(1, "Despicable Me Collection"), D(3, "Favourites")],
        members=managed + favourites,
        collections={m["uniqueid"]["tmdb"]: "86066" for m in managed + favourites},
        names=names, holders={"collection:86066": 1})
    assert [p.reason for p in plans] == [None, LEFT_SHARED]
    assert not moves


def test_managed_set_is_the_holder():
    # The holder has fewer members than the other set; the record wins.
    names = {"collection:86066": frozenset({"verschrikkelijke ikke collectie"})}
    plans, moves = _two_sets("Despicable Me Collection", names=names,
                             holders={"collection:86066": 2})
    assert [p.reason for p in plans] == [LEFT_DUPLICATE, None]
    assert plans[0].managed_setid == 2
    assert {m.to_setid for m in moves} == {2} and len(moves) == 3


def test_managed_set_without_holder_is_the_larger_then_lower_id():
    # Only sets carrying a known name are candidates.
    names = {"collection:86066": frozenset({"despicable me collection",
                                            "verschrikkelijke ikke collectie"})}
    plans, _ = _two_sets("Despicable Me Collection", names=names)
    assert [p.reason for p in plans] == [None, LEFT_DUPLICATE]
    # A holder that is no longer a Kodi set counts as no holder.
    plans, _ = _two_sets("Despicable Me Collection", names=names,
                         holders={"collection:86066": 99})
    assert [p.reason for p in plans] == [None, LEFT_DUPLICATE]
    # Equal size: the lower setid is managed.
    plans, _ = _plan(
        sets=[M(5, "Despicable Me Collection"), M(4, "Verschrikkelijke Ikke Collectie")],
        members=[_movie(1, 5, KIDS), _movie(2, 4, KIDS)],
        collections={"1001": "86066", "1002": "86066"},
        names=names)
    assert {p.setid: p.reason for p in plans} == {4: None, 5: LEFT_DUPLICATE}


def test_without_holder_a_set_with_an_unknown_name_is_never_managed():
    # First scan of the collection: Favourites is larger, but only the
    # scraper-named set may be managed, and nothing moves into Favourites.
    names = {"collection:86066": frozenset({"despicable me collection"})}
    scraper = [_movie(i, 1, KIDS) for i in range(1, 3)]
    favourites = [_movie(i, 3, KIDS) for i in range(10, 14)]
    plans, moves = _plan(
        sets=[M(1, "Despicable Me Collection"), D(3, "Favourites")],
        members=scraper + favourites,
        collections={m["uniqueid"]["tmdb"]: "86066" for m in scraper + favourites},
        names=names)
    assert [p.reason for p in plans] == [None, LEFT_SHARED]
    assert plans[0].managed_setid is None and plans[1].managed_setid is None
    assert not moves


def test_without_holder_or_known_name_every_set_is_shared():
    plans, moves = _two_sets("Despicable Me Collection")
    assert [p.reason for p in plans] == [LEFT_SHARED, LEFT_SHARED]
    assert not moves
    # A known name of another collection does not count.
    plans, moves = _two_sets("Despicable Me Collection",
                             names={"collection:2150": frozenset({"despicable me collection"})})
    assert [p.reason for p in plans] == [LEFT_SHARED, LEFT_SHARED]
    assert not moves


def test_lone_set_is_managed_without_holder_or_names():
    assert _only().reason is None


def test_sets_of_other_collections_do_not_interact():
    plans, moves = _plan(
        sets=[M(1, "Despicable Me Collection"), M(2, "Shrek Collection")],
        members=[_movie(1, 1, KIDS), _movie(2, 2, KIDS)],
        collections={"1001": "86066", "1002": "2150"},
        names={"collection:86066": frozenset({"shrek collection"})})
    assert [p.reason for p in plans] == [None, None]
    assert not moves


def test_member_in_library_without_movie_sets_not_moved():
    names = {"collection:86066": frozenset({"despicable me collection"})}
    managed = [_movie(i, 1, KIDS) for i in range(1, 4)]
    duplicate = [_movie(7, 2, KIDS), _movie(8, 2, KIDS), _movie(9, 2, SERIES)]
    plans, moves = _plan(
        sets=[M(1, "Verschrikkelijke Ikke Collectie"), D(2, "Despicable Me Collection")],
        members=managed + duplicate,
        collections={m["uniqueid"]["tmdb"]: "86066" for m in managed + duplicate},
        names=names, holders={"collection:86066": 1},
        libraries=_config(series_toggles=frozenset({"plots"})))
    assert plans[1].reason == LEFT_DUPLICATE
    assert [m.movieid for m in moves] == [7, 8]


def test_member_in_excluded_folder_not_moved():
    names = {"collection:86066": frozenset({"despicable me collection"})}
    managed = [_movie(i, 1, KIDS) for i in range(1, 4)]
    duplicate = [_movie(7, 2, KIDS), _movie(8, 2, EXCLUDED)]
    _, moves = _plan(
        sets=[M(1, "Verschrikkelijke Ikke Collectie"), D(2, "Despicable Me Collection")],
        members=managed + duplicate,
        collections={m["uniqueid"]["tmdb"]: "86066" for m in managed + duplicate},
        names=names, holders={"collection:86066": 1})
    assert [m.movieid for m in moves] == [7]


def test_member_of_another_collection_not_moved():
    names = {"collection:86066": frozenset({"despicable me collection"})}
    managed = [_movie(i, 1, KIDS) for i in range(1, 4)]
    duplicate = [_movie(7, 2, KIDS), _movie(8, 2, KIDS), _movie(9, 2, KIDS)]
    collections = {m["uniqueid"]["tmdb"]: "86066" for m in managed + duplicate}
    collections["1009"] = "2150"
    _, moves = _plan(
        sets=[M(1, "Verschrikkelijke Ikke Collectie"), D(2, "Despicable Me Collection")],
        members=managed + duplicate, collections=collections,
        names=names, holders={"collection:86066": 1})
    assert [m.movieid for m in moves] == [7, 8]


def test_user_moved_member_not_moved_again():
    names = {"collection:86066": frozenset({"despicable me collection"})}
    move = {"kodi_id": 7, "from_name": "Despicable Me Collection", "to_setid": 1,
            "moved_on": "2026-11-01", "locked": 0}
    _, moves = _two_sets("Despicable Me Collection", names=names,
                         holders={"collection:86066": 1},
                         set_moves={"tmdb:1007": move})
    assert not moves
    # The same record with another movieid: Kodi re-added the movie.
    _, moves = _two_sets("Despicable Me Collection", names=names,
                         holders={"collection:86066": 1},
                         set_moves={"tmdb:1007": dict(move, kodi_id=70)})
    assert [m.movieid for m in moves] == [7]


def test_user_moved_movie_is_not_moved_again():
    assert user_moved({"kodi_id": 7, "to_setid": 1, "locked": 0}, 7, 2) is True
    assert user_moved({"kodi_id": 7, "to_setid": 1, "locked": 0}, 9, 2) is False   # re-added by a refresh
    assert user_moved(None, 7, 2) is False
    # Still where MetaMender put it: not a user move.
    assert user_moved({"kodi_id": 7, "to_setid": 1, "locked": 0}, 7, 1) is False
    # A recorded user move stays one for good.
    assert user_moved({"kodi_id": 7, "to_setid": 1, "locked": 1}, 7, 1) is True
    assert user_moved({"kodi_id": 7, "to_setid": 1, "locked": 1}, 9, 2) is True


# ---------------------------------------------------------------------------
# describe_set: one set's plan from what is stored, for Details
# ---------------------------------------------------------------------------


class _StoredTracker:
    """The tracker reads describe_set makes; any write fails the test."""

    def __init__(self, collections, holders=None, names=None):
        self.collections = collections
        self.holders = holders or {}
        self.names = names or {}

    def get_movie_collection(self, tmdb_id):
        return self.collections.get(tmdb_id)

    def get_item(self, media_type, identity):
        setid = self.holders.get(identity)
        return None if setid is None else {"kodi_id": setid}

    def get_set_names(self, identity):
        return self.names.get(identity, frozenset())

    def get_set_move(self, identity):
        return None


def _describe(monkeypatch, sets, members, tracker, setid):
    from resources.lib.engine import sets as sets_module
    monkeypatch.setattr(sets_module.kodi, "get_all_items", lambda mt, **kw: sets)
    monkeypatch.setattr(sets_module.kodi, "get_set_members", lambda: members)
    return describe_set(setid, _config(), tracker)


def test_describe_set_uses_stored_collections_only(monkeypatch):
    members = [_movie(i, 1, KIDS) for i in range(1, 4)]
    stored = {"1001": "86066", "1002": "86066"}
    plan = _describe(monkeypatch, [M(1, "Set")], members, _StoredTracker(stored), 1)
    assert plan.reason is None and plan.collection_id == "86066"
    # A movie never asked about counts for no collection.
    plan = _describe(monkeypatch, [M(1, "Set")], members,
                     _StoredTracker({"1001": "86066"}), 1)
    assert plan.reason == LEFT_NO_MAJORITY


def test_describe_set_of_a_set_kodi_lacks_is_none(monkeypatch):
    assert _describe(monkeypatch, [M(1, "Set")], [], _StoredTracker({}), 2) is None


def test_describe_shared_set_names_the_managed_set(monkeypatch):
    members = [_movie(1, 1, KIDS), _movie(2, 2, KIDS)]
    stored = {"1001": "86066", "1002": "86066"}
    tracker = _StoredTracker(stored, holders={"collection:86066": 1})
    plan = _describe(monkeypatch, [M(1, "Toy Story"), M(2, "Favourites")], members,
                     tracker, 2)
    assert plan.reason == LEFT_SHARED and plan.managed_setid == 1
    # No set may be managed: the other set of the collection is named.
    plan = _describe(monkeypatch, [M(1, "Mine"), M(2, "Favourites")], members,
                     _StoredTracker(stored), 2)
    assert plan.reason == LEFT_SHARED and plan.managed_setid == 1
