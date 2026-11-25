"""Movie set planning: which library a Kodi set follows, which TMDB
collection it stands for, which set MetaMender manages for that collection,
and which movies move out of a duplicate set into the managed one.

Kodi sets carry no TMDB id, so a set is read through its member movies: their
files place them in a library (by effective folder settings, the longest
prefix wins, as for movies) and their stored TMDB collection says what the
set is. A set is managed only when its counted members clearly agree on both;
anything less leaves the set alone with a reason Details can show.

The planning (plan_sets) is pure. prepare_sets runs it against Kodi and the
tracker before the set pass: it asks TMDB for the collections it still
lacks, merges duplicate sets by moving their movies, and hands the pass the
managed sets to scan. describe_set plans one set the same way from what is
stored, for Details and Update now, without asking a site or writing.

Logging:
    Logger: 'sets'
    Key events:
        - sets.moved (INFO): a movie moved out of a duplicate set into the
          managed set of its collection (movie, from_set, to_set)
        - sets.move_failed (WARNING): Kodi refused the move, or the movie
          did not land in the managed set (AddSet matches names by LIKE)
        - sets.plot_restored (INFO): a move made Kodi overwrite the managed
          set's plot with the old set's, and it was put back
        - sets.left_alone (DEBUG): a set the pass does not scan, with the
          plan's reason
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Callable, Optional

from resources.lib.data import kodi
from resources.lib.data.identity import compute_identity
from resources.lib.data.libraries import Library, LibraryConfig
from resources.lib.data.media_types import MOVIE, SET
from resources.lib.data.nfo import NFO, NO_NFO, UNREACHABLE, nfo_state
from resources.lib.engine.art import ENGLISH
from resources.lib.engine.chain import TRANSIENT
from resources.lib.engine.scanctx import _call_provider, _ScanCtx, extract_ids
from resources.lib.providers import tmdb
from resources.lib.providers.languages import tmdb_language
from resources.lib.utils import get_logger

log = get_logger('sets')

# Why a set is left alone (SetPlan.reason); None means it is managed.
LEFT_TIE_LIBRARY = "tie_library"    # two libraries hold equally many members
LEFT_NO_MEMBERS = "no_members"      # no member is in an included library and folder
LEFT_OFF = "off"                    # the set's library does not have Movie sets ticked
LEFT_NO_COLLECTION = "no_collection"  # no counted member belongs to a TMDB collection
LEFT_NO_MAJORITY = "no_majority"    # no collection holds more than half the members
LEFT_DUPLICATE = "duplicate"        # a second set of a managed collection; merged
LEFT_SHARED = "shared"              # a set of the user's own that shares a collection

# A stored "in no collection" answer is asked again at this age: TMDB adds
# movies to collections over time. An answer naming a collection is final.
NONE_RECHECK_DAYS = 180

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
    group: list[tuple[SetPlan, int]], holder: Optional[int], known: frozenset[str],
) -> Optional[int]:
    """The set managed for a collection, or None when none may be.

    The set the tracker row records wins. Without one (a collection's first
    scan, or a holder Kodi no longer has) only a set carrying a known name of
    the collection may be chosen, by most counted members, then lowest
    setid: a set the user named, such as "Favourites", must never become the
    target that the scraper's set is merged into. A collection with one set
    has nothing to merge, so that set is managed whatever its name."""
    setids = {plan.setid for plan, _ in group}
    if holder is not None and holder in setids:
        return holder
    if len(group) == 1:
        return group[0][0].setid
    named = [pc for pc in group if _name_key(pc[0].title) in known]
    if not named:
        return None
    return min(named, key=lambda pc: (-pc[1], pc[0].setid))[0].setid


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

    ``members`` are the movies in a set (kodi.get_movies_for_sets, setid above 0);
    ``collections`` maps a movie's TMDB id to its stored collection ("" for
    none); ``holders`` maps a set identity to the setid its tracker row
    records; ``names`` maps a set identity to its known lowercased names;
    ``set_moves`` maps a movie identity to its recorded move.

    Among the sets whose members chose one collection, at most one is
    managed (see _managed_setid). Any other is a duplicate only when its name
    is a known name of the collection (the scraper's or an old TMDB name,
    which is how Kodi makes one); a set the user named is shared and left
    alone, and is never a merge target unless the tracker already records it
    as the collection's set. When no set may be managed, every set of the
    collection is shared and nothing moves.
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
        known = names.get(identity, frozenset())
        managed = _managed_setid(group, holders.get(identity), known)
        for plan, _ in group:
            if plan.setid == managed:
                continue
            if managed is not None and _name_key(plan.title) in known:
                final[plan.setid] = replace(plan, reason=LEFT_DUPLICATE, managed_setid=managed)
                moves.extend(_moves_out_of(final[plan.setid], managed,
                                           by_set.get(plan.setid, []), libraries,
                                           collections, set_moves))
            else:
                final[plan.setid] = replace(plan, reason=LEFT_SHARED)
    plans = [final.get(plan.setid, plan) for plan, _ in first]
    return plans, moves


# ---------------------------------------------------------------------------
# Names and NFO state
# ---------------------------------------------------------------------------


def collection_text(texts: dict[str, str], kodi_lang: str) -> Optional[str]:
    """A collection's name or plot in a Kodi language: the "xx-YY"
    translation, else the language's bare "xx" one; None when TMDB has
    neither."""
    full = tmdb_language(kodi_lang)
    if not full:
        return None
    return texts.get(full) or texts.get(full.split("-")[0]) or None


def pick_set_name(
    names: dict[str, str], lang: str, fallback: Optional[str],
) -> tuple[Optional[str], bool]:
    """(name, from_fallback): TMDB's name of the collection in the library
    language, then the fallback language, then English. Any step after the
    first counts as a fallback, so the name is rechecked monthly."""
    steps = [lang]
    if fallback and fallback.strip():
        steps.append(fallback.strip())
    steps.append(ENGLISH)
    for index, code in enumerate(steps):
        name = collection_text(names, code)
        if name and name.strip():
            return name, index > 0
    return None, False


def set_nfo_state(member_files: tuple[str, ...]) -> str:
    """A set's NFO state from its counted members (it has no NFO of its
    own): NO_NFO as soon as a member answers without an NFO, UNREACHABLE
    when none did and a member's media cannot be reached, NFO when every
    member has one. A reachable member without an NFO already settles the
    set, whatever an unreachable one would say: the set's own fields live in
    Kodi's database, not on a share, so a member whose video is gone (a
    replaced release Kodi has not cleaned) must not hold the set back."""
    unreachable = False
    for path in member_files:
        state = nfo_state(MOVIE.name, path)
        if state == NO_NFO:
            return NO_NFO
        unreachable = unreachable or state == UNREACHABLE
    if unreachable:
        return UNREACHABLE
    return NFO if member_files else NO_NFO


# ---------------------------------------------------------------------------
# The set pass's preparation (Kodi, TMDB and the tracker)
# ---------------------------------------------------------------------------


def _set_identity(collection_id: str) -> str:
    return compute_identity({"collection_id": collection_id}, SET)


def _ask_tmdb(ctx: _ScanCtx, fetch: Callable[[], Any], identity: str) -> Any:
    """A TMDB answer, or TRANSIENT when TMDB is out for the cycle or did not
    answer."""
    if ctx.breaker.is_disabled("tmdb"):
        return TRANSIENT
    return _call_provider("tmdb", fetch, ctx, identity)


def collection_is_stale(row: Optional[dict[str, str]], today: str) -> bool:
    """Whether a movie's stored collection answer is to be asked again: it
    was never asked, or it is "none" and undated or at least
    NONE_RECHECK_DAYS old on ``today``."""
    if row is None:
        return True
    if row.get("collection_id"):
        return False
    checked_on = row.get("checked_on") or ""
    if not checked_on:
        return True
    try:
        age = date.fromisoformat(today) - date.fromisoformat(checked_on)
    except ValueError:
        return True
    return age.days >= NONE_RECHECK_DAYS


def _member_collections(
    ctx: _ScanCtx, movies: list[dict[str, Any]], wanted: Callable[[dict[str, Any]], bool],
    libraries: LibraryConfig, today: str, stats: dict[str, Any],
) -> dict[str, str]:
    """The TMDB collection of every counted movie ``wanted`` accepts, keyed
    on its TMDB id. A movie without a stored answer, or whose "none" is
    NONE_RECHECK_DAYS old, is asked (/movie/{id}, no append) and the answer
    kept; one TMDB did not answer is left out (a stale "none" stays as
    stored and counts as "none"), so it counts for no collection this scan
    and can only keep its set from being managed, never make it managed. It counts in
    stats["transient"], so Update now says it could not check rather than
    that the set is left alone."""
    collections: dict[str, str] = {}
    for member in movies:
        if not wanted(member):
            continue
        if libraries.included_for(str(member.get("file") or "")) is None:
            continue
        tmdb_id = extract_ids(member)[0]
        if not tmdb_id or tmdb_id in collections:
            continue
        row = ctx.tracker.get_movie_collection_row(tmdb_id)
        stored = None if row is None else row["collection_id"]
        if collection_is_stale(row, today):
            answer = _ask_tmdb(
                ctx, _collection_lookup(tmdb_id, ctx.tmdb_api_key),
                compute_identity(member, MOVIE),
            )
            if answer is TRANSIENT:
                stats["transient"] = stats.get("transient", 0) + 1
            else:
                stored = str(answer or "")
                ctx.tracker.set_movie_collection(tmdb_id, stored, today)
        if stored is not None:
            collections[tmdb_id] = stored
    return collections


def _collection_lookup(tmdb_id: str, api_key: str) -> Callable[[], Any]:
    return lambda: tmdb.fetch_movie_collection_id(tmdb_id, api_key)


def _learn_names(
    ctx: _ScanCtx, plans: list[SetPlan], names: dict[str, frozenset[str]],
) -> bool:
    """Record TMDB's names of every collection whose sets are all shared
    because none carries a known name (a collection's first scan with two
    or more Kodi sets). Without them the scraper's set could never be told
    from the user's and nothing would be merged. Returns whether any names
    were learned, so the caller plans again."""
    groups: dict[str, list[SetPlan]] = {}
    for plan in plans:
        if plan.collection_id and plan.reason in (None, LEFT_DUPLICATE, LEFT_SHARED):
            groups.setdefault(plan.collection_id, []).append(plan)
    learned = False
    for cid, group in sorted(groups.items()):
        if len(group) < 2 or any(p.reason != LEFT_SHARED for p in group):
            continue
        identity = _set_identity(cid)
        answer = _ask_tmdb(
            ctx, _collection_fetch(cid, ctx.tmdb_api_key), identity,
        )
        if not isinstance(answer, dict):
            continue
        ctx.tracker.add_set_names(identity, (answer.get("names") or {}).values())
        known = ctx.tracker.get_set_names(identity)
        if known != names.get(identity, frozenset()):
            names[identity] = known
            learned = True
    return learned


def _collection_fetch(collection_id: str, api_key: str) -> Callable[[], Any]:
    # Only the names are read here: no image language is needed.
    return lambda: tmdb.fetch_collection(collection_id, api_key, ())


def _lock_user_moves(
    tracker: Any, members: list[dict[str, Any]], set_moves: dict[str, dict[str, Any]],
) -> None:
    """Lock, for good, the recorded move of every movie the user has since
    put in another set."""
    for member in members:
        identity = compute_identity(member, MOVIE)
        move = set_moves.get(identity)
        if move is None or move.get("locked"):
            continue
        if user_moved(move, int(member.get("movieid") or 0), int(member.get("setid") or 0)):
            tracker.lock_set_move(identity)
            set_moves[identity] = dict(move, locked=1)


def _target_setid(plans: list[SetPlan], only_setid: Optional[int]) -> Optional[int]:
    """For Update now on a set: the managed set of its collection, or None
    when the set is not one MetaMender manages or merges."""
    for plan in plans:
        if plan.setid != only_setid:
            continue
        if plan.reason is None and plan.collection_id:
            return plan.setid
        if plan.reason == LEFT_DUPLICATE:
            return plan.managed_setid
    return None


@dataclass
class _MoveResult:
    # The members that moved, by managed setid.
    moved: dict[int, list[dict[str, Any]]]
    # The plot each managed set holds after its moves (see _restore_plot).
    plots: dict[int, str]
    # The caller's before_write said to stop (shutdown or Stop scan).
    stopped: bool


def _move_movies(
    ctx: _ScanCtx, moves: list[Move], kodi_sets: dict[int, dict[str, Any]],
    members: dict[int, dict[str, Any]], today: str, stats: dict[str, Any],
    before_write: Callable[[], bool],
) -> _MoveResult:
    """Move each movie into its managed set by that set's current name, and
    keep the move once Kodi's own answer shows the movie in that set.

    ``before_write`` runs before each move: it holds while the user browses
    the video library and returns False to stop. A move and the plot
    restore it calls for are one unit, with no hold between them, so a stop
    never leaves a managed set with the old set's plot."""
    result = _MoveResult(moved={}, plots={}, stopped=False)
    for move in moves:
        if not before_write():
            result.stopped = True
            break
        target = kodi_sets[move.to_setid]
        name = str(target.get("title") or "")
        member = members.get(move.movieid) or {}
        title = str(member.get("title") or "")
        ok = kodi.update_item(move.movieid, MOVIE, title, set=name)
        landed = kodi.get_movie_setid(move.movieid) if ok else None
        # A refused or misfiled move may still have run AddSet on the set.
        result.plots[move.to_setid] = _restore_plot(target)
        if landed != move.to_setid:
            log.warning(
                "Movie not moved into the managed set", event="sets.move_failed",
                movie=title, movieid=move.movieid, from_set=move.from_name, to_set=name,
                written=ok, setid=landed,
            )
            continue
        ctx.tracker.record_set_move(move.identity, move.movieid, move.from_name,
                                    move.to_setid, today)
        stats["moved"] = stats.get("moved", 0) + 1
        result.moved.setdefault(move.to_setid, []).append(member)
        log.info("Movie moved into the managed set", event="sets.moved", movie=title,
                 from_set=move.from_name, to_set=name)
    return result


