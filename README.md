### my servers dashboard

Personal server management/insights dashboard. FastAPI backend + React frontend, deployed as a single Docker container behind WireGuard.

Has a small agent harness for interpreting the data and performing small tasks, runs well with small local models.
Investigation board allows for model findings to be pinned to a kanbam board.

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

The app runs as one container (nginx + FastAPI via supervisord). The root
`docker-compose.yml` is a self-contained stack — admindash plus its Postgres:

```bash
cp .env.example .env          # configure (see above)
docker compose up --build -d  # build + start admindash and Postgres
```

Then, from the host (the container must be running), set an admin login and
verify:

```bash
bash scripts/create_admin.sh                          # interactive
DASH_URL=http://localhost:8090 bash scripts/healthcheck.sh
```

Override defaults via `.env` (e.g. `ADMINDASH_PORT`, `POSTGRES_PASSWORD`). The
dashboard is served on `http://localhost:${ADMINDASH_PORT:-8090}`. Remove the
`runtime: nvidia` / `deploy` blocks from the compose file on machines without an
NVIDIA GPU.

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
