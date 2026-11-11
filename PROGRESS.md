# MetaMender Build Progress

## Phase 1: Scaffold and Infrastructure
- [x] 1.1 Create repo and addon skeleton (addon.xml, settings.xml, strings.po, entry points)
- [x] 1.2 Port constants, utilities, and logging (constants.py, utils.py)
- [x] 1.3 Set up test infrastructure (conftest.py, Kodi stubs, smoke tests)

## Phase 2: SQLite Tracker
- [x] 2.1 Database schema and connection (items, field_status, scan_state tables)
- [x] 2.2 Item CRUD operations (upsert, get, update status, record attempt)
- [x] 2.3 Field status tracking (set/get per field, query by status)
- [x] 2.4 Cycle tracking and resume (start cycle, mark processed, scan state)

## Phase 3: Kodi Data Layer
- [x] 3.1 Media type definitions (MOVIE, TVSHOW, EPISODE dataclasses)
- [x] 3.2 Kodi JSON-RPC queries (get_all_items, update_item with timing)
- [x] 3.3 Source configuration (load/save/discover/match, longest-prefix)

## Phase 4: TMDB Provider
- [x] 4.1 TMDB movie lookup (append_to_response=credits,videos,release_dates)
- [x] 4.2 TMDB season/episode and TV show lookup (content_ratings)

## Phase 5: Basic Backfill Engine
- [x] 5.1 Gap detection (which fields need work per item)
- [x] 5.2 Staleness detection (vote count thresholds, retry scheduling)
- [x] 5.3 Scan orchestrator (query, filter, prioritize, process, stats)
- [x] 5.4 Service daemon and manual trigger (first end-to-end test)

## Phase 6: Staleness and Retry Integration
- [x] 6.1 Integrate staleness into scan loop (re-check stale, backoff, exhaustion)

## Phase 7: IMDb API Provider and ID-based Episode Matching
- [x] 7.1 Create IMDb API provider (fetch_movie, fetch_episode, fetch_season, fetch_certificates)
- [x] 7.2 Create IMDb API tests (35 tests)
- [x] 7.3 Create source priority module (resolve_field, resolve_metadata)
- [x] 7.4 Create source priority tests (26 tests)
- [x] 7.5 Update constants, settings, strings, daemon (OMDB -> IMDb transition)
- [x] 7.6 Update TMDB fetch_season with tmdb_id per episode
- [x] 7.7 Restructure scan.py (ID-based episode matching, IMDb provider wiring, resolve integration)
- [x] 7.8 Update scan tests for ID-based matching (43 tests, +2 new)
- [x] 7.9 Run full validation (242 passed, ruff clean, pyflakes clean)
- [x] 7.10 Code review audit: 7 findings fixed (source_map key mismatch, live test broken refs, TVDB episode exhaust, episode error counting, diagnostic log, inlined parsing, dual timing)
- [x] 7.11 Live integration test suite extended (tests 11-33, 6 groups, 178 checks)
- [x] 7.12 IMDB ID enrichment: movies/shows from TMDB response, episodes from external_ids endpoint
- [x] 7.13 Retry jitter (random 0 to interval/4 days) and resume integration test
- [x] 7.14 Provider circuit breaker (disable after 5 consecutive failures, stop scan if both down)

## Phase 7b: Scan Engine v2
- [x] 7b.1 Check everything on first encounter (no field_status = needs provider call)
- [x] 7b.2 Playback yield (pause scan when video starts, resume when stops)
- [x] 7b.3 cycle_processed resume (skip already-checked items on resume)
- [x] 7b.4 Targeted library-event scans (only items whose Kodi id the tracker does not know)
- [x] 7b.5 Increase debounce to 5 minutes (300s)
- [x] 7b.6 Tests and validation
- [x] 7b.7 Live Kodi validation: first-encounter, resume, playback yield, targeted scan
- [x] 7b.8 Bug fix: first-encounter runs for all items (not just zero-gap), skip same-value writes, "checked" fields count as resolved