def _restore_plot(kodi_set: dict[str, Any]) -> str:
    """Put the managed set's plot back after a move into it, and return the
    plot it now has. Kodi's SetMovieDetails with ``set`` calls AddSet with
    the moved movie's old set overview, which overwrites the plot of the
    set it lands in."""
    setid = int(kodi_set.get("setid") or 0)
    before = str(kodi_set.get("plot") or "")
    detail = kodi.get_item_by_id(SET, setid, art=False)
    after = str((detail or {}).get("plot") or "")
    if detail is None or after == before:
        return before
    title = str(kodi_set.get("title") or "")
    if not kodi.update_item(setid, SET, title, plot=before):
        return after
    log.info("Managed set plot put back after a move", event="sets.plot_restored",
             set=title, setid=setid)
    return before


def _tracker_facts(
    tracker: Any, members: list[dict[str, Any]], collections: dict[str, str],
) -> tuple[dict[str, int], dict[str, frozenset[str]], dict[str, dict[str, Any]]]:
    """plan_sets' holders, names and set_moves, read from the tracker."""
    holders: dict[str, int] = {}
    names: dict[str, frozenset[str]] = {}
    for identity in {_set_identity(cid) for cid in collections.values() if cid}:
        row = tracker.get_item(SET.name, identity)
        if row and row.get("kodi_id") is not None:
            holders[identity] = int(row["kodi_id"])
        names[identity] = tracker.get_set_names(identity)
    set_moves: dict[str, dict[str, Any]] = {}
    for member in members:
        movie = compute_identity(member, MOVIE)
        recorded = tracker.get_set_move(movie)
        if recorded is not None:
            set_moves[movie] = recorded
    return holders, names, set_moves


