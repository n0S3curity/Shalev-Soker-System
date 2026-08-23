#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  Routine maintenance: pull the latest code and images, rebuild, restart.
#
#  Safe on a live deployment and safe to re-run. The four named volumes
#  (mongo_data, mongo_config, mongo_backups, app_logs) are never touched, so
#  surveys, uploaded files, backups and logs survive every update - only the
#  containers are replaced.
#
#  A safety dump is taken *before* anything changes, so there is always a
#  restore point from immediately before the update, not just from 02:00.
#
#  Usage:  bash scripts/update.sh [options]
#
#    --no-git      Skip `git pull` - just rebuild and restart what is here
#    --no-backup   Skip the safety dump (not recommended)
#    --no-prune    Keep dangling images instead of reclaiming their disk
#    --rollback    If the stack comes back unhealthy, return the code to the
#                  commit it was on before and rebuild that
#    -y, --yes     Do not ask for confirmation
#    -h, --help    Show this text
#
#  Unattended (monthly, 03:30, logged) - `crontab -e`:
#    30 3 1 * * cd /srv/shalev && bash scripts/update.sh -y >> /var/log/shalev-update.log 2>&1
# ---------------------------------------------------------------------------
set -euo pipefail

DO_GIT=1
DO_BACKUP=1
DO_PRUNE=1
DO_ROLLBACK=0
ASSUME_YES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --no-git)     DO_GIT=0 ;;
    --no-backup)  DO_BACKUP=0 ;;
    --no-prune)   DO_PRUNE=0 ;;
    --rollback)   DO_ROLLBACK=1 ;;
    -y|--yes)     ASSUME_YES=1 ;;
    # Print the header block, from the opening rule to the closing one, so the
    # help text cannot drift out of sync with a hardcoded line range.
    -h|--help)    sed -n '2,/^# -\{10,\}$/p' "$0" | sed 's/^#\{1,\} \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

log()  { echo "[update] $(date '+%Y-%m-%d %H:%M:%S') $*"; }
fail() { echo "[update] ERROR: $*" >&2; exit 1; }

# ── Preflight ─────────────────────────────────────────────────────────────
# Compose v2 is `docker compose`; the `docker-compose` shim may or may not be
# present, so resolve whichever exists rather than assuming.
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  fail "neither 'docker compose' nor 'docker-compose' is available"
fi

if ! docker info >/dev/null 2>&1; then
  # Almost always one of two things on a server: the daemon is stopped, or this
  # user is not in the docker group and so cannot reach /var/run/docker.sock.
  fail "cannot talk to the Docker daemon.
         Is it running?           sudo systemctl status docker
         Are you in the group?    sudo usermod -aG docker \$USER   (then log out and back in)"
fi

[ -f .env ] || fail ".env is missing - run scripts/setup-env.sh first"

# The tunnel lives behind a compose profile, so it is only rebuilt when it is
# actually in use. Without this the running cloudflared would be left on the
# old image after an update.
# Read the value rather than pattern-matching the line: if .env was ever saved
# with CRLF endings, an empty `CLOUDFLARE_TUNNEL_TOKEN=` still has a stray \r
# after the '=', which a naive `=.+` test reads as a token and would start
# cloudflared with no credentials (it then restart-loops). Strip whitespace and
# check what is actually left.
PROFILES=()
TUNNEL_TOKEN="$(sed -n 's/^CLOUDFLARE_TUNNEL_TOKEN=//p' .env | head -n1 | tr -d '[:space:]')"
if [ -n "$TUNNEL_TOKEN" ]; then
  PROFILES=(--profile tunnel)
  log "cloudflare tunnel token present - including the tunnel profile"
fi
unset TUNNEL_TOKEN

log "repository: $REPO_ROOT"

