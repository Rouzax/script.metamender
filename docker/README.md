# MetaMender: live testing against headless Kodi

These scripts test MetaMender against the shared headless Kodi container
documented at `~/docker/kodi-headless/README.md` (container
`kodi-test`, JSON-RPC on `http://localhost:8085/jsonrpc`, shared MariaDB
library `testvideo131`). MetaMender is already bind-mounted there at
`/addon-metamender`, so no compose changes are needed.

## Two ways to test

| Mode | Tool | When | Side effects |
|------|------|------|--------------|
| **Host harness** | `scripts/verify.py` | Surgical engine checks: identity, gaps, a single field write, provider behaviour | None (bundled commands are read-only) |
| **In-container service** | `scripts/setup.sh` + `scripts/run-test.sh` | End-to-end: the real addon service runs inside Kodi | Writes to the shared library |

Prefer the **host harness** for verifying specific behaviour. It runs the real
engine modules on the host and proxies `xbmc.executeJSONRPC` to the container,
so you exercise the actual `run_scan` code without installing the addon. It also
sidesteps a headless limitation: MetaMender's cross-process lock
(`service/lock.py`) uses `xbmcgui.Window` properties, which are unreliable on
the headless image, so the in-container **service** may not lock cleanly. (UI,
window-property, and lock testing need the native Flatpak Kodi instead, per the
shared README.)

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

## Host harness: `verify.py`

```bash
./scripts/verify.py probe     # library composition, identity-fallback analysis, gap counts
./scripts/verify.py apikey    # shows invalid-TMDB-key -> run_scan returns normal stats (silent success)
```

Add new surgical checks as commands in `verify.py`. Any command that writes to
Kodi must be wrapped in backup/restore.

## In-container service

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
| `run-test.sh` | `--status` report / trigger scan / `--log` | `--status` no; scan yes |
| `kodi-rpc.sh` | Send a raw JSON-RPC payload | Depends on payload |
| `wait-for-kodi.sh` | Wait for JSON-RPC readiness (shared) | No |
| `backup-db.sh` / `restore-db.sh` | Shared DB backup/restore wrappers | restore yes |
