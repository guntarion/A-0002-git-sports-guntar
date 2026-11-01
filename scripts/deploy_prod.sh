#!/usr/bin/env bash
# Manual production deploy that does NOT break the data.
#
# `vercel deploy` from a laptop uploads no .git, so scripts/vercel_build.sh cannot
# hydrate site/*.json from the dashboard-data branch and the site ships with an
# empty data.json and no activities.json (404s everywhere). This script fetches
# that data first, refuses to deploy without it, then verifies the live site.
#
# Usage (any cwd):  bash scripts/deploy_prod.sh
set -euo pipefail
cd "$(dirname "$0")/.."

BRANCH="${DASHBOARD_DATA_BRANCH:-dashboard-data}"
SITE_URL="${SITE_URL:-https://git-sports-guntar.vercel.app}"
FILES=(data.json activities.json analytics.json ai_insights.json ai_insights_history.json)

git fetch -q origin "$BRANCH"
for f in "${FILES[@]}"; do
  if git show "origin/$BRANCH:site/$f" > "site/$f.tmp" 2>/dev/null; then
    mv "site/$f.tmp" "site/$f"; echo "  ok: $f"
  else
    rm -f "site/$f.tmp"; echo "  (not on $BRANCH: $f)"
  fi
done

# Hard stop: these two are what the pages cannot live without.
for f in data.json activities.json; do
  [ -s "site/$f" ] || { echo "ABORT: site/$f missing; refusing to deploy a broken site." >&2; exit 1; }
done

vercel deploy --prod --yes

echo "Verifying $SITE_URL ..."
sleep 15
fail=0
for f in data.json activities.json; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$SITE_URL/$f")
  echo "  $f -> $code"; [ "$code" = 200 ] || fail=1
done
[ "$fail" = 0 ] && echo "Deploy OK." || { echo "WARNING: live site is missing data." >&2; exit 1; }
