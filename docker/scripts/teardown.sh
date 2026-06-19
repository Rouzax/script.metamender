#!/usr/bin/env bash
# Remove MetaMender from the shared headless Kodi container.
# Usage: teardown.sh [--restore]
#   --restore  also restore the test databases from the latest backup

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KODI_HEADLESS_DIR="${KODI_HEADLESS_DIR:-$HOME/docker/kodi-headless}"
CONTAINER="${CONTAINER:-kodi-test}"
ADDON_ID="script.metamender"

echo "=== Removing MetaMender from container ==="

echo "Removing addon symlink..."
docker exec "$CONTAINER" rm -f "/config/.kodi/addons/$ADDON_ID"
echo "  Symlink removed."

echo "Removing addon data..."
docker exec "$CONTAINER" rm -rf "/config/.kodi/userdata/addon_data/$ADDON_ID"
echo "  Addon data removed."

if [[ "${1:-}" == "--restore" ]]; then
    echo ""
    echo "=== Restoring database ==="
    "$KODI_HEADLESS_DIR/scripts/restore-db.sh"
else
    echo ""
    echo "Restarting container..."
    "$KODI_HEADLESS_DIR/scripts/restart.sh"
fi

echo ""
echo "MetaMender removed from container."
echo "To re-install: $(dirname "$SCRIPT_DIR")/scripts/setup.sh"
