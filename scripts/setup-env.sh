#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  Create .env from .env.example with freshly generated secrets.
#
#  Secrets are never committed, so a fresh clone has no .env and the stack
#  refuses to start. Run this once, then `docker compose up -d --build`.
#
#  Existing .env files are left alone - this never overwrites your secrets.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo ".env already exists - leaving it untouched."
  exit 0
fi

if [ ! -f .env.example ]; then
  echo "error: .env.example is missing." >&2
  exit 1
fi

gen() { LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "${1:-48}"; }

cp .env.example .env

# Replace the generated-secret placeholders. SMTP_PASSWORD is deliberately left
# as CHANGE-ME: only you can supply a Gmail app password.
sed -i.bak \
  -e "s|^MONGO_ROOT_PASSWORD=.*|MONGO_ROOT_PASSWORD=$(gen 32)|" \
  -e "s|^MONGO_APP_PASSWORD=.*|MONGO_APP_PASSWORD=$(gen 32)|" \
  -e "s|^SESSION_SECRET=.*|SESSION_SECRET=$(gen 64)|" \
  .env
rm -f .env.bak

echo ".env created with generated secrets."
echo
echo "Still to fill in by hand before mail features work:"
echo "  SMTP_PASSWORD   - Gmail app password (daily report + password resets)"
echo "  PUBLIC_ORIGIN   - currently https://surveys.example.com;"
echo "                    set to http://localhost:8080 for a local run"
echo
echo "Then:  docker compose up -d --build"