## Phase 7c: Per-field maturity and refresh model
Implemented on branch feat/7c-field-maturity (12 commits). Content-age-driven
per-field maturity: facts (fetch_count/fetched_at/next_retry) stored, maturity
computed; refresh-on-diff replaces the empty-only guard; old code (status machine,
staleness.py, retry ladder) removed. 344 unit tests green; live suite refreshed.
- [x] 7c.1 Brainstorm + write plan (per-field maturity state machine, `final` with two outcomes)
- [x] 7c.2 Generalize staleness into a per-field "volatile / due to refetch" decision (content age + fetch count); content-age finish line, schedule + settle-capture
- [x] 7c.3 Refresh writes: drop the empty-only guard; overwrite-on-diff; keep the same-value write-skip
- [x] 7c.4 field_status state model: facts not verdicts; maturity computed (FINAL / FINAL_EMPTY); old status machine removed
- [x] 7c.5 Manual scan reset: "Update now" ignores `final`, pulls latest, re-baselines the per-field clock; fixes bugs #1 (disabled-source silent skip) and #2 (playback block)
- [x] 7c.6 Rescope + relabel settings: `default_source` spinner, `refetch_schedule` string, `reset_tracker` action; dropped max_retries / min_votes
- [x] 7c.7 Tests + live Kodi validation (maturity ladder, age-based one-shot finalize, manual reset, refresh-on-diff, SKIP_NOT_DUE)
- [x] 7c.8 Doc sync: new design spec written (docs/superpowers/specs/2026-06-19-field-maturity-model-design.md); "superseded by maturity model" banners added to the eucert master specs (engine/storage/settings/overview/scan-engine-v2) and to the write-correctness and retry-exhaustion plans (banners point to the current design; spec internals not rewritten)

## Review fixes (adversarial review, 2026-09-12)
- [x] Five verified runtime defects fixed (resolve_id through provider_get, transient sentinel on the episode TMDB path, clock untouched on a failed Kodi write, service backoff, cycle reopen)
- [x] Unreachable provider (open breaker, transient ID lookup) is transient, never "empty"
- [x] API keys redacted at the logging layer
- [x] Rig scripts moved to JSON-RPC port 8101

