---
name: feedback
description: Process the pending bug reports and feature requests in the admindash feedback queue. Use when the user says "process feedback", "work the feedback queue", "implement pending feedback", "/feedback", or similar — reads the feedback table, implements safe items, deploys, and marks each row done or needs-review.
---

# Process the admindash feedback queue

Users submit bug reports and feature requests through the dashboard UI. They land in the `feedback` table in Postgres with `status='pending'`. Your job is to read pending items, implement the safe ones, deploy, verify, and mark each item resolved.

## Step 1 — Read the queue

Fetch pending items via the dashboard's HTTP API (it's running on the host at `http://10.8.0.1`):

```bash
curl -s 'http://10.8.0.1/api/feedback?status=pending' | jq
```

Each row has: `id`, `title`, `description`, `status`, `created_at`. If the list is empty, stop — there is nothing to do, report that back to the user.

## Step 2 — Triage each item

For each pending item, decide:

- **IMPLEMENT** if it is a safe, scoped change inside `/home/andrew/docker_deployments/admindash/`:
  UI tweaks, copy/style changes, small bug fixes, additional cards backed by existing collectors, new endpoints that follow existing patterns.

- **SKIP (mark `needs-review`)** if any of:
  - Touches files outside `admindash/` (especially `docker-compose.yml` or anything in `builds/`)
  - Requires new system packages, infra changes, or secrets
  - Ambiguous, contradictory, or missing critical detail
  - Risky (DB schema breaking changes, deleting user data, security-sensitive)
  - Larger than ~2 hours of focused work

When in doubt, mark `needs-review`. The user can clarify and re-queue.

## Step 3 — Implement

Make all changes inside `/home/andrew/docker_deployments/admindash/`. Follow the existing patterns documented in `CLAUDE.md` (read it first if you haven't). Batch related items so you only rebuild once.

Do NOT modify:
- `/home/andrew/docker_deployments/builds/docker-compose.yml`
- The Dockerfile in ways that require new apt packages (the build runs without network for system pkgs)
- Anything outside `admindash/`

## Step 4 — Deploy and verify

```bash
cd /home/andrew/docker_deployments/admindash
bash scripts/deploy.sh
bash scripts/healthcheck.sh
```

If `healthcheck.sh` exits non-zero, **iterate until it passes** — do not give up after one failure:
1. Run `docker logs admindash --tail 100` to diagnose
2. Fix the root cause
3. Re-run `bash scripts/deploy.sh && bash scripts/healthcheck.sh`
4. Repeat

Only proceed to step 5 once the healthcheck passes.

## Step 5 — Mark each item resolved

For every item you touched, update its status so it disappears from the pending queue.

The `POST /api/feedback/{id}/status` HTTP endpoint requires admin auth, so the skill
writes the status **directly to Postgres** instead — it runs locally with DB access,
which avoids needing credentials in an automated context:

```bash
# Implemented
docker exec -w /app/backend admindash /app/backend/.venv/bin/python -c \
  "import db; db.update_feedback_status(<ID>, 'done', '<short description of what you did>')"

# Skipped
docker exec -w /app/backend admindash /app/backend/.venv/bin/python -c \
  "import db; db.update_feedback_status(<ID>, 'needs-review', '<reason it needs human review>')"
```

Replace `<ID>` with the integer id from step 1. Valid statuses: `pending`, `in-progress`, `done`, `needs-review`, `failed`.

## Step 6 — Summarize

Report back to the user:
- Number of items processed
- For each: id, title, outcome (done / needs-review), one-line reason
- Any deploy issues you hit and how you resolved them

## Direct Postgres access (fallback)

If the HTTP API is down, you can query the DB directly through the container:

```bash
docker exec -i postgress psql -U app -d admindash -c "SELECT id,title,status FROM feedback WHERE status='pending';"
```

But prefer the HTTP API — it's the same interface the UI uses and exercises the live app.
