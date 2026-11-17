#!/bin/bash
# Container / PaaS entry point: create tables, optionally create an admin, start the server.
set -e

python -m app.cli init-db

# Optional: create or update an admin from environment variables (set these as
# secrets in your host's dashboard, never in the repo). Remove them afterwards if you like.
if [ -n "$ADMIN_USERNAME" ] && [ -n "$ADMIN_PASSWORD" ]; then
    printf '%s\n' "$ADMIN_PASSWORD" | python -m app.cli admin set "$ADMIN_USERNAME" --password-stdin
fi

# The app works out the client IP itself (see TRUSTED_PROXIES), so uvicorn must not rewrite it.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-proxy-headers
