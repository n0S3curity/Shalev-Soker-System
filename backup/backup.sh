#!/bin/bash
# Continuous backup sidecar.
#
# Dumps the whole application database (survey documents *and* the GridFS
# attachments, which live in the same database) into a named Docker volume, so
# rebuilding or updating the containers never touches the backups.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
BACKUP_HOUR="${BACKUP_HOUR:-2}"
BACKUP_MINUTE="${BACKUP_MINUTE:-0}"

MONGO_HOST="${MONGO_HOST:-mongo}"
MONGO_PORT="${MONGO_PORT:-27017}"
MONGO_DB="${MONGO_DB:-surveys}"

mkdir -p "$BACKUP_DIR"

log() { echo "[backup] $(date -u '+%Y-%m-%dT%H:%M:%SZ') $*"; }

take_backup() {
  local stamp archive
  stamp="$(date -u '+%Y%m%d-%H%M%S')"
  archive="${BACKUP_DIR}/${MONGO_DB}-${stamp}.archive.gz"

  log "starting dump -> ${archive}"
  if mongodump \
      --host "${MONGO_HOST}" --port "${MONGO_PORT}" \
      --username "${MONGO_ROOT_USER}" --password "${MONGO_ROOT_PASSWORD}" \
      --authenticationDatabase admin \
      --db "${MONGO_DB}" \
      --archive="${archive}" --gzip --quiet; then
    log "dump finished ($(du -h "${archive}" | cut -f1))"
    echo "${stamp}" > "${BACKUP_DIR}/LAST_SUCCESS"
  else
    log "ERROR: dump failed"
    rm -f "${archive}"
    return 1
  fi

  # Retention
  find "${BACKUP_DIR}" -name "${MONGO_DB}-*.archive.gz" -type f -mtime "+${RETENTION_DAYS}" -print -delete \
    | while read -r old; do log "pruned ${old}"; done
}

seconds_until_next_run() {
  local now target
  now=$(date -u +%s)
  target=$(date -u -d "today ${BACKUP_HOUR}:${BACKUP_MINUTE}:00" +%s 2>/dev/null || echo 0)
  if [ "$target" -le "$now" ]; then
    target=$(date -u -d "tomorrow ${BACKUP_HOUR}:${BACKUP_MINUTE}:00" +%s)
  fi
  echo $((target - now))
}

log "waiting for mongo at ${MONGO_HOST}:${MONGO_PORT}"
until mongosh --host "${MONGO_HOST}" --port "${MONGO_PORT}" \
        --username "${MONGO_ROOT_USER}" --password "${MONGO_ROOT_PASSWORD}" \
        --authenticationDatabase admin --quiet --eval 'db.adminCommand("ping")' >/dev/null 2>&1; do
  sleep 5
done
log "mongo is reachable"

# One dump at startup so a fresh deployment already has a restore point.
take_backup || log "initial backup failed, will retry on schedule"

while true; do
  sleep_for=$(seconds_until_next_run)
  log "next backup in ${sleep_for}s"
  sleep "${sleep_for}"
  take_backup || true
done
