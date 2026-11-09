#!/usr/bin/env bash
# Helper: send a JSON-RPC request to the headless Kodi instance.
# Usage: kodi-rpc.sh '{"jsonrpc":"2.0","method":"...","params":{...},"id":1}'
# Optional: KODI_URL environment variable (default: http://localhost:8101/jsonrpc)

set -euo pipefail

KODI_URL="${KODI_URL:-http://localhost:8101/jsonrpc}"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 '<json-rpc-payload>'" >&2
    exit 1
fi

curl -s -X POST "$KODI_URL" \
    -H "Content-Type: application/json" \
    -d "$1"
