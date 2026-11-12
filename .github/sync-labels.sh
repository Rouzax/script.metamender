#!/usr/bin/env bash
# Make the repository's labels match the list below, which is the source of truth.
#
#   .github/sync-labels.sh            create or update every listed label
#   .github/sync-labels.sh --prune    also delete labels that are not listed
#
# Labels say what an issue is or what it waits on; when it ships is its milestone
# (Next or Later). Without --prune an unlisted label is only reported: deleting one
# strips it from every issue that carries it.

set -euo pipefail

REPO="${REPO:-Rouzax/script.metamender}"

# name|colour|description
LABELS="$(cat <<'LIST'
needs-info|fbca04|Waiting on the reporter for details
needs-upstream|5319e7|Waiting on TMDB, TheTVDB, IMDb's data or Kodi; cannot be fixed in the add-on alone
bug|d73a4a|Something isn't working
enhancement|a2eeef|New feature or request
documentation|0075ca|Improvements or additions to documentation
accessibility|f143ab|Barrier affecting people with disabilities
age-ratings|c5def5|Age ratings, a country preset or its conversions
question|d876e3|Further information is requested
duplicate|cfd3d7|This issue or pull request already exists
invalid|e4e669|This doesn't seem right
wontfix|ffffff|This will not be worked on
good first issue|7057ff|Good for newcomers
help wanted|008672|Extra attention is needed
LIST
)"

prune=false
case "${1:-}" in
    "") ;;
    --prune) prune=true ;;
    *) echo "usage: sync-labels.sh [--prune]" >&2; exit 2 ;;
esac

while IFS='|' read -r name color description; do
    gh label create "$name" --repo "$REPO" --color "$color" --description "$description" --force >/dev/null
    echo "ok       $name"
done <<< "$LABELS"

listed="$(cut -d'|' -f1 <<< "$LABELS")"
gh label list --repo "$REPO" --limit 200 --json name --jq '.[].name' | while IFS= read -r name; do
    grep -qxF "$name" <<< "$listed" && continue
    if $prune; then
        gh label delete "$name" --repo "$REPO" --yes >/dev/null
        echo "deleted  $name"
    else
        echo "unlisted $name (kept; --prune deletes it)"
    fi
done
