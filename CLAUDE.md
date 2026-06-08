# admindash

Personal server management dashboard deployed as a single Docker container at `http://10.8.0.1` (WireGuard server IP). Accessible from any VPN peer.

## Architecture

One container runs two processes via **supervisord**:
- **Nginx** on port 80 — serves React static build at `/`, proxies `/api/*` → `localhost:8000`
- **uvicorn** on port 8000 — FastAPI backend

## Deployment

**Always use the scripts** — do not run `docker compose` directly:

```bash
bash scripts/deploy.sh        # rebuild + restart admindash
bash scripts/healthcheck.sh   # verify the running app (exits non-zero on failure)
```

The container depends on `postgress` (postgres:16) being healthy. The compose file is at `/home/andrew/docker_deployments/builds/docker-compose.yml`. The admindash service runs with `pid: host`, `privileged: true`, `runtime: nvidia`, and mounts `/var/run/docker.sock`.

The **agent** (see Backend → Agent) additionally needs the `llama-cpp` container running and reachable over `wg-network` — it is the LLM backend. The rest of the dashboard works without it; only the agent (`/api/send`, `WS /api/ws/agent`) degrades (errors) if llama-cpp is down.

### Maintaining the scripts

If the deployment procedure or what "healthy" means changes, **update the scripts, not the prompts**:

- New endpoint that should be part of the healthcheck → add a step to `scripts/healthcheck.sh`
- New service or build dependency → update `scripts/deploy.sh`
- New required env var → add a check at the top of `scripts/deploy.sh`

This keeps the verification logic in one place. Inline curl checks in prompts get stale; scripts do not.

## Auth

Mutating endpoints (🔒 in the API table) require an admin token; all GETs and feedback submission stay public. The dashboard renders read-only on load and only prompts for login when a protected action is attempted. The agent's **`root_bash` approval** is likewise admin-gated — approving a command over `WS /api/ws/agent` requires a valid token the server re-verifies (see Backend → Agent → Streaming); denying does not.

- **Mechanism:** `backend/auth.py` — PBKDF2-HMAC-SHA256 passwords + HMAC-signed `<payload>.<signature>` tokens, **stdlib only** (no extra deps). Tokens live 24h. The signing secret is generated once and persisted in the `auth_config` table (survives rebuilds).
- **Create/reset an admin:** `bash scripts/create_admin.sh` (interactive hidden prompt) or `ADMIN_USER=… ADMIN_PASS=… bash scripts/create_admin.sh`. Re-running an existing username resets its password. The container must be running. Passwords are never stored in plaintext or in the compose file.
- **Frontend:** token + expiry in `localStorage` (`frontend/src/auth.js`); `LoginModal.jsx` appears on demand. `authedFetch` attaches the Bearer header and clears the token on 401.
- The feedback skill marks status via a **direct DB call** (`docker exec … db.update_feedback_status`), not the now-protected HTTP endpoint.

## Backend

**Location:** `backend/`  
**Entry point:** `backend/main.py` (FastAPI + APScheduler)  
**Package manager:** `uv` — add deps with `uv add <package>` which updates `pyproject.toml`. The Dockerfile runs `uv sync --no-dev`.  
**DB:** PostgreSQL via `psycopg[binary]` — DSN in `backend/db.py`: `postgresql://app:hackme@postgress:5432/admindash`

### Collectors (`backend/collectors/`)

Each collector has a `collect()` function. The host network/mount namespace is accessed via `nsenter` because the container uses `pid: host` (making `/proc/1/ns/*` point to the host):

