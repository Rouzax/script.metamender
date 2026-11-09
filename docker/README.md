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

Prefer the **host harness** for verifying specific behaviour. It runs the real
engine modules on the host and proxies `xbmc.executeJSONRPC` to the container,
so you exercise the actual `run_scan` code without installing the addon.

The headless image cannot run the service itself: MetaMender's cross-process
lock (`service/lock.py`) uses a home-window property, the headless image has no
home window, and every scan stops with `RuntimeError: Window id does not exist`
(`service.scan_error` in kodi.log). For an end-to-end run of the real service
use the Flatpak Kodi (next sections).

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
3. Put `settings.xml` (TMDB key, `logging` on, the field toggles) and
   `sources.json` (one entry per library root with its language, e.g.
   `smb://NAS/Data/Kids/TV/` as `nl-NL`) in
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
triggered in the container (`run-test.sh` without arguments) stops at the
scan lock described above, so it changes nothing.

```bash
./scripts/setup.sh            # backup, install deps, symlink, seed settings, restart
./scripts/run-test.sh --status   # read-only metadata-gap report
./scripts/run-test.sh            # trigger a manual scan, poll metamender.log, before/after
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
| `run-test.sh` | `--status` report / trigger scan / `--log` | `--status` no; scan: stops at the lock, writes nothing |
| `kodi-rpc.sh` | Send a raw JSON-RPC payload | Depends on payload |
| `wait-for-kodi.sh` | Wait for JSON-RPC readiness (shared) | No |
| `backup-db.sh` / `restore-db.sh` | Shared DB backup/restore wrappers | restore yes |
