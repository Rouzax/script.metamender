# MetaMender: live testing against headless Kodi

These scripts test MetaMender against the shared headless Kodi container
documented at `~/docker/kodi-headless/README.md` (container
`kodi-test`, JSON-RPC on `http://localhost:8101/jsonrpc`, shared MariaDB
library `testvideo131`). MetaMender is already bind-mounted there at
`/addon-metamender`, so no compose changes are needed.

## Two ways to test

| Mode | Tool | When | Side effects |
|------|------|------|--------------|
| **Host harness** | `scripts/verify.py` | Surgical engine checks: identity, gaps, a single field write, provider behaviour | None (bundled commands are read-only) |
| **Flatpak service** | Flatpak Kodi, Omega or Piers (below) | End-to-end: the real addon service runs inside Kodi | Writes to the shared library |
| **Matrix** | `matrix/run.py` (below) | The real service on small libraries built by different scrapers and settings | None: own container and sqlite library |

Prefer the **host harness** for verifying specific behaviour. It runs the real
engine modules on the host and proxies `xbmc.executeJSONRPC` to the container,
so you exercise the actual `run_scan` code without installing the addon.

The service also runs in the headless image. With no home window, its scan
lock (`service/lock.py`) keeps its token in `special://temp/metamender_scan.lock`
instead of a window property, and the service deletes that file at startup in
case a killed Kodi left it behind. Kodi logs one harmless
`EXCEPTION: Window id does not exist` per interpreter when the lock first looks
for the window. The image runs Python 3.10, the add-on's floor. There is no
scoped scan: a scan in the container covers the whole library, so bracket it
with backup and restore.

## Prerequisites

- The shared container running: `~/docker/kodi-headless/scripts/start.sh`
- `cp docker/.env.example docker/.env` and set `TMDB_API_KEY` (needed only by
  `setup.sh`; the bundled `verify.py` commands do not require a key)
- `python3` and `requests` on the host

## Backup / restore discipline

Anything that writes to the live library must be bracketed:

```bash
./scripts/backup-db.sh        # before
# ... mutating test ...
./scripts/restore-db.sh       # after
```

`setup.sh` backs up automatically. `verify.py probe` and `verify.py apikey`
write nothing to Kodi, so they need no backup.

## Live suite: `tests/live_integration_test.py`

Scenario tests on the host, through the same JSON-RPC proxy, against real TMDB
and TheTVDB. They change single items and put them back; bracket the run with
backup and restore anyway.

```bash
export TMDB_API_KEY=...            # from docker/.env
export IMDBRATINGS_LIB=~/.var/app/tv.kodi.Kodi/data/addons/script.module.imdbratings/lib
python3 tests/live_integration_test.py            # all
python3 tests/live_integration_test.py providers  # one group (see GROUPS)
```

It talks to the headless container on 8101 by default; set
`KODI_URL=http://localhost:8100/jsonrpc` to use a running Flatpak Kodi instead.

NFO detection reads the share through `/mnt/media/Data/` (`MEDIA_ROOT`), the
same target as the rig's path substitution. `IMDBRATINGS_LIB` points at the
module's `lib/` folder in any Kodi profile that has it installed; the first IMDb
scenario downloads IMDb's ratings dataset (about 9 MB) into a temp folder.

## Flatpak service run (Omega and Piers)

The real service, scanning the whole library. Both Flatpak branches serve
JSON-RPC on 8100, so run one at a time. Path substitution from
`smb://NAS/Data/` to `/mnt/media/Data/` is already in both profiles'
`advancedsettings.xml`; a new profile needs it too.

| Kodi | Flatpak branch | Profile | Library |
|------|----------------|---------|---------|
| 21.3 Omega | `stable` (system) | `~/.var/app/tv.kodi.Kodi/data` | `testvideo131` |
| 22.0 RC1 Piers | `beta` (user) | `~/.var/app/tv.kodi.Kodi/data-piers` | `testvideo149` |

1. Back up the databases (`scripts/backup-db.sh`).
2. In the profile: symlink this repo to `addons/script.metamender`, unpack
   `script.module.imdbratings` 1.0.0 into `addons/` (from the Kodi repository,
   or the zip under `https://mirrors.kodi.tv/addons/omega/` or `.../piers/`),
   and on Piers also the `script.module.requests` dependency chain
   (requests, urllib3, chardet, idna, certifi). Enable each new add-on with a
   row in `userdata/Database/Addons33.db` (`installed`, `enabled=1`).
3. Put `settings.xml` (TMDB key, `setup_complete` set to `true` so the
   service does not wait for the setup wizard, `logging` on, the field
   toggles) and
   `sources.json` (one entry per library root with its `language`, e.g.
   `smb://NAS/Data/Kids/TV/` as `nl-NL`, and optionally a
   `fallback_language` for text that language lacks) in
   `userdata/addon_data/script.metamender/`. Delete an old `backfill.db`
   there: the tracker has no migrations.
