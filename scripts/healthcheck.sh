#!/bin/bash
# Post-deployment health checks for admindash.
# Run after deploy.sh. Exits 0 if all checks pass, 1 if any fail.
#
# Add new checks here as new endpoints / functionality are added.

set -uo pipefail

# Served via the shared nginx proxy over HTTPS. Bare-IP https://10.8.0.1 hits
# the admindash default_server, so this works with no DNS dependency (the cert
# won't match the IP -> curl -k below stays lenient).
DASH_URL="${DASH_URL:-https://10.8.0.1}"
FAIL=0

step() { echo "[$1] $2"; }
ok()   { echo "  ok${1:+: $1}"; }
fail() { echo "  FAIL: $1"; FAIL=1; }

step 1 "Waiting for container to settle"
sleep 8
ok

step 2 "Container status"
status=$(docker ps --filter name=admindash --format '{{.Status}}' 2>/dev/null || echo "")
if [[ -z "$status" || "$status" != Up* ]]; then
    fail "container not running (status: ${status:-none})"
else
    ok "$status"
fi

step 3 "HTTP root"
code=$(curl -skf -o /dev/null -w "%{http_code}" --max-time 10 "$DASH_URL/" 2>/dev/null || echo "000")
if [[ "$code" != "200" ]]; then
    fail "$DASH_URL/ returned $code"
else
    ok "200"
fi

step 4 "API /api/stats"
if ! curl -skf --max-time 10 "$DASH_URL/api/stats" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'cpu_pct' in d, 'missing cpu_pct'" 2>/dev/null; then
    fail "/api/stats missing or invalid JSON"
else
    ok
fi

step 5 "API /api/containers"
if ! curl -skf --max-time 10 "$DASH_URL/api/containers" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d,list), 'not a list'" 2>/dev/null; then
    fail "/api/containers missing or invalid"
else
    ok
fi

step 6 "API /api/feedback"
if ! curl -skf --max-time 10 "$DASH_URL/api/feedback" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d,list)" 2>/dev/null; then
    fail "/api/feedback missing or invalid"
else
    ok
fi

step 7 "API /api/connections"
if ! curl -skf --max-time 10 "$DASH_URL/api/connections" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'connections' in d and isinstance(d['connections'],list), 'missing connections list'" 2>/dev/null; then
    fail "/api/connections missing or invalid"
else
    ok
fi

step 7b "API /api/hardware"
if ! curl -skf --max-time 10 "$DASH_URL/api/hardware" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'logical_cores' in d and 'per_core_pct' in d, 'missing cpu fields'" 2>/dev/null; then
    fail "/api/hardware missing or invalid"
else
    ok
fi

step 7c "API /api/vms"
if ! curl -skf --max-time 10 "$DASH_URL/api/vms" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'available' in d and isinstance(d.get('vms'),list), 'missing vms list'" 2>/dev/null; then
    fail "/api/vms missing or invalid"
else
    ok
fi

step 7d "API /api/cron"
if ! curl -skf --max-time 10 "$DASH_URL/api/cron" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d,list), 'not a list'" 2>/dev/null; then
    fail "/api/cron missing or invalid"
else
    ok
fi

step 8 "OpenAPI spec /openapi.json"
if ! curl -skf --max-time 10 "$DASH_URL/openapi.json" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'openapi' in d and d.get('info',{}).get('title')=='admindash API', 'spec missing or wrong title'" 2>/dev/null; then
    fail "/openapi.json missing, not proxied (SPA fallback?), or wrong title"
else
    ok
fi

step 9 "Swagger UI /docs"
ctype=$(curl -skf -o /dev/null -w "%{content_type}" --max-time 10 "$DASH_URL/docs" 2>/dev/null || echo "")
if [[ "$ctype" != text/html* ]]; then
    fail "/docs not serving HTML (got: ${ctype:-none})"
else
    ok
fi

step 10 "ReDoc /redoc"
ctype=$(curl -skf -o /dev/null -w "%{content_type}" --max-time 10 "$DASH_URL/redoc" 2>/dev/null || echo "")
if [[ "$ctype" != text/html* ]]; then
    fail "/redoc not serving HTML (got: ${ctype:-none})"