| Collector | Technique | Notes |
|---|---|---|
| `system.py` | psutil | CPU%, RAM, uptime |
| `hardware.py` | `lscpu` + psutil | static CPU facts (model, cores, cache, freq) + load avg + per-core % |
| `vms.py` | `nsenter --mount=/proc/1/ns/mnt --net=/proc/1/ns/net -- virsh -c qemu:///system` | libvirt VMs: state, vCPUs, memory (balloon), host RSS, per-VM CPU% (rate of cumulative `cpu.time`) |
| `gpu.py` | `nvidia-smi` subprocess | util%, temp, VRAM, processes |
| `temps.py` | `sensors -j` | CPU Tctl, NVMe temps |
| `containers.py` | Docker SDK | name, status, health, ports |
| `ports.py` | `nsenter --net=/proc/1/ns/net -- ss -tlnp` | host open TCP ports (listening) |
| `connections.py` | conntrack via `pyroute2` netlink (run in host netns through `conntrack_helper.py`); falls back to `ss -tunap` | every tracked flow (incl. UDP + NAT/forwarded) src→dst; enriches process via `ss`, classifies scope (public/private/loopback) |
| `wireguard.py` | `nsenter --net=/proc/1/ns/net -- wg show` | wg0 interface + peers |
| `disk.py` | `nsenter --mount=/proc/1/ns/mnt -- df` | per-partition usage |
| `network.py` | `nsenter --net=/proc/1/ns/net -- cat /proc/net/dev` | host NIC byte rates |
| `processes.py` | psutil (pid:host) | top CPU/RAM processes |
| `smart.py` | `smartctl` direct (privileged) | NVMe/ATA health, auto-discovers via `--scan` |
| `sessions.py` | `nsenter --mount=/proc/1/ns/mnt -- who` | active SSH sessions; classifies origin scope (`public` flag) |
| `cron.py` | `nsenter --mount=/proc/1/ns/mnt -- cat/ls` | host cron jobs from `/etc/crontab`, `/etc/cron.d/*`, and per-user spools (`/var/spool/cron[/crontabs]`); parses schedule/user/command/source |
| `events.py` | Docker SDK event stream | container lifecycle events → Postgres |
| `alerts.py` | calls other collectors | threshold-based alerts; critical alert on any login session from a public IP |

### Agent (`backend/agent/`)

A natural-language assistant over the dashboard's own data. The LLM runs on the
local **llama-cpp** container (OpenAI-compatible server at `http://llama-cpp:8080/v1`,
reachable by name over `wg-network`), not api.openai.com. Uses the `openai` SDK.

**Tavily key:** `web_search` reads `TAVILY_API_KEY` from the env — never hardcoded.
It lives in `admindash/.env` (gitignored-by-intent; not committed) and is loaded
into the container via the compose `env_file: [../admindash/.env]` on the admindash
service. **Use `../admindash/.env`, not `builds/.env`** — the latter is a symlink to
immich's env. If the key is absent, `web_search` returns a graceful "unavailable"
string and the rest of the agent still works.