## Phase 7d: Replace the IMDb provider
api.imdbapi.dev shut down in July 2026. Built on branch
feat/7d-provider-replacement.
- [x] 7d.1 Design: IMDb ratings via script.module.imdbratings, TMDB and TheTVDB for text, id resolution for items without usable ids
- [x] 7d.2 IMDb ratings from script.module.imdbratings 1.0.0 (`providers/imdb_ratings.py`): the store is refreshed once per cycle with an abort that yields to playback; while no dataset is loaded the rating is transient, a missing title is a real "no rating". `providers/imdbapi.py` and its settings removed
- [x] 7d.3 TheTVDB v4 provider (`providers/tvdb.py`): token login with one re-login on 401, auth failure disables TVDB for the cycle only, 400 is transient, a series' episodes are fetched per language in one paged call and cached only when every page loaded; `http.provider_post` for the login; the circuit breaker tracks any provider by name
- [x] 7d.4 Text chain per field (`engine/chain.py`, `engine/textrules.py`, `providers/languages.py`): folder language first (`text_source_order`, TMDB then TVDB by default), then the fallback language (per folder in `sources.json` since 2026-11-10, off by default; previously a global setting defaulting to en-US) in `fallback_order`; a transient step stops the chain; "Aflevering N"-style placeholders count as empty in every TMDB language; fallback values only fill empty or placeholder fields, except that a changed fallback setting lets the new chain replace a value metamender filled from the old fallback (unless the user edited it). Movies stay TMDB-only
- [x] 7d.5 Ids: discovered ids survive later scans, episodes find their show by Kodi `tvshowid`; untyped `unknown` uniqueids are accepted as TVDB ids only after the series name (or its translation or an alias) matches the Kodi title and the years agree, or, when only the year differs (Buurman en Buurman: Kodi 1976, TVDB 1979), when at least two and a majority of the show's own untyped episode ids are in that series' episode list; episodes only when the id is in the show's TVDB episode list; uniqueid values that cannot be an id ("None", "0") are ignored; TMDB `/find` reads only the results of the asking media type; TMDB `/find` and `external_ids` fill missing cross-references, an empty answer is retried after 30 days
- [x] 7d.6 NFO items (`data/nfo.py`, `engine/protect.py`): NFO detection follows Kodi's own lookup; on an NFO-managed item a field is written only when empty, a placeholder, or still equal to metamender's last write, and the NFO's rating slots and `default` flag are never changed; unreachable media skips the item for the cycle
- [x] 7d.7 Tracker (schema v3, no migration): per-field provenance (`written_value` fingerprint in Kodi's stored form), per-field transient verdicts instead of one per item, fallback values re-checked monthly up to 12 times, and a settings fingerprint per field so changing the rating source, the text or fallback order, the fallback language or a folder's language re-checks the affected fields once
- [x] 7d.8 Live validation on Omega 21.3 and Piers 22.0 RC1 (full service scans in the Flatpak Kodi against testvideo131 / testvideo149, identical results on both): IMDb ratings added to 370 movies, 427 shows and 5,451 episodes; NFO titles, NFO rating slots and their `default` flags unchanged; untyped TVDB ids accepted for 14 of 16 kids' shows; a second scan writes nothing and every old field is final except fallback plots waiting for their monthly re-check. Live suite (`tests/live_integration_test.py`) rewritten for the new providers
- [ ] 7d.9 Live findings: shows with only a TVmaze id (De 12 van Schouwendam, Stanley H.) keep their "Aflevering N" / "Episode N" placeholders
- [x] 7d.10 Headless Kodi: the scan lock falls back to a file in `special://temp` when there is no home window, the service clears a leftover lock file at startup, and a held lock is retried after a minute instead of every tick; full service scan verified in the headless Omega image. Python floor raised to 3.10

## Phase 8: Certifications (rewrite, not a port of script.eucert.fixer)
- [x] 8.0 Settle the ten decisions in the Phase 8 review action list
- [x] 8.1 Presets and config (`resources/presets`, read-only; `data/presets.py`, fails closed; country Automatic from Kodi's region)
- [x] 8.2 Certification sources: TMDB, then the country's own site, then TMDB mapped, then the other sites mapped (`providers/fsk.py`, `bbfc.py`, `medieraadet.py`; Kijkwijzer dropped: bot challenge)
- [x] 8.3 Certification chain and site gate (`engine/certchain.py`: per-site pacing, cap per cycle, memo, breaker, abort)
- [x] 8.4 Integrate certifications into scan engine (`mpaa` producer, `maturity.decide_certification`: real final at once, derived or none rechecked monthly)

## Phase 9: Setup wizard
- [x] 9.1 "Follow the scraper" rating source, the new default: the slot Kodi marks as default decides TMDB or IMDb, and the default mark never moves
- [x] 9.2 sources.json helpers: multipath sources written per folder, hand-written and disabled entries kept, failed saves reported; the folder list is Kodi's video sources, or, on a client that shares another machine's library database and has none, the top folders derived from the library's file paths
- [x] 9.3 TMDB key check (ok, refused, not checked)
- [x] 9.4 Setup wizard (key, library language, folders in another language, what to fill, certification country, summary) as a pure model plus one WindowXMLDialog; the same window is the Folder languages dialog. Styled after the author's other add-ons (navy panel, amber accent from window properties, step dots, checkbox and radio markers); heartbeat-card add-on icon; art rendered by `docs/art/build_art.py`
- [x] 9.5 Setup gate: nothing scans until the wizard has been finished once; the first Finish starts the first scan

## Phase 10: Scale, Progress, and Polish
- [ ] 10.1 Progress notifications (per-scan, summary, resume)
- [ ] 10.2 Docker test scripts (setup.sh, run-test.sh)
- [ ] 10.3 CLAUDE.md and documentation

## Audits
- [x] Audit 1 (post Phase 3): Fixed 15 logging/error handling issues across tracker, kodi, sources
- [x] Audit 2 (post Phase 4): Fixed 3 issues (redundant except clauses, missing elapsed_ms in INFO logs)
- [x] Audit 3 (post Phase 5): Full log consistency audit against Docker Kodi
  - Fixed logger init timing (settings unreadable at import time)
  - Split scan.item into scan.processing (DEBUG) and scan.updated (INFO)
  - Refactored to per-scan log files (one timestamped file per scan, keep 10)

## Integration Test Bugs Found
- [x] `mpaa` in base JSON-RPC properties broke episode queries (episodes don't have mpaa)
- [x] Episode scan used episode-level TMDB IDs instead of show-level IDs for season lookups (all 404s)
- [x] `update_item` title parameter collision when writing episode titles (renamed to log_title)
- [x] Kodi JSON-RPC cannot clear rating to 0.0 (data reality, not a bug)
- [x] Some niche content (Dutch shows) doesn't exist on TMDB (data reality, handled gracefully)

## Additions Beyond Plan
- [x] Targeted scan support: `kodi_filter` param on get_all_items, `items` param on run_scan
- [x] Docker infrastructure: media mount (/mnt/media/Data), path substitution, MetaMender volume
- [x] Live integration test suite (10 scenarios, 53 checks, runs against Docker Kodi)

