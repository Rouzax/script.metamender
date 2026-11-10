#!/usr/bin/env bash
# Inspect and exercise MetaMender against the headless Kodi instance.
#
# Usage:
#   run-test.sh             # Trigger a manual scan, poll the log, show before/after
#   run-test.sh --status    # Read-only: metadata-gap report (movies/shows/episodes)
#   run-test.sh --log       # Dump the addon log (metamender.log)
#   run-test.sh --kodi-log  # MetaMender entries from kodi.log
#
# --status is read-only. The default (trigger scan) asks the add-on inside the
# container to scan the whole library and writes to it, so bracket it with
# backup-db.sh and restore-db.sh. Surgical engine checks use the host harness,
# verify.py.

set -euo pipefail

KODI_URL="${KODI_URL:-http://localhost:8101/jsonrpc}"
CONTAINER="${CONTAINER:-kodi-test}"
ADDON_ID="script.metamender"
ADDON_DATA="/config/.kodi/userdata/addon_data/$ADDON_ID"

rpc() {
    curl -sf -m 15 -X POST "$KODI_URL" -H "Content-Type: application/json" -d "$1"
}

addon_log() {
    docker exec "$CONTAINER" cat "$ADDON_DATA/logs/metamender.log" 2>/dev/null || true
}

kodi_log() {
    docker exec "$CONTAINER" cat /config/.kodi/temp/kodi.log 2>/dev/null || true
}

# Read-only metadata-gap report. Counts items missing the fields MetaMender
# backfills, so you can see what a scan should fix and confirm it afterwards.
show_status() {
    python3 - "$KODI_URL" <<'PY'
import json, sys, urllib.request

URL = sys.argv[1]

def rpc(method, props):
    payload = {"jsonrpc": "2.0", "method": method,
               "params": {"properties": props}, "id": 1}
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["result"]

def report(label, key, items, fields):
    n = len(items)
    print("=== {} ({} total) ===".format(label, n))
    for f in fields:
        if f == "rating":
            missing = sum(1 for i in items if not i.get("rating"))
        elif f == "plot":
            missing = sum(1 for i in items if not (i.get("plot") or "").strip())
        elif f == "no_ext_id":
            def has_id(i):
                u = i.get("uniqueid") or {}
                return bool(u.get("tmdb") or u.get("themoviedb") or u.get("imdb") or u.get("tvdb"))
            missing = sum(1 for i in items if not has_id(i))
        else:
            missing = sum(1 for i in items if not i.get(f))
        pct = (100.0 * missing / n) if n else 0.0
        print("  missing {:<14} {:>5}  ({:.1f}%)".format(f, missing, pct))
    print()

m = rpc("VideoLibrary.GetMovies", ["title", "rating", "plot", "tagline", "uniqueid"])
report("Movies", "movies", m.get("movies", []), ["rating", "plot", "tagline", "no_ext_id"])

s = rpc("VideoLibrary.GetTVShows", ["title", "rating", "plot", "uniqueid"])
report("TV Shows", "tvshows", s.get("tvshows", []), ["rating", "plot", "no_ext_id"])

e = rpc("VideoLibrary.GetEpisodes", ["title", "rating", "plot", "firstaired", "uniqueid"])
report("Episodes", "episodes", e.get("episodes", []), ["rating", "plot", "firstaired", "no_ext_id"])
PY
}

trigger_scan() {
    echo "=== Pre-scan status ==="
    show_status

    echo "=== Triggering manual scan (Addons.ExecuteAddon) ==="
    rpc "{\"jsonrpc\":\"2.0\",\"method\":\"Addons.ExecuteAddon\",\"params\":{\"addonid\":\"$ADDON_ID\"},\"id\":3}" || true
    echo ""
    echo "Monitoring $ADDON_DATA/logs/metamender.log ..."
    echo ""

    local start last=0
    start=$(date +%s)
    while true; do
        sleep 5
        local elapsed=$(( $(date +%s) - start ))
        local content count
        content=$(addon_log)
        count=$(echo "$content" | wc -l)
        if [[ $count -gt $last ]]; then
            echo "--- new log (${elapsed}s) ---"
            echo "$content" | tail -n $((count - last))
            last=$count
        fi
        if echo "$content" | tail -20 | grep -qE "service.scan_complete|scan.complete"; then
            echo ""
            echo "=== Scan complete (${elapsed}s) ==="
            break
        fi
        if [[ $elapsed -gt 600 ]]; then
            echo "WARNING: scan running >10min, stopping poll."
            break
        fi
    done

    echo ""
    echo "=== Post-scan status ==="
    show_status
}

case "${1:-}" in
    --status)   show_status ;;
    --log)      addon_log ;;
    --kodi-log) kodi_log | grep -i "metamend\|script.metamender" || echo "(no addon entries in kodi.log)" ;;
    *)          trigger_scan ;;
esac
