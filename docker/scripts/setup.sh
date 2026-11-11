#!/usr/bin/env bash
# Install MetaMender into the shared headless Kodi container.
#
# What this does:
#   1. Verifies the shared container is running
#   2. Backs up the test MySQL databases (the addon writes to the shared library)
#   3. Installs the script.module.requests and script.module.imdbratings dependencies
#   4. Symlinks the addon source into Kodi's addons directory
#   5. Creates addon settings (TMDB key, debug logging, fast scan interval)
#   6. Restarts Kodi so everything takes effect
#
# Prerequisites:
#   - Shared container running (start with $KODI_HEADLESS_DIR/scripts/start.sh)
#   - docker/.env with TMDB_API_KEY
#   - MetaMender mounted at /addon-metamender (already in the shared compose)
#
# NOTE: once Kodi restarts, the service scans the whole shared library and
#   writes to it; step 2's backup is what restore-db.sh rolls back to.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR="$SCRIPT_DIR/.."
KODI_HEADLESS_DIR="${KODI_HEADLESS_DIR:-$HOME/docker/kodi-headless}"
CONTAINER="${CONTAINER:-kodi-test}"
ADDON_ID="script.metamender"
ADDON_MOUNT="/addon-metamender"

if [[ ! -d "$KODI_HEADLESS_DIR/scripts" ]]; then
    echo "ERROR: Shared Kodi headless infrastructure not found at $KODI_HEADLESS_DIR" >&2
    echo "Set KODI_HEADLESS_DIR or create it first." >&2
    exit 1
fi

ENV_FILE="$DOCKER_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found." >&2
    echo "Copy .env.example to .env and fill in your TMDB API key." >&2
    exit 1
fi
source "$ENV_FILE"

if [[ -z "${TMDB_API_KEY:-}" || "$TMDB_API_KEY" == "your_tmdb_api_key_here" ]]; then
    echo "ERROR: TMDB_API_KEY not set in $ENV_FILE" >&2
    exit 1
fi

echo "=== Step 1: Verify container is running ==="
"$KODI_HEADLESS_DIR/scripts/wait-for-kodi.sh" 10 || {
    echo "ERROR: Kodi container is not running." >&2
    echo "Start it first: $KODI_HEADLESS_DIR/scripts/start.sh" >&2
    exit 1
}

echo ""
echo "=== Step 2: Backup databases ==="
"$KODI_HEADLESS_DIR/scripts/backup-db.sh"

echo ""
echo "=== Step 3: Install dependencies ==="
docker exec "$CONTAINER" install_addon "script.module.requests" || {
    echo "WARNING: install_addon failed. Attempting pip fallback..."
    docker exec "$CONTAINER" pip3 install requests 2>/dev/null || true
}
docker exec "$CONTAINER" install_addon "script.module.imdbratings" || \
    echo "WARNING: install_addon script.module.imdbratings failed; IMDb ratings stay transient."

echo ""
echo "=== Step 4: Link addon source ==="
docker exec "$CONTAINER" ln -sfn "$ADDON_MOUNT" "/config/.kodi/addons/$ADDON_ID"
echo "Addon symlinked from $ADDON_MOUNT."

echo ""
echo "=== Step 5: Create addon settings ==="
ADDON_DATA="/config/.kodi/userdata/addon_data/$ADDON_ID"
docker exec "$CONTAINER" mkdir -p "$ADDON_DATA"

# Test-friendly settings. Certifications are OFF: Phase 8 (the cert module)
# is not ported yet, so enable_certifications=false and the scrapers stay off.
SETTINGS_XML="<settings version=\"1\">
    <setting id=\"tmdb_api_key\">${TMDB_API_KEY}</setting>
    <setting id=\"setup_complete\">true</setting>
    <setting id=\"rating_source\">0</setting>
    <setting id=\"text_source_order\">0</setting>
    <setting id=\"enable_ratings\">true</setting>
    <setting id=\"enable_plots\">true</setting>
    <setting id=\"enable_episode_titles\">true</setting>
    <setting id=\"enable_certifications\">false</setting>
    <setting id=\"enable_credits\">true</setting>
    <setting id=\"enable_trailers\">true</setting>
    <setting id=\"enable_taglines\">true</setting>
    <setting id=\"enable_details\">true</setting>
    <setting id=\"stale_threshold_days\">365</setting>
    <setting id=\"country\">0</setting>
    <setting id=\"enable_fsk\">false</setting>
    <setting id=\"enable_bbfc\">false</setting>
    <setting id=\"enable_medieraadet\">false</setting>
    <setting id=\"enable_kijkwijzer\">false</setting>
    <setting id=\"scan_interval\">1</setting>
    <setting id=\"rate_limit\">0.25</setting>
    <setting id=\"show_notifications\">true</setting>
    <setting id=\"resume_interrupted\">true</setting>
    <setting id=\"orphan_retention_days\">30</setting>
    <setting id=\"logging\">true</setting>
</settings>"

echo "$SETTINGS_XML" | docker exec -i "$CONTAINER" tee "$ADDON_DATA/settings.xml" > /dev/null
echo "Addon settings created (debug logging on, scan_interval=1h, certs off)."

echo ""
echo "=== Step 6: Restart Kodi ==="
"$KODI_HEADLESS_DIR/scripts/restart.sh"

echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "  Kodi JSON-RPC: http://localhost:8101/jsonrpc"
echo "  Addon log:     docker exec $CONTAINER cat $ADDON_DATA/logs/metamender.log"
echo ""
echo "  Library status:   ./scripts/run-test.sh --status"
echo "  Trigger a scan:   ./scripts/run-test.sh"
echo "  Host-harness:     ./scripts/verify.py probe"
echo "  Restore DB after: ./scripts/restore-db.sh"
echo ""