| File | Role |
|---|---|
| `prompt.py` | Builds the system prompt; embeds the live OpenAPI spec (slimmed to GET path + summary) fetched from `127.0.0.1:8000/openapi.json` so the model knows which endpoints exist. |
| `main.py` | LLM client + tools — `get_stat` (curls a GET on the local API), `web_search` (Tavily, for interpreting what a behavior/process/port/error means; used sparingly), `cat_file`/`ls_folder` (read host files/dirs through an allowlisted path), and **`root_bash`** (runs an arbitrary command as root in the host's namespaces via `nsenter -t 1 -m -u -i -n -p -- bash -c`, 60s timeout via `AGENT_BASH_TIMEOUT`). `root_bash` is **gated by per-command human approval + admin auth**: `_run_loop` takes a `request_approval(command) -> bool` callback and refuses to run anything it doesn't approve (no callback ⇒ refused, so the POST `/api/send` path can never run it — only the WS path supplies one). The system prompt tells the model to avoid `root_bash` unless explicitly asked to take an action and to prefer the read-only tools. — + `_run_loop` (drives tool calls to a final answer). The Qwen3.5 thinking model occasionally burns a whole generation in its `<think>`/`reasoning_content` channel and returns **empty** user-facing content (or "thinks" about a follow-up tool call but never emits it, then stops); `_run_loop` detects an empty answer and re-prompts the model with an **ephemeral nudge** (`EMPTY_NUDGE`, retried up to `AGENT_EMPTY_RETRIES`, default 2) that is sent to the model but **never written into `messages`** — so the persisted/rendered history stays clean. Config via env: `AGENT_LLM_BASE_URL`, `AGENT_LLM_API_KEY` (llama.cpp ignores it; placeholder), `AGENT_MODEL` (default `Qwen_Qwen3.5-9B-Q6_K.gguf` — must match what `/v1/models` reports), `AGENT_MAX_STEPS`, `AGENT_EMPTY_RETRIES`, `AGENT_MAX_CONVERSATIONS`. |
| `store.py` | Postgres-backed conversation store (`chat_sessions` table) with an in-memory write-through cache. Chats **persist** across restarts/reloads; the DB is the source of truth and cache eviction never loses data. A new conversation is cached but its DB row is written lazily on the first saved turn (empty 'New chat' tabs never persist). Removed only by explicit `end` or the most-recent-N cap (`AGENT_MAX_CONVERSATIONS`, default 200, pruned on save). |
| `endpoint.py` | The router (mounted via `include_router` in `main.py`) + the `_render` helper that reduces raw messages to user/assistant turns for the UI. |

Conversation lifecycle: `POST /api/newconversation` mints a UUID seeded with the
system prompt (cached only; persisted on first turn); `POST /api/send/{id}`
appends a turn, runs the loop, and saves the full message list to `chat_sessions`
(404 if the id is unknown — i.e. never persisted or already deleted); `DELETE
/api/end/{id}` retires it (deletes the DB row). Removal is by explicit `end` or
the most-recent-N DB cap — there is no idle TTL and the frontend no longer deletes
chats on page close. The model's tool calls require llama.cpp running with `--jinja`
(enables the chat template's function calling); `launch.sh` already does.

**Streaming (WebSocket):** `POST /api/send/{id}` returns only the final answer.
`WS /api/ws/agent` additionally streams a `tool_call` event as each tool is
dispatched, so the UI can show tools live. The blocking agent loop runs in a
threadpool; `_run_loop`'s `on_event` callback bridges events back to the event
loop via a queue (see `endpoint.py`). Tool calls **are persisted**: the full raw
message list (assistant `tool_calls` + `role:tool` results) is saved to
`chat_sessions`, and `_render` surfaces each stored tool_call as the same
`name arg` chip the WS streams live, so a reloaded conversation
(`GET /api/conversations/{id}`) shows tool activity too — not just user/assistant
bubbles. Tool *result* bodies stay in the DB for agent context but aren't rendered
as bubbles (the live stream never showed them either). Requires the inner nginx
`/api/ws/` upgrade block (HTTP/1.1
+ `Upgrade`/`Connection` headers — the plain `/api/` block is HTTP/1.0); the outer
server-nginx already forwards upgrades.

**Turns survive a dropped socket.** A turn runs in `_run_turn` (an `asyncio.Task`
tracked in `endpoint._running`) **decoupled from the socket that started it**, so a
tab close / refresh / network blip mid-turn neither aborts it nor loses the answer —
it runs to completion and persists regardless of who's listening. A per-conversation
busy flag (`store.try_begin/finish`, enforced on both the POST and WS paths) prevents
two concurrent turns from corrupting the shared message list (POST returns 409; WS
attaches to the running turn instead). WS protocol additions: the client can send
`{conv_id, subscribe:true}` to rejoin — the server replies `{type:resume, user_message,
events:[buffered tool_calls]}` if a turn is live (so a freshly loaded page reconstructs
the in-progress turn, then receives the remaining live events + `answer`/`done`), or
`{type:idle}` if nothing is running. The frontend keeps the socket open while the panel
is open, auto-reconnects with a 1.5s backoff, and subscribes to the active conversation
on every (re)connect and tab switch; `done` for a resumed turn triggers a reload so the
optimistic pending tail is replaced by the canonical persisted turn.

**root_bash approval (over the same WS).** When the agent calls `root_bash`, the
blocking loop (on the executor thread) registers a pending approval on the `_Turn`
and dispatches `{type:approval_request, approval_id, command}`, then blocks on a
`threading.Event` until a reply resolves it or it times out (`AGENT_APPROVAL_TIMEOUT`,
default 180s ⇒ denied). The client replies `{conv_id, approval_id, approved, token}`.
**Approving requires a valid admin token** — the server re-verifies it (`auth.verify_token`)
and, if it's missing/expired, leaves the approval pending and sends `{type:approval_unauthorized}`
so the UI re-prompts login; **denying needs no auth** (anyone may cancel — the fail-safe
direction). The frontend gates the Approve button through `runProtected` (login modal +
replay) and attaches the Bearer token; pending approvals are included in the `resume`
payload (`approvals:[…]`) so a reconnect re-shows the buttons. Because approval blocks
inside the turn, the busy claim is held throughout, so a never-answered prompt can't
double-run and the timeout always releases the worker.

### Scheduled jobs (APScheduler)

- Every 60s: `_collect_and_store()` → inserts into `metrics` table
- Every 15s: `events_collector.poll_and_store()` → inserts into `docker_events` table

### Database schema (`backend/db.py`)

```
metrics       — ts, cpu_pct, ram_pct, gpu_util, gpu_temp, cpu_temp (24h retention)
docker_events — ts, action, container, image (last 200 rows)
feedback      — id, type (vestigial, always 'feedback'), title, description, status, created_at, resolved_at, resolution_note
users         — id, username (unique), password_hash, salt, role, created_at (admin accounts)
auth_config   — key/value; holds the persisted token-signing secret ('token_secret')
chat_sessions — id (uuid), title, turns, messages (JSONB raw message list), created_at, updated_at (persisted agent chats; most-recent-N retained)
```

### API endpoints

🔒 = requires admin auth (`Authorization: Bearer <token>`). Everything else is public (read-only GETs + feedback submission).

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/login` | Exchange `username`+`password` for a 24h token (`{token, expires_at, username, role}`) |
| GET | `/api/auth/me` | 🔒 Current token's user |
| POST | `/api/auth/change-password` | 🔒 `old_password`, `new_password` (min 8 chars) |
| GET | `/api/stats` | CPU, RAM, disk, uptime |
| GET | `/api/hardware` | Static CPU/hardware info + load avg + per-core utilization |
| GET | `/api/vms` | libvirt/virsh VMs (`{available, vms[]}`) with per-VM stats |
| GET | `/api/gpu` | GPU util, temp, VRAM |
| GET | `/api/gpu/processes` | Per-process GPU usage |
| GET | `/api/temps` | CPU/NVMe temperatures |
| GET | `/api/containers` | Docker containers list |
| POST | `/api/containers/{name}/action` | 🔒 start/stop/restart |
| GET | `/api/logs/{name}` | Container logs |
| GET | `/api/ports` | Host open TCP ports (listening) |
| GET | `/api/connections` | All tracked network flows (conntrack) + per-proto/scope counts |
| GET | `/api/wireguard` | WireGuard peers |
| POST | `/api/vpn/clients` | 🔒 Add a WireGuard client (`name`, `address`) via host `admin vpn add`; returns the generated `.conf` (incl. private key, shown once) and live-activates the peer with `wg syncconf` |
| GET | `/api/disk` | Disk partitions |
| GET | `/api/network` | NIC byte rates |
| GET | `/api/processes` | Top processes |
| GET | `/api/smart` | SMART disk health |
| GET | `/api/sessions` | Active SSH sessions |
| GET | `/api/cron` | Host cron jobs (schedule, user, command, source) |
| GET | `/api/alerts` | Threshold alerts |
| GET | `/api/events` | Docker event log |
| GET | `/api/history` | Metric history (SQLite) |
| GET | `/api/feedback` | Feedback items (optionally `?status=pending`) |
| POST | `/api/feedback` | Submit feedback (`title`, `description`) |
| POST | `/api/feedback/{id}/status` | 🔒 Update item status (`status`, `note`) |
| POST | `/api/host/reboot` | 🔒 Reboot the host |
| POST | `/api/newconversation` | Start an agent conversation → `{id}` |
| POST | `/api/send/{id}` | Add a turn to a conversation (`message`) → `{answer}` (404 if unknown id) |
| GET | `/api/conversations` | Active conversation ids (+ title preview, turn count) |
| GET | `/api/conversations/{id}` | Renderable user/assistant turns for one conversation |
| DELETE | `/api/end/{id}` | Retire a conversation → `{ended}` |
| WS | `/api/ws/agent` | Send `{conv_id, message}`; streams `{type:tool_call,name,args}` per dispatched tool, then `{type:answer,content}`, then `{type:done}` (or `{type:error}`). Every event carries `conv_id`. |

## Frontend

**Location:** `frontend/src/`  
**Stack:** React 19, Vite, Recharts (for history chart)  
**Build:** `npm run build` → `dist/` (served by Nginx). Dockerfile runs `npm ci` so `package-lock.json` must stay committed.

### Components

```
App.jsx              — root: polling loops, layout, modal state
App.css              — all styles (no component-level CSS files)
components/
  AlertBanner.jsx    — top banner for critical/warning threshold alerts
  SystemCard.jsx     — CPU%, RAM, uptime big-stat display
  HardwareCard.jsx   — CPU model/cores/cache/freq, load avg, per-core utilization bars
  VmsCard.jsx        — libvirt VM table (state, vCPU, CPU%, memory, host RSS)
  GpuCard.jsx        — GPU util%, temp, VRAM + process list toggle
  TempsCard.jsx      — CPU Tctl, NVMe Composite temps
  NetworkCard.jsx    — per-NIC rx/tx rates + totals
  DiskCard.jsx       — per-partition usage bars + collapsible SMART health (NVMe + ATA) section
  ContainersTable.jsx — container list with start/stop/restart actions
  ProcessesCard.jsx  — top 10 processes by CPU
  PortsCard.jsx      — host open TCP ports (listening)
  ConnectionsCard.jsx — "Network Surface": all conntrack flows (TCP/UDP/NAT) src→dst, scope + proto summary chips (public peers highlighted)
  WireguardCard.jsx  — wg0 IP + scrollable peer list
  SessionsCard.jsx   — active SSH sessions (public-origin sessions highlighted red)
  CronCard.jsx       — host cron jobs (schedule, user, command, source)
  EventsFeed.jsx     — Docker lifecycle event log
  HistoryChart.jsx   — 24h sparkline (Recharts) for CPU/GPU/RAM
  LogsModal.jsx      — container log viewer (bottom sheet mobile / centered desktop)
  LoginModal.jsx     — admin login (shown only when a protected action needs auth)
  FeedbackModal.jsx  — submit feedback (title + optional description)
  FeedbackPanel.jsx  — read-only display of the feedback queue with statuses
  AgentPanel.jsx     — assistant drawer: collapsible right-edge panel (fixed; pushes/squishes the dashboard via right-padding on .app). Active-conversation tabs across the top, standard chat below. Sends over WS /api/ws/agent and renders live tool-dispatch chips as they stream (falls back to POST /api/send on WS failure). Past chats (persisted in `chat_sessions`) load on open and survive reloads — removed only via the per-tab × (DELETE /api/end). Desktop: drag the left edge to resize (width persisted in localStorage). Mobile: overlays instead of squishing.
```

### Polling intervals

```js
POLL_MS       = 5000   // stats, gpu, temps, containers, ports, wireguard, disk, processes, smart
NET_POLL_MS   = 3000   // network rates
EVENTS_POLL_MS = 15000 // docker events
SLOW_POLL_MS  = 30000  // alerts, sessions, cron
history       = 60000  // metric history
feedback      = 15000  // feedback panel
```

### CSS design system

Dark GitHub-inspired theme. All CSS is in `App.css` using custom properties:

```css
--bg: #0d1117        /* page background */
--surface: #161b22   /* card background */
--surface2: #1c2128  /* inset / secondary surface */
--border: #30363d
--text: #e6edf3
--text-muted: #8b949e
--accent: #58a6ff    /* blue — links, highlights */
--green: #3fb950
--yellow: #d29922
--red: #f85149
--orange: #e3b341
--radius: 8px
--gap: 12px          /* grid gap */
--pad: 14px          /* card padding */
```

Layout: CSS Grid, mobile-first. 1 column → 2 col at ≥640px → 3 col at ≥1024px. `.full-width` spans all columns. No external UI component library — all components use plain CSS classes defined in `App.css`.

### Adding a new card

1. Create `frontend/src/components/YourCard.jsx`
2. Add styles to `App.css` (follow existing naming conventions)
3. Import and add a `GET /api/your-endpoint` call in `App.jsx`'s `poll()` or a separate interval
4. Add the backend endpoint in `main.py` and a collector in `backend/collectors/your.py`
5. Add the `<YourCard>` to the JSX grid in `App.jsx`

## Feedback queue

Users submit feedback via the UI. Items land in the `feedback` table with `status='pending'` and are displayed read-only in the dashboard's Feedback Queue panel.

To work the queue, invoke the **`feedback` skill** (`.claude/skills/feedback/SKILL.md`) — it walks through reading pending items, triaging, implementing, deploying, and marking each row resolved via `POST /api/feedback/{id}/status`. There is no background trigger and no automation; processing the queue is on-demand by the user.

### Post-deployment verification (mandatory)

After every rebuild, run:

```bash
bash scripts/healthcheck.sh
```

If it exits non-zero, **iterate until it passes** — do not give up after one failure:

1. Run `docker logs admindash --tail 100` to diagnose
2. Fix the root cause (syntax error, bad import, broken build, missing dep, etc.)
3. Re-deploy: `bash scripts/deploy.sh`
4. Re-run: `bash scripts/healthcheck.sh`
5. Repeat until it passes, then update feedback item statuses

## What NOT to do

- Avoid modifying `/home/andrew/docker_deployments/builds/docker-compose.yml` unless necessary and confirmed; it is carefully tuned (networks, the proxy-net isolation, etc.). The admindash service intentionally has `env_file: [../admindash/.env]` (Tavily key). Note `builds/.env` is a **symlink to immich's env** — never point admindash at it or overwrite it.
- Do not add new system-level packages (can't modify Dockerfile at runtime)
- Do not hardcode device paths (e.g. `/dev/sda`) — SMART auto-discovers via `smartctl --scan`
- Do not write per-component CSS files — all styles go in `App.css`
- Do not add comments explaining what code does — only add comments for non-obvious WHY