4. Start Kodi. The service scans on start; the first scan downloads IMDb's
   ratings dataset into `special://temp/imdbratings.db`.
   - Omega: `DISPLAY=:20 flatpak run --system --branch=stable tv.kodi.Kodi`
   - Piers: Kodi 22 upgrades whatever profile it opens, so move the Omega
     profile aside first (`mv data data-omega`), then
     `DISPLAY=:20 flatpak run --user --branch=beta --command=sh tv.kodi.Kodi -c 'export XDG_DATA_HOME=$HOME/.var/app/tv.kodi.Kodi/data-piers; exec /app/bin/kodi'`
     and check that `special://home` in `data-piers/temp/kodi.log` is
     `data-piers`. Afterwards remove the empty `data` the Flatpak creates and
     move `data-omega` back.
5. Follow `event=scan.complete` / `event=service.scan_complete` in
   `temp/kodi.log`; per-scan logs are in `addon_data/script.metamender/logs/`.
   Restart Kodi for a second scan. Stop it with `kill $(pgrep -x kodi.bin)`
   and wait until `pgrep -x kodi.bin` prints nothing: `pgrep -f kodi.bin`
   also matches the shell running the command, so a `kill` built on it can
   kill that shell instead.
6. Stop Kodi, restore the databases, and remove what steps 2 and 3 added.

## Docs screenshots: `scripts/screenshots.py`

Retakes every screenshot the docs use, from the Flatpak Kodi (Omega profile)
on an X display. A development-only helper add-on
(`scripts/screenshot_helper/`, never shipped) opens each MetaMender window
through the add-on's own code with invented demo data, so a shot shows the
real window and nothing of a real library. The scenes are listed in
`scripts/screenshot_helper/scenes.json`; `tests/test_screenshot_scenes.py`
fails when a window has no scene or a scene has no image.

```bash
python3 docker/scripts/screenshots.py                    # every scene, staged in build/screenshots/
python3 docker/scripts/screenshots.py --only setup-key   # one scene (comma-separate several)
python3 docker/scripts/screenshots.py --apply            # also copy them to docs/screenshots/
```

Look at the staged images before `--apply`. The script installs this
checkout (a symlink), the IMDb ratings module (from the official Kodi mirror)
and the helper, restarts Kodi, and removes what it added at the end
(`--keep` leaves it). MetaMender stays in its "setup not finished" state, in
which the service never scans or writes, so the shared library is not
touched; the script refuses to run if the profile says setup is finished.
Needs ImageMagick (`import`, `convert`) and Estuary as the active skin. The
context menu scene opens a random library show (`--show TITLE` picks one).

## Scraper and settings matrix: `matrix/run.py`

Builds a small library with one of Kodi's TV scrapers, lets the real
metamender service scan it twice, and checks what it wrote. It runs in its own
container, `kodi-matrix` (`matrix/compose.yaml`, JSON-RPC on
`127.0.0.1:8102`), whose library is a local sqlite file, so the shared
MariaDB libraries are never touched and no backup is needed.

```bash
python3 docker/matrix/run.py --out /path/to/results K1-M1 K2-M3   # chosen runs
python3 docker/matrix/run.py --out /path/to/results --plan         # every planned run
python3 docker/matrix/run.py --out /path/to/results --reanalyse    # redo reports from saved files
docker compose -f docker/matrix/compose.yaml down -v               # remove the rig afterwards
```

A run name is `K<n>-M<n>`, optionally `-C<n>`; the tables at the top of
`run.py` define them:

- `K`: the TV scraper (TMDB, TheTVDB or TVmaze; movies always use TMDB) and
  whether each folder is scraped in its own language or in English.
- `M`: metamender settings on top of the defaults (an en-US fallback for the
  Dutch folders, text source order, rating source, no `sources.json`, text
  fields only, show and movie titles managed, certifications for NL or GB).
- `C`: a setting changed after the second scan, followed by a third scan
  (the Dutch folders' fallback becomes de-DE, or is removed, or the fallback
  order flips).
  Before the change the driver edits one value metamender filled from the
  fallback, as a user would, and checks that it survives.

Each run starts from an empty volume: Kodi installs the TheTVDB and TVmaze
scrapers and `script.module.imdbratings` from its repository, scrapes the
subset (symlinks into the share, listed in `SUBSET`), and only then gets
metamender, with `docker/.env`'s TMDB key. A run takes several minutes and
writes, under `<out>/<run>/`:

