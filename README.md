### my servers dashboard

Personal server management/insights dashboard. FastAPI backend + React frontend, deployed as a single Docker container behind WireGuard.

Has a small agent harness for interpreting the data and performing small tasks, runs well with Qwen3.5 9B

I wrote like none of this, thanks claude

<img width="1919" height="932" alt="image" src="https://github.com/user-attachments/assets/54781e5b-0156-4233-a1de-fc143bad3e58" />

## Configuration

Copy the env template and edit it:

```bash
cp .env.example .env
```

All vars are optional:

- `TAVILY_API_KEY` — enables the agent's web search (degrades gracefully if unset).
- `AGENT_LLM_BASE_URL` / `AGENT_MODEL` / `AGENT_LLM_API_KEY` — the agent's LLM
  endpoint (any OpenAI-compatible server). Also editable at runtime.
- `DATABASE_URL` — Postgres DSN (defaults to the in-compose `postgress` service).

## Docker setup

The app ships as one container (nginx + FastAPI via supervisord) and depends on a
Postgres service, wired up by a `docker-compose.yml`.

```bash
bash scripts/deploy.sh        # build + (re)start the container
bash scripts/healthcheck.sh   # verify the running app
bash scripts/create_admin.sh  # set the admin login (interactive)
```

The scripts resolve paths relative to the repo and accept env overrides:

| Var | Used by | Default |
|---|---|---|
| `COMPOSE_DIR` | deploy | `../builds` (dir holding `docker-compose.yml`) |
| `COMPOSE_SERVICE` | deploy | `admindash` |
| `ADMINDASH_CONTAINER` | healthcheck, create_admin | `admindash` |
| `DASH_URL` | healthcheck | `https://10.8.0.1` |

## Local setup

For development without Docker. Requires Python (uv recommended),
Node, and a reachable Postgres.

**Backend** (`backend/`):

```bash
cd backend
uv sync                                          # install deps
export DATABASE_URL=postgresql://app:hackme@localhost:5432/admindash
uv run uvicorn main:app --reload --port 8000     # serves /api on :8000
```

**Frontend** (`frontend/`):

```bash
cd frontend
npm install
npm run dev          # Vite dev server; proxies /api → localhost:8000
```