if [ "$ASSUME_YES" -ne 1 ]; then
  echo
  echo "About to update the running stack:"
  [ "$DO_GIT" -eq 1 ]    && echo "  - git pull"
  [ "$DO_BACKUP" -eq 1 ] && echo "  - safety database dump"
  echo "  - docker compose pull + build + recreate containers"
  [ "$DO_PRUNE" -eq 1 ]  && echo "  - prune dangling images"
  echo
  echo "Your data volumes are NOT touched."
  echo
  printf "Continue? [y/N] "
  read -r reply
  case "$reply" in
    [yY]|[yY][eE][sS]) ;;
    *) log "aborted"; exit 0 ;;
  esac
fi

# ── 1. Safety dump ────────────────────────────────────────────────────────
# Taken first: if the update goes wrong, this is the restore point. The
# credentials are expanded inside the container, so they never appear in the
# host process list.
if [ "$DO_BACKUP" -eq 1 ]; then
  if "${DC[@]}" ps --status running --services 2>/dev/null | grep -qx backup; then
    STAMP="$(date '+%Y%m%d-%H%M%S')"
    log "taking a pre-update dump -> preupdate-${STAMP}.archive.gz"
    if "${DC[@]}" exec -T -e "PREUPDATE_STAMP=${STAMP}" backup sh -c '
        mongodump --host "${MONGO_HOST:-mongo}" --port "${MONGO_PORT:-27017}" \
          --username "$MONGO_ROOT_USER" --password "$MONGO_ROOT_PASSWORD" \
          --authenticationDatabase admin \
          --db "${MONGO_DB:-surveys}" \
          --archive="/backups/preupdate-${PREUPDATE_STAMP}.archive.gz" \
          --gzip --quiet'; then
      log "pre-update dump complete"
      # The backup sidecar only prunes its own surveys-*.archive.gz files, so
      # without this the pre-update dumps would pile up in the volume forever.
      "${DC[@]}" exec -T backup sh -c '
        find /backups -name "preupdate-*.archive.gz" -type f \
          -mtime "+${BACKUP_RETENTION_DAYS:-30}" -print -delete' \
        2>/dev/null | sed 's/^/[update]   pruned /' || true
    else
      # A stopped mongo or an empty database should not silently block a
      # maintenance run, but the operator must know the safety net is missing.
      log "WARNING: pre-update dump failed - continuing without a fresh restore point"
      if [ "$ASSUME_YES" -ne 1 ]; then
        printf "Continue anyway? [y/N] "
        read -r reply
        case "$reply" in
          [yY]|[yY][eE][sS]) ;;
          *) fail "aborted after failed dump" ;;
        esac
      fi
    fi
  else
    log "backup service is not running - skipping the pre-update dump"
  fi
fi

# ── 2. Pull the latest code ───────────────────────────────────────────────
PREV_COMMIT=""
if [ "$DO_GIT" -eq 1 ]; then
  if [ -d .git ]; then
    PREV_COMMIT="$(git rev-parse HEAD)"
    # Refuse to clobber uncommitted work. .env is gitignored, so a normal
    # deployment is clean here and this never fires.
    if ! git diff --quiet || ! git diff --cached --quiet; then
      fail "there are uncommitted changes to tracked files - commit, stash, or use --no-git"
    fi
    log "pulling latest code (currently ${PREV_COMMIT:0:8})"
    git pull --ff-only
    NEW_COMMIT="$(git rev-parse HEAD)"
    if [ "$NEW_COMMIT" = "$PREV_COMMIT" ]; then
      log "already up to date at ${NEW_COMMIT:0:8}"
    else
      log "updated ${PREV_COMMIT:0:8} -> ${NEW_COMMIT:0:8}"
      git --no-pager log --oneline "${PREV_COMMIT}..${NEW_COMMIT}" | sed 's/^/[update]   /'
    fi
  else
    log "not a git repository - skipping git pull"
  fi
fi

# ── 3. Pull base images and rebuild ───────────────────────────────────────
# --ignore-buildable: api/worker/backup are built here, not pulled. Without it
# compose would try to fetch shalev-surveys-api:latest from a registry and fail.
log "pulling base images (mongo, cloudflared)"
"${DC[@]}" "${PROFILES[@]}" pull --ignore-buildable || \
  log "WARNING: some images could not be pulled - continuing with local copies"

