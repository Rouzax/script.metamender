#!/usr/bin/env bash
# Wait for Kodi's JSON-RPC endpoint to become responsive.
# Delegates to the shared kodi-headless wait script.

set -euo pipefail

KODI_HEADLESS_DIR="${KODI_HEADLESS_DIR:-$HOME/docker/kodi-headless}"

if [[ ! -d "$KODI_HEADLESS_DIR/scripts" ]]; then
    echo "ERROR: Shared Kodi headless infrastructure not found at $KODI_HEADLESS_DIR" >&2
    exit 1
fi

"$KODI_HEADLESS_DIR/scripts/wait-for-kodi.sh" "$@"
