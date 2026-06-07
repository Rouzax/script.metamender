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
- [ ] 5.1 Gap detection (which fields need work per item)
- [ ] 5.2 Staleness detection (vote count thresholds, retry scheduling)
- [ ] 5.3 Scan orchestrator (query, filter, prioritize, process, stats)
- [ ] 5.4 Service daemon and manual trigger (first end-to-end test)

## Phase 6: Staleness and Retry Integration
- [ ] 6.1 Integrate staleness into scan loop (re-check stale, backoff, exhaustion)

## Phase 7: OMDB Provider
- [ ] 7.1 OMDB movie and episode lookup (imdbRating, imdbVotes, plot)
- [ ] 7.2 Source priority and fallback (prefer TMDB/OMDB, resolve per field)
- [ ] 7.3 OMDB daily budget tracking (950/day limit, persist counter)

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

## Integration Test Bugs Found
- [x] `mpaa` in base JSON-RPC properties broke episode queries (episodes don't have mpaa)
- [x] Episode scan used episode-level TMDB IDs instead of show-level IDs for season lookups (all 404s)

## Test Count: 168 passing
