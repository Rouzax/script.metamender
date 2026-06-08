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
- [x] 7b.4 Targeted library-event scans (dateadded filter for new items only)
- [x] 7b.5 Increase debounce to 5 minutes (300s)
- [x] 7b.6 Tests and validation
- [x] 7b.7 Live Kodi validation: first-encounter, resume, playback yield, targeted scan
- [x] 7b.8 Bug fix: first-encounter runs for all items (not just zero-gap), skip same-value writes, "checked" fields count as resolved

## Phase 8: Port Certification Module
- [ ] 8.1 Port presets and config (country JSON presets, load/inference/seed)
- [ ] 8.2 Port certification providers (FSK, BBFC, Kijkwijzer, Medieraadet)
- [ ] 8.3 Port certification backfill logic (multi-tier lookup)
- [ ] 8.4 Integrate certifications into scan engine

## Phase 9: Source Configuration Dialog
- [ ] 9.1 Custom Kodi dialog for source config (toggle, language picker)

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

## Test Count: 246 unit + 185 integration = 431 total
