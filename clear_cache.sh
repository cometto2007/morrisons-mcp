#!/usr/bin/env bash
# clear_cache.sh — purge stale SQLite cache entries from the MCP server volume.
# Run after deploying code fixes that bump the cache key.
# Usage: bash clear_cache.sh [compose-file-dir]

set -euo pipefail

DIR="${1:-.}"
DB_PATH="/data/cache.db"
IMAGE="alpine"

echo "==> Locating cache volume..."
VOLUME=$(docker compose -f "$DIR/docker-compose.yml" config --format json \
  | python3 -c "
import json, sys
cfg = json.load(sys.stdin)
for svc in cfg.get('services', {}).values():
    for v in svc.get('volumes', []):
        if '/data' in v.get('target', ''):
            print(v['source'])
            sys.exit(0)
print('', end='')
")

if [ -z "$VOLUME" ]; then
  echo "ERROR: Could not determine cache volume from docker-compose.yml" >&2
  exit 1
fi

echo "==> Volume: $VOLUME"
echo "==> Clearing stale fallback:* cache entries (pre-v2 keys)..."

docker run --rm \
  -v "${VOLUME}:/data" \
  "$IMAGE" \
  sh -c "apk add --quiet sqlite && sqlite3 $DB_PATH \"DELETE FROM cache WHERE key LIKE 'fallback:%'; SELECT changes() || ' rows deleted';\" 2>&1"

echo "==> Cache clear complete."
echo "==> Restarting morrisons-mcp container..."
docker compose -f "$DIR/docker-compose.yml" restart morrisons-mcp
echo "==> Done."
