#!/bin/bash
# Rebuilds and restarts the admindash container.
# Run after editing any files in admindash/.
#
# Exits 0 on successful build + start, non-zero on build failure.
# Does NOT verify the running app is healthy — run healthcheck.sh after.
#
# Portable across machines — paths are derived from this script's location, with
# env overrides for non-default layouts:
#   COMPOSE_DIR        dir holding docker-compose.yml   (default: ../builds relative to the repo)
#   COMPOSE_SERVICE    compose service name             (default: admindash)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

COMPOSE_SERVICE="${COMPOSE_SERVICE:-admindash}"
COMPOSE_DIR="${COMPOSE_DIR:-$REPO_DIR/../builds}"

if [[ ! -f "$COMPOSE_DIR/docker-compose.yml" && ! -f "$COMPOSE_DIR/compose.yml" ]]; then
    echo "ERROR: no docker-compose.yml in '$COMPOSE_DIR'." >&2
    echo "Set COMPOSE_DIR to the directory that holds the compose file, e.g.:" >&2
    echo "  COMPOSE_DIR=/path/to/builds bash scripts/deploy.sh" >&2
    exit 1
fi

# The agent's web_search (Tavily) tool reads TAVILY_API_KEY, loaded into the
# container from admindash/.env via the compose env_file. Warn (don't fail) if
# it's missing — web_search just degrades; the rest of the app is unaffected.
# Copy .env.example → .env and fill it in (see README) on a fresh machine.
ENV_FILE="$REPO_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "WARN: $ENV_FILE not found — copy .env.example to .env and fill it in (see README)"
elif ! grep -qE '^TAVILY_API_KEY=.+' "$ENV_FILE" 2>/dev/null; then
    echo "WARN: TAVILY_API_KEY not set in $ENV_FILE — the agent's web_search tool will be disabled"
fi

cd "$COMPOSE_DIR"
docker compose up --build -d "$COMPOSE_SERVICE"
