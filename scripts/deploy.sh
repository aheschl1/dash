#!/bin/bash
# Rebuilds and restarts the admindash container.
# Run after editing any files in admindash/.
#
# Exits 0 on successful build + start, non-zero on build failure.
# Does NOT verify the running app is healthy — run healthcheck.sh after.
set -euo pipefail

# The agent's web_search (Tavily) tool reads TAVILY_API_KEY, loaded into the
# container from admindash/.env via the compose env_file. Warn (don't fail) if
# it's missing — web_search just degrades; the rest of the app is unaffected.
ENV_FILE="/home/andrew/docker_deployments/admindash/.env"
if ! grep -qE '^TAVILY_API_KEY=.+' "$ENV_FILE" 2>/dev/null; then
    echo "WARN: TAVILY_API_KEY not set in $ENV_FILE — the agent's web_search tool will be disabled"
fi

cd /home/andrew/docker_deployments/builds
docker compose up --build -d admindash
