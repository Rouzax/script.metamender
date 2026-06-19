#!/usr/bin/env bash
# Restore the test MySQL databases from a backup.
# Delegates to the shared kodi-headless restore script.

set -euo pipefail

KODI_HEADLESS_DIR="${KODI_HEADLESS_DIR:-$HOME/docker/kodi-headless}"

if [[ ! -d "$KODI_HEADLESS_DIR/scripts" ]]; then
    echo "ERROR: Shared Kodi headless infrastructure not found at $KODI_HEADLESS_DIR" >&2
    exit 1
fi

"$KODI_HEADLESS_DIR/scripts/restore-db.sh" "$@"