# `up --wait` is not usable here: it treats a service with no healthcheck as an
# error, and the worker deliberately disables its own (it serves no port). So
# wait manually - healthy where a healthcheck exists, running where it does not.
wait_for_health() {
  local timeout="${1:-180}" deadline now cid name status pending
  deadline=$(( $(date +%s) + timeout ))

  while :; do
    pending=""
    for cid in $("${DC[@]}" "${PROFILES[@]}" ps -q); do
      name="$(docker inspect --format '{{.Name}}' "$cid" | sed 's|^/||')"
      status="$(docker inspect \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "$cid")"
      case "$status" in
        healthy|running)    ;;
        starting|created|restarting) pending="${pending} ${name}(${status})" ;;
        *)
          log "ERROR: ${name} is ${status}"
          return 1
          ;;
      esac
    done

    [ -z "$pending" ] && return 0

    now=$(date +%s)
    if [ "$now" -ge "$deadline" ]; then
      log "ERROR: timed out after ${timeout}s waiting for:${pending}"
      return 1
    fi
    sleep 3
  done
}

build_and_up() {
  "${DC[@]}" "${PROFILES[@]}" up -d --build --remove-orphans || return 1
  log "waiting for services to report healthy"
  wait_for_health 180
}

log "building and recreating containers"
if build_and_up; then
  log "stack is up"
else
  log "ERROR: the stack did not come up healthy"
  "${DC[@]}" "${PROFILES[@]}" ps || true
  echo
  log "recent api logs:"
  "${DC[@]}" logs api --tail 40 2>&1 | sed 's/^/[update]   /' || true

  if [ "$DO_ROLLBACK" -eq 1 ] && [ -n "$PREV_COMMIT" ]; then
    log "rolling back to ${PREV_COMMIT:0:8}"
    git reset --hard "$PREV_COMMIT"
    if build_and_up; then
      fail "update failed; rolled back to ${PREV_COMMIT:0:8}, which is running again"
    fi
    fail "update failed AND the rollback did not come up - restore from /backups"
  fi

  echo
  log "to roll back manually:"
  if [ -n "$PREV_COMMIT" ]; then
    log "  git reset --hard ${PREV_COMMIT:0:8} && bash scripts/update.sh --no-git --no-backup -y"
  fi
  exit 1
fi

# ── 4. Verify ─────────────────────────────────────────────────────────────
# --wait already gated on the container healthchecks; this additionally proves
# the API can reach Mongo, which is what actually matters to a user.
PORT="$(grep -E '^API_LOCAL_PORT=' .env | cut -d= -f2- | tr -d '\r\n' || true)"
PORT="${PORT:-8080}"
HEALTH="$(curl -fsS --max-time 10 "http://127.0.0.1:${PORT}/api/health" 2>/dev/null || true)"
if [ -z "$HEALTH" ]; then
  log "WARNING: could not reach http://127.0.0.1:${PORT}/api/health from the host"
elif echo "$HEALTH" | grep -q '"database":"up"'; then
  log "health check: ${HEALTH}"
else
  log "WARNING: degraded health response: ${HEALTH}"
fi

# ── 5. Reclaim disk ───────────────────────────────────────────────────────
# Each rebuild leaves the previous image layers dangling. Over months of
# updates that is the main way this host runs out of disk. Only untagged
# images are removed - never volumes, never an image in use.
if [ "$DO_PRUNE" -eq 1 ]; then
  log "pruning dangling images"
  RECLAIMED="$(docker image prune -f 2>/dev/null | grep -i 'Total reclaimed' || true)"
  if [ -n "$RECLAIMED" ]; then
    log "${RECLAIMED}"
  fi
fi

echo
"${DC[@]}" "${PROFILES[@]}" ps
echo
log "update complete"
