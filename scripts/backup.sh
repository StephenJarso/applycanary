#!/usr/bin/env bash
# Back up the ApplyCanary data volume.
#
# The volume holds the only state not reproducible from this repository: the
# SQLite database, uploaded resumes, and generated CVs. Job postings can be
# re-fetched from the boards; your application history cannot.
#
#   ./scripts/backup.sh              # write to ./backups
#   ./scripts/backup.sh /mnt/backup  # write elsewhere
#
# Restore:
#   docker compose down
#   docker run --rm -v applycanary-data:/data -v "$PWD/backups":/backup alpine \
#     sh -c 'rm -rf /data/* && tar xzf /backup/applycanary-YYYY-MM-DD.tar.gz -C /data'
#   docker compose up -d

set -euo pipefail

VOLUME="applycanary-data"
DEST="${1:-$(cd "$(dirname "$0")/.." && pwd)/backups}"
KEEP=14

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker not found" >&2
  exit 1
fi

# A missing volume means the app has never run. Backing up nothing and exiting 0
# would hide that from a cron log, so fail loudly.
if ! docker volume inspect "$VOLUME" >/dev/null 2>&1; then
  echo "error: volume '$VOLUME' does not exist — has the app run yet?" >&2
  exit 1
fi

mkdir -p "$DEST"
STAMP="$(date +%F-%H%M)"
ARCHIVE="applycanary-${STAMP}.tar.gz"

# Runs as root inside the container so file ownership in the volume is
# preserved regardless of who invokes this script.
docker run --rm \
  -v "${VOLUME}:/data:ro" \
  -v "${DEST}:/backup" \
  alpine \
  tar czf "/backup/${ARCHIVE}" -C /data .

SIZE="$(du -h "${DEST}/${ARCHIVE}" | cut -f1)"
echo "$(date '+%F %T')  wrote ${DEST}/${ARCHIVE} (${SIZE})"

# Verify the archive is readable rather than assuming tar succeeded. A silently
# corrupt backup is worse than none, because it is trusted.
if ! tar tzf "${DEST}/${ARCHIVE}" >/dev/null 2>&1; then
  echo "error: archive failed verification — not rotating old backups" >&2
  exit 1
fi

# Rotate, keeping the newest $KEEP.
COUNT="$(find "$DEST" -maxdepth 1 -name 'applycanary-*.tar.gz' | wc -l)"
if [ "$COUNT" -gt "$KEEP" ]; then
  find "$DEST" -maxdepth 1 -name 'applycanary-*.tar.gz' -printf '%T@ %p\n' \
    | sort -n \
    | head -n "$((COUNT - KEEP))" \
    | cut -d' ' -f2- \
    | while read -r old; do
        echo "  rotating out $(basename "$old")"
        rm -f "$old"
      done
fi