def describe_set(setid: int, libraries: LibraryConfig, tracker: Any) -> Optional[SetPlan]:
    """The plan of one Kodi set as the next scan would make it, for Details
    and Update now; None when Kodi has no such set.

    Reads only: it uses the collections already stored (a movie TMDB was
    never asked about counts for none, as one TMDB did not answer does in a
    scan), asks no site and records nothing. For a shared set,
    ``managed_setid`` is the set that follows the collection, or, when no
    set of it is managed, another set of the collection, so Details can
    name the set it shares with.
    """
    kodi_sets = kodi.get_all_items(SET, art=False)
    if not any(int(s.get("setid") or 0) == setid for s in kodi_sets):
        return None
    members = [m for m in kodi.get_movies_for_sets()
               if int(m.get("setid") or 0) > 0]
    collections: dict[str, str] = {}
    for member in members:
        tmdb_id = extract_ids(member)[0]
        if tmdb_id and tmdb_id not in collections:
            stored = tracker.get_movie_collection(tmdb_id)
            if stored is not None:
                collections[tmdb_id] = stored
    holders, names, set_moves = _tracker_facts(tracker, members, collections)
    plans, _ = plan_sets(kodi_sets, members, libraries, collections, holders, names,
                         set_moves)
    plan = next(p for p in plans if p.setid == setid)
    if plan.reason != LEFT_SHARED:
        return plan
    same = [p for p in plans if p.collection_id == plan.collection_id and p.setid != setid]
    managed = [p for p in same if p.reason is None] or sorted(same, key=lambda p: p.setid)
    return replace(plan, managed_setid=managed[0].setid if managed else None)


