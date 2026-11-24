"""Movie set planning: which library a Kodi set follows, which TMDB
collection it stands for, which set MetaMender manages for that collection,
and which movies move out of a duplicate set into the managed one.

Kodi sets carry no TMDB id, so a set is read through its member movies: their
files place them in a library (by effective folder settings, the longest
prefix wins, as for movies) and their stored TMDB collection says what the
set is. A set is managed only when its counted members clearly agree on both;
anything less leaves the set alone with a reason Details can show.

Logging:
    None (pure functions).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Optional

from resources.lib.data.identity import compute_identity
from resources.lib.data.libraries import Library, LibraryConfig
from resources.lib.data.media_types import MOVIE, SET
from resources.lib.engine.scanctx import extract_ids

# Why a set is left alone (SetPlan.reason); None means it is managed.
LEFT_TIE_LIBRARY = "tie_library"    # two libraries hold equally many members
LEFT_NO_MEMBERS = "no_members"      # no member is in an included library and folder
LEFT_OFF = "off"                    # the set's library does not have Movie sets ticked
LEFT_NO_COLLECTION = "no_collection"  # no counted member belongs to a TMDB collection
LEFT_NO_MAJORITY = "no_majority"    # no collection holds more than half the members
LEFT_DUPLICATE = "duplicate"        # a second set of a managed collection; merged
LEFT_SHARED = "shared"              # a set of the user's own that shares a collection

_SETS_TOGGLE = "movie_sets"


@dataclass(frozen=True)
class SetPlan:
    setid: int
    title: str
    reason: Optional[str]
    # The TMDB collection the members chose; None when they did not get
    # that far (or chose none).
    collection_id: Optional[str]
    # A member file of the winning library group, the first in sorted order:
    # the scan reads the set's library and folder settings from it. "" when
    # no library won.
    file: str
    # The newest counted member's premiered date: the set's age.
    premiered: str
    # Every counted member's file, sorted (the set's NFO state is theirs).
    member_files: tuple[str, ...]
    # For a duplicate, the managed set it merges into.
    managed_setid: Optional[int]


@dataclass(frozen=True)
class Move:
    movieid: int
    identity: str
    from_setid: int
    from_name: str
    to_setid: int


def _name_key(name: str) -> str:
    return name.strip().lower()


def user_moved(move: Optional[dict[str, Any]], movieid: int, current_setid: int) -> bool:
    """Whether the user moved this movie after MetaMender did: the recorded
    move is for the same Kodi movie, which now sits outside the set it was
    moved to. A recorded user move (locked) stays one for good. Another
    movieid means Kodi re-added the movie (a refresh deletes and re-adds), so
    the earlier move says nothing about it."""
    if move is None:
        return False
    if move.get("locked"):
        return True
    return int(move.get("kodi_id") or 0) == movieid and int(
        move.get("to_setid") or 0) != current_setid


def _counted_library(member: dict[str, Any], libraries: LibraryConfig) -> Optional[Library]:
    """The member's library when both it and the member's folder are
    included; None means the member does not count."""
    return libraries.included_for(str(member.get("file") or ""))


def _library_vote(
    counted: list[tuple[dict[str, Any], Library]], libraries: LibraryConfig,
) -> tuple[Optional[Library], str]:
    """(winning library, its representative file), or (None, "") on a tie.
    Members are grouped by library and effective language and fallback, so
    a subfolder with its own language votes on its own."""
    groups: dict[tuple[str, str, str], list[str]] = {}
    owner: dict[tuple[str, str, str], Library] = {}
    for member, lib in counted:
        path = str(member.get("file") or "")
        _, language, fallback = libraries.folder_settings(path, lib)
        key = (lib.path, language, fallback)
        groups.setdefault(key, []).append(path)
        owner[key] = lib
    ranked = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
    if len(ranked) > 1 and len(ranked[0][1]) == len(ranked[1][1]):
        return None, ""
    key, files = ranked[0]
    return owner[key], min(files)


def _collection_vote(
    counted: list[tuple[dict[str, Any], Library]], collections: dict[str, str],
) -> tuple[Optional[str], Optional[str]]:
    """(collection id, None) when one collection holds more than half of the
    counted members, else (None, reason). A member with no stored collection
    counts against every collection."""
    votes: Counter[str] = Counter()
    for member, _ in counted:
        cid = _member_collection(member, collections)
        if cid:
            votes[cid] += 1
    if not votes:
        return None, LEFT_NO_COLLECTION
    cid, count = votes.most_common(1)[0]
    if count * 2 > len(counted):
        return cid, None
    return None, LEFT_NO_MAJORITY


def _member_collection(member: dict[str, Any], collections: dict[str, str]) -> str:
    tmdb_id = extract_ids(member)[0]
    return collections.get(tmdb_id, "") if tmdb_id else ""


def _first_plan(
    kodi_set: dict[str, Any], members: list[dict[str, Any]],
    libraries: LibraryConfig, collections: dict[str, str],
) -> tuple[SetPlan, int]:
    """The set's plan before managed and duplicate sets are told apart, and
    its number of counted members."""
    counted = [(m, lib) for m in members
               for lib in [_counted_library(m, libraries)] if lib is not None]
    files = tuple(sorted(str(m.get("file") or "") for m, _ in counted))
    dates = [str(m.get("premiered") or "") for m, _ in counted]
    base = SetPlan(
        setid=int(kodi_set.get("setid") or 0),
        title=str(kodi_set.get("title") or ""),
        reason=None, collection_id=None, file="",
        premiered=max(dates, default=""), member_files=files, managed_setid=None,
    )
    if not counted:
        return replace(base, reason=LEFT_NO_MEMBERS), 0
    lib, file = _library_vote(counted, libraries)
    if lib is None:
        return replace(base, reason=LEFT_TIE_LIBRARY), len(counted)
    base = replace(base, file=file)
    if _SETS_TOGGLE not in lib.toggles:
        return replace(base, reason=LEFT_OFF), len(counted)
    cid, reason = _collection_vote(counted, collections)
    return replace(base, reason=reason, collection_id=cid), len(counted)


def _managed_setid(
    group: list[tuple[SetPlan, int]], holder: Optional[int],
) -> int:
    """The set managed for a collection: the one the tracker row records,
    else the one with most counted members, then the lowest setid."""
    setids = {plan.setid for plan, _ in group}
    if holder is not None and holder in setids:
        return holder
    return min(group, key=lambda pc: (-pc[1], pc[0].setid))[0].setid


def _moves_out_of(
    duplicate: SetPlan, managed_setid: int, members: list[dict[str, Any]],
    libraries: LibraryConfig, collections: dict[str, str],
    set_moves: dict[str, dict[str, Any]],
) -> list[Move]:
    """The duplicate's members that move into the managed set: each in an
    included library and folder with Movie sets ticked, of the managed
    collection, and not moved elsewhere by the user."""
    out: list[Move] = []
    for member in members:
        lib = _counted_library(member, libraries)
        if lib is None or _SETS_TOGGLE not in lib.toggles:
            continue
        if _member_collection(member, collections) != duplicate.collection_id:
            continue
        movieid = int(member.get("movieid") or 0)
        identity = compute_identity(member, MOVIE)
        if user_moved(set_moves.get(identity), movieid, duplicate.setid):
            continue
        out.append(Move(movieid=movieid, identity=identity, from_setid=duplicate.setid,
                        from_name=duplicate.title, to_setid=managed_setid))
    return out


def plan_sets(
    sets: list[dict[str, Any]],
    members: list[dict[str, Any]],
    libraries: LibraryConfig,
    collections: dict[str, str],
    holders: dict[str, int],
    names: dict[str, frozenset[str]],
    set_moves: dict[str, dict[str, Any]],
) -> tuple[list[SetPlan], list[Move]]:
    """A plan for every Kodi set (in the order given) and the moves that
    merge duplicate sets.

    ``members`` are the movies in a set (kodi.get_set_members);
    ``collections`` maps a movie's TMDB id to its stored collection ("" for
    none); ``holders`` maps a set identity to the setid its tracker row
    records; ``names`` maps a set identity to its known lowercased names;
    ``set_moves`` maps a movie identity to its recorded move.

    Among the sets whose members chose one collection, one is managed. Any
    other is a duplicate only when its name is a known name of the
    collection (the scraper's or an old TMDB name, which is how Kodi makes
    one); a set the user named is shared and left alone, and never becomes
    the managed set's merge target in reverse.
    """
    by_set: dict[int, list[dict[str, Any]]] = {}
    for member in members:
        by_set.setdefault(int(member.get("setid") or 0), []).append(member)

    first = [_first_plan(s, by_set.get(int(s.get("setid") or 0), []), libraries, collections)
             for s in sets]
    by_collection: dict[str, list[tuple[SetPlan, int]]] = {}
    for plan, count in first:
        if plan.reason is None and plan.collection_id:
            by_collection.setdefault(plan.collection_id, []).append((plan, count))

    final: dict[int, SetPlan] = {}
    moves: list[Move] = []
    for cid, group in by_collection.items():
        identity = compute_identity({"collection_id": cid}, SET)
        managed = _managed_setid(group, holders.get(identity))
        known = names.get(identity, frozenset())
        for plan, _ in group:
            if plan.setid == managed:
                continue
            if _name_key(plan.title) in known:
                final[plan.setid] = replace(plan, reason=LEFT_DUPLICATE, managed_setid=managed)
                moves.extend(_moves_out_of(final[plan.setid], managed,
                                           by_set.get(plan.setid, []), libraries,
                                           collections, set_moves))
            else:
                final[plan.setid] = replace(plan, reason=LEFT_SHARED)
    plans = [final.get(plan.setid, plan) for plan, _ in first]
    return plans, moves