| File | Contents |
|------|----------|
| `A.json` | the library as Kodi's scraper left it |
| `B.json`, `C.json`, `D.json` | the library after each metamender scan |
| `B.db`, `C.db`, `D.db` | metamender's tracker after each scan |
| `kodi-B.log` ... | `kodi.log` of each scan; `mm-logs/` holds the add-on's own logs |
| `report.json` | the checks below, with every finding listed |

`<out>/summary.md` has one row per run. A run fails when any check lists a
finding:

- `nfo_protected`: a field of an item with an NFO changed although it was not
  empty or a placeholder.
- `second_scan_quiet`: the second scan changed anything.
- `no_errors`: a metamender error or warning, or a Python traceback, in
  `kodi.log`.
- `fallback_fill_only`: real text was replaced by a fallback-language value.
- `change_reopens_only_fallback` (C runs): the setting change altered text
  metamender did not fill from the fallback, or the hand edit.
- `certs_valid` (certification runs): a certification metamender wrote lacks
  the country's prefix or is not in its rating list, or the second scan asked
  a certification site.

`notes` in the report are not failures but need a human look:
`real_text_replaced` lists text the folder-language value replaced (expected
when a folder was scraped in another language), and `still_blank` lists
titles and plots still empty or a placeholder after the first scan.

### Timeline scenarios: `matrix/scenarios.py`

Takes one matrix library through a series of steps, with a metamender scan
after each, to see behaviour over time and under changing data:

```bash
python3 docker/matrix/scenarios.py --out /path/to/results TIME CHANGES OUTLIERS
python3 docker/matrix/scenarios.py --out /path/to/results OUTAGE   # only the provider steps
```

- `TIME`: the tracker's dates are moved back in jumps (8, 31, 91, 401 days,
  then monthly) to stand in for time passing. Content does not age with it:
  a 2026 show stays young.
- `CHANGES`: a user edits, empties or blanks out values, the provider's
  rating and plot change since the last run (Kodi gets an older value and the
  tracker records it as metamender's own), Kodi re-scrapes an episode, an
  episode is removed and later pruned, a movie gets another movie's id.
- `OUTLIERS`: odd ids, text and dates; a show folder that disappears; the
  providers blocked in the container's hosts file (the step checks they are
  really unreachable, and scans through the add-on's manual scan because a
  container restart rewrites the hosts file); an invalid TMDB key; Kodi killed during a full rescan;
  a show added while the service runs (waits for the library-event scan).

Every step is checked: a field's fetch count rises by at most one and only
when the item had a due field, the scan's logged decisions match
metamender's maturity rules for the tracker before the scan, NFO values that
are not metamender's stay, and nothing unexpected is logged. A step that
needs provider traffic first drops items from the tracker and fails when its
scan processed nothing. `<out>/scenarios.md` has one table per scenario;
`<out>/<scenario>/report.json` lists each step's failures and the values it
changed.

## Host harness: `verify.py`

```bash
./scripts/verify.py probe     # library composition, identity-fallback analysis, gap counts
./scripts/verify.py apikey    # shows invalid-TMDB-key -> run_scan returns normal stats (silent success)
```

Add new surgical checks as commands in `verify.py`. Any command that writes to
Kodi must be wrapped in backup/restore.

## In-container setup

`setup.sh` installs the add-on and its dependencies in the container and seeds
its settings, which the host harness and `run-test.sh --status` can use. A scan
triggered in the container (`run-test.sh` without arguments) runs the real
service over the whole library and writes to it.

```bash
./scripts/setup.sh            # backup, install deps, symlink, seed settings, restart
./scripts/run-test.sh --status   # read-only metadata-gap report
./scripts/run-test.sh            # trigger a manual scan, follow its own log, before/after
./scripts/run-test.sh --log      # dump the addon log
./scripts/teardown.sh --restore  # remove addon and restore the DB
```

The seeded settings enable the metadata field groups with **certifications off**
(Phase 8 is not ported yet), debug logging on, and a 1-hour scan interval.

## Script reference

| Script | Purpose | Writes to library? |
|--------|---------|--------------------|
| `verify.py` | Host-harness surgical checks | No (bundled commands) |
| `setup.sh` | Install MetaMender into the container | Backs up first |
| `teardown.sh` | Remove MetaMender (`--restore` to roll back the DB) | No (optional restore) |
| `run-test.sh` | `--status` report / trigger scan / `--log` | `--status` no; scan yes (whole library) |
| `kodi-rpc.sh` | Send a raw JSON-RPC payload | Depends on payload |
| `wait-for-kodi.sh` | Wait for JSON-RPC readiness (shared) | No |
| `backup-db.sh` / `restore-db.sh` | Shared DB backup/restore wrappers | restore yes |