else
    ok
fi

step 11 "Auth: protected endpoint rejects anonymous requests"
code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST "$DASH_URL/api/containers/nonexistent/action" \
    -H 'Content-Type: application/json' -d '{"action":"start"}' 2>/dev/null || echo "000")
if [[ "$code" != "401" ]]; then
    fail "POST /api/containers/.../action returned $code without a token (expected 401)"
else
    ok "401"
fi

step 12 "Auth: login endpoint rejects bad credentials"
code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 10 \
    -X POST "$DASH_URL/api/auth/login" \
    -H 'Content-Type: application/json' -d '{"username":"nope","password":"nope"}' 2>/dev/null || echo "000")
if [[ "$code" != "401" ]]; then
    fail "POST /api/auth/login returned $code for bad creds (expected 401)"
else
    ok "401"
fi

step 12b "Agent: conversation lifecycle (no LLM call)"
# Create a conversation, fetch it from the store, list (exercises the
# chat_sessions table), then retire it. A fresh chat is cached-only and persists
# to the DB lazily on its first turn, so it intentionally does NOT appear in
# /api/conversations yet — the list call just has to query the table cleanly.
# Avoids /api/send so this stays independent of the llama-cpp model being loaded.
cid=$(curl -skf --max-time 10 -X POST "$DASH_URL/api/newconversation" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")
if [[ -z "$cid" ]]; then
    fail "/api/newconversation did not return an id"
else
    fetched=$(curl -skf --max-time 10 "$DASH_URL/api/conversations/$cid" \
        | python3 -c "import sys,json; print('yes' if json.load(sys.stdin).get('id')=='$cid' else 'no')" 2>/dev/null || echo "no")
    listok=$(curl -skf --max-time 10 "$DASH_URL/api/conversations" \
        | python3 -c "import sys,json; print('yes' if isinstance(json.load(sys.stdin).get('conversations'), list) else 'no')" 2>/dev/null || echo "no")
    ended=$(curl -sk --max-time 10 -X DELETE "$DASH_URL/api/end/$cid" \
        | python3 -c "import sys,json; print('yes' if json.load(sys.stdin).get('ended') else 'no')" 2>/dev/null || echo "no")
    if [[ "$fetched" == "yes" && "$listok" == "yes" && "$ended" == "yes" ]]; then
        ok
    else
        fail "conv not fetched ($fetched) / list not ok ($listok) / not ended ($ended)"
    fi
fi

step 12c "Agent WebSocket upgrade + endpoint (no LLM call)"
# Connect through the inner nginx (localhost:80 inside the container) to verify
# the /api/ws/ upgrade block and the endpoint. Sending a bogus conv_id yields an
# error event without invoking llama-cpp.
if docker exec -i admindash /app/backend/.venv/bin/python - <<'PY' 2>/dev/null
import asyncio, json, websockets
async def main():
    async with websockets.connect("ws://localhost:80/api/ws/agent") as ws:
        await ws.send(json.dumps({"conv_id": "nope", "message": "ping"}))
        ev = json.loads(await asyncio.wait_for(ws.recv(), 5))
        assert ev.get("type") == "error", ev
asyncio.run(main())
PY
then
    ok
else
    fail "WebSocket /api/ws/agent did not handshake/respond"
fi

step 12d "Agent: TAVILY_API_KEY present in container env"
# Confirms the compose env_file wired admindash/.env into the container. Checks
# presence only — never prints the value.
if docker exec admindash sh -c '[ -n "$TAVILY_API_KEY" ]' 2>/dev/null; then
    ok
else
    fail "TAVILY_API_KEY not set in container (env_file / .env wiring) — web_search disabled"
fi

step 13 "Backend tracebacks in recent logs"
if docker logs admindash --tail 50 2>&1 | grep -q "Traceback"; then
    fail "Python traceback found in logs"
    docker logs admindash --tail 30 2>&1 | grep -A 5 "Traceback" | head -40
else
    ok
fi

echo ""
if [[ $FAIL -eq 0 ]]; then
    echo "All health checks passed."
    exit 0
else
    echo "Health check FAILED. Run: docker logs admindash --tail 100"
    exit 1
fi