def prepare_sets(
    ctx: _ScanCtx,
    libraries: LibraryConfig,
    new_only: bool,
    only_setid: Optional[int],
    stats: dict[str, Any],
    before_write: Optional[Callable[[], bool]] = None,
) -> list[dict[str, Any]]:
    """The managed sets for the set pass, after merging duplicate sets.

    Lists Kodi's sets and their movies; asks TMDB for the collection of a
    counted member that has no stored answer; makes sure a collection with
    two or more Kodi sets has its names on record before the merge decision
    (_learn_names); plans; locks moves the user undid; moves the movies of
    duplicate sets, verifies each by re-reading the movie's setid, and puts
    the managed set's plot back where a move changed it. ``before_write``
    runs before each move (run_scan's browse hold, shutdown and Stop scan
    checks); when it returns False no further movie moves and no set is
    returned.

    A set left alone that the tracker holds a row for (its Movie sets tick
    went off, its library was left out, a tie) is marked present, so orphan
    pruning keeps its history (originals, names) for when it is managed
    again, as the movie pass does for excluded movies.

    ``new_only`` (a library-event cycle) still merges every collection but
    returns only the sets the tracker does not know yet. ``only_setid``
    (Update now on a set) merges and returns only that set's collection.
    Each item is the Kodi set with its art flattened, plus ``file`` (the
    member file whose folder settings the set follows), ``premiered`` (its
    newest counted member's), ``collection_id`` and ``member_files``.
    Raises KodiQueryError when a listing fails.
    """
    tracker = ctx.tracker
    today = date.today().isoformat()
    kodi_sets = kodi.get_all_items(SET)
    members = [m for m in kodi.get_movies_for_sets()
               if int(m.get("setid") or 0) > 0]
    by_setid = {int(s.get("setid") or 0): s for s in kodi_sets}
    by_movieid = {int(m.get("movieid") or 0): m for m in members}

    # Planned without collections first: a set its library leaves alone (a
    # tie, no counted member, Movie sets not ticked) costs no TMDB request.
    probe, _ = plan_sets(kodi_sets, members, libraries, {}, {}, {}, {})
    asking = {p.setid for p in probe if p.reason == LEFT_NO_COLLECTION}
    collections = _member_collections(
        ctx, members, lambda m: int(m.get("setid") or 0) in asking, libraries, today, stats,
    )

    holders, names, set_moves = _tracker_facts(tracker, members, collections)
    _lock_user_moves(tracker, members, set_moves)

    plans, moves = plan_sets(kodi_sets, members, libraries, collections, holders, names,
                             set_moves)
    if _learn_names(ctx, plans, names):
        plans, moves = plan_sets(kodi_sets, members, libraries, collections, holders,
                                 names, set_moves)

    target = _target_setid(plans, only_setid) if only_setid is not None else None
    if only_setid is not None:
        moves = [m for m in moves if target is not None and m.to_setid == target]
    result = _move_movies(ctx, moves, by_setid, by_movieid, today, stats,
                          before_write or (lambda: True))
    moved, plots = result.moved, result.plots

    tracked = tracker.identities_by_kodi_id(SET.name)
    for plan in plans:
        if plan.reason is not None and plan.setid in tracked:
            tracker.update_last_seen(SET.name, tracked[plan.setid], today)
    tracker.commit()
    if result.stopped:
        return []

    known = tracker.known_kodi_ids(SET.name) if new_only else set()
    items: list[dict[str, Any]] = []
    for plan in plans:
        if plan.reason is not None or not plan.collection_id:
            log.debug("Set left alone", event="sets.left_alone", set=plan.title,
                      setid=plan.setid, reason=plan.reason)
            continue
        if only_setid is not None and plan.setid != target:
            continue
        if plan.setid in known:
            continue
        item = dict(by_setid[plan.setid])
        joined = moved.get(plan.setid, [])
        if plan.setid in plots:
            item["plot"] = plots[plan.setid]
        item.update(
            file=plan.file,
            premiered=max([plan.premiered] + [str(m.get("premiered") or "") for m in joined]),
            collection_id=plan.collection_id,
            member_files=tuple(sorted(set(plan.member_files) | {
                str(m.get("file") or "") for m in joined})),
        )
        items.append(item)
    return items
