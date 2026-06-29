import json
import subprocess
import urllib.request
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Body, Header, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import docker as docker_sdk

import auth
import db
from db import (init_db, insert_snapshot, query_history, insert_event, query_events,
               insert_feedback, list_feedback, update_feedback_status,
               get_auth_secret, get_user, set_password,
               memory_list, memory_save, memory_update, memory_delete,
               board_list, board_add, board_move, board_delete,
               boards_list, board_create, board_update, board_delete_investigation,
               reflection_run_list)
from collectors import system, gpu, temps, containers, ports, wireguard, disk, processes, network, connections
from collectors import events as events_collector, smart, alerts, sessions, hardware, vms, cron, directory, vpn
from agent import router as agent_router
from agent import main as agent_main
from agent.main import resolve_llm_config as agent_resolve_llm_config
from agent.reflect import run_nightly_reflection


def _collect_and_store() -> None:
    sys_data = system.collect()
    gpu_data = gpu.collect()
    temp_data = temps.collect()

    cpu_temp = temp_data.get("cpu_Tctl")
    gpu_util = gpu_data["util_pct"] if gpu_data else None
    gpu_temp_val = gpu_data["temp_c"] if gpu_data else None

    insert_snapshot(
        cpu_pct=sys_data["cpu_pct"],
        ram_pct=sys_data["ram_pct"],
        gpu_util=gpu_util,
        gpu_temp=gpu_temp_val,
        cpu_temp=cpu_temp,
    )


# The nightly self-reflection ("memory") job is runtime-configurable (DB-backed,
# survives rebuilds): it can be turned off and rescheduled from settings without a
# redeploy. Keys live in app_config; absent keys fall back to enabled @ 00:00.
_CFG_REFLECT_ENABLED = "reflect_enabled"
_CFG_REFLECT_HOUR = "reflect_hour"
_CFG_REFLECT_MINUTE = "reflect_minute"


def _reflect_config() -> dict:
    cfg = db.get_app_config((_CFG_REFLECT_ENABLED, _CFG_REFLECT_HOUR, _CFG_REFLECT_MINUTE))
    enabled = cfg.get(_CFG_REFLECT_ENABLED)
    return {
        "enabled": enabled != "0" if enabled is not None else True,
        "hour": int(cfg.get(_CFG_REFLECT_HOUR) or 0),
        "minute": int(cfg.get(_CFG_REFLECT_MINUTE) or 0),
    }


def _apply_reflect_schedule(scheduler) -> dict:
    """(Re)install or remove the 'reflect' job to match the stored config.
    Idempotent — safe to call at startup and after every config change."""
    cfg = _reflect_config()
    if cfg["enabled"]:
        scheduler.add_job(
            run_nightly_reflection,
            CronTrigger(hour=cfg["hour"], minute=cfg["minute"]),
            id="reflect", max_instances=1, misfire_grace_time=3600,
            replace_existing=True,
        )
    elif scheduler.get_job("reflect") is not None:
        scheduler.remove_job("reflect")
    return cfg


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = BackgroundScheduler()
    scheduler.add_job(_collect_and_store, "interval", seconds=60, id="collect")
    scheduler.add_job(
        lambda: events_collector.poll_and_store(insert_event),
        "interval", seconds=15, id="events"
    )
    app.state.scheduler = scheduler
    _apply_reflect_schedule(scheduler)
    scheduler.start()
    _collect_and_store()
    events_collector.poll_and_store(insert_event)
    yield
    scheduler.shutdown()


app = FastAPI(
    lifespan=lifespan,
    title="admindash API",
    version="1.0.0",
    description=(
        "Backend API for admindash, a personal server management dashboard. "
        "Exposes host system metrics (CPU/RAM/GPU/temps/disk/SMART), Docker "
        "container inspection and control, network surface (open ports, "
        "conntrack flows, WireGuard peers, SSH sessions), threshold alerts, "
        "metric history, and a feedback queue."
    ),
    contact={"name": "admindash", "email": "ajheschl@gmail.com"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(agent_router)


# ── Auth ────────────────────────────────────────────────────────────────────

def require_admin(authorization: str | None = Header(default=None)) -> dict:
    """Gate for mutating endpoints. Reads `Authorization: Bearer <token>`."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = auth.verify_token(authorization.split(" ", 1)[1], get_auth_secret())
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


@app.post("/api/auth/login")
def login(username: str = Body(...), password: str = Body(...)):
    user = get_user(username)
    if not user or not auth.verify_password(password, user["salt"], user["password_hash"]):
        return JSONResponse(status_code=401, content={"error": "Invalid credentials"})
    token, expires_at = auth.create_token(user["username"], user["role"], get_auth_secret())
    return {"token": token, "expires_at": expires_at, "username": user["username"], "role": user["role"]}


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(require_admin)):
    return {"username": user["sub"], "role": user.get("role")}


@app.post("/api/auth/change-password")
def change_password(
    old_password: str = Body(...),
    new_password: str = Body(...),
    user: dict = Depends(require_admin),
):
    if len(new_password) < 8:
        return JSONResponse(status_code=400, content={"error": "new password must be at least 8 characters"})
    current = get_user(user["sub"])
    if not current or not auth.verify_password(old_password, current["salt"], current["password_hash"]):
        return JSONResponse(status_code=401, content={"error": "current password incorrect"})
    set_password(user["sub"], new_password)
    return {"status": "ok"}


@app.get("/api/stats")
def get_stats():
    return system.collect()


@app.get("/api/hardware")
def get_hardware():
    return hardware.collect()


@app.get("/api/vms")
def get_vms():
    return vms.collect()


@app.get("/api/vms/{name}/logs")
def get_vm_logs(name: str, lines: int = Query(default=100, ge=10, le=1000)):
    result = vms.logs(name, lines)
    if "error" in result and "not found" in result["error"]:
        return JSONResponse(status_code=404, content=result)
    return result


@app.get("/api/vms/{name}/network")
def get_vm_network(name: str):
    result = vms.net_info(name)
    if "error" in result and "not found" in result["error"]:
        return JSONResponse(status_code=404, content=result)
    return result


@app.get("/api/gpu")
def get_gpu():
    return gpu.collect()


@app.get("/api/gpu/processes")
def get_gpu_processes():
    return gpu.collect_processes()


@app.get("/api/temps")
def get_temps():
    return temps.collect()


@app.get("/api/containers")
def get_containers():
    return containers.collect()


@app.post("/api/containers/{name}/action")
def container_action(name: str, action: str = Body(..., embed=True), user: dict = Depends(require_admin)):
    if action not in ("start", "stop", "restart"):
        return JSONResponse(status_code=400, content={"error": "Invalid action"})
    try:
        client = docker_sdk.from_env()
        c = client.containers.get(name)
        getattr(c, action)()
        c.reload()
        return {"name": name, "action": action, "status": c.status}
    except docker_sdk.errors.NotFound:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/containers/{name}/stats")
def get_container_stats(name: str):
    """Granular live resource stats for one container: host-CPU%, memory (usage,
    limit, page-cache, app load, %), and cumulative network + block I/O byte
    totals. A single snapshot — difference two reads for network/block rates."""
    try:
        return containers.stats(name)
    except docker_sdk.errors.NotFound:
        return JSONResponse(status_code=404, content={"error": f"Container '{name}' not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/ports")
def get_ports():
    return ports.collect()


@app.get("/api/connections")
def get_connections():
    return connections.collect()


@app.get("/api/wireguard")
def get_wireguard():
    return wireguard.collect()


@app.post("/api/vpn/clients")
def add_vpn_client(
    name: str = Body(...),
    address: str = Body(...),
    user: dict = Depends(require_admin),
):
    try:
        return vpn.add_client(name, address)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/history")
def get_history(minutes: int = Query(default=1440, ge=1, le=1440)):
    return query_history(minutes)


@app.get("/api/disk")
def get_disk():
    return disk.collect()


@app.get("/api/processes")
def get_processes():
    return processes.collect()


@app.get("/api/network")
def get_network():
    return network.collect()


@app.get("/api/logs/{name}")
def get_logs(name: str, lines: int = Query(default=100, ge=10, le=1000)):
    try:
        client = docker_sdk.from_env()
        c = client.containers.get(name)
        raw = c.logs(tail=lines, timestamps=True)
        text = raw.decode("utf-8", errors="replace")
        log_lines = [l for l in text.splitlines() if l]
        return {"name": name, "lines": log_lines}
    except docker_sdk.errors.NotFound:
        return JSONResponse(status_code=404, content={"error": f"Container '{name}' not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/events")
def get_events(limit: int = Query(default=30, ge=1, le=100)):
    return query_events(limit)


@app.get("/api/smart")
def get_smart():
    return smart.collect()


@app.get("/api/alerts")
def get_alerts():
    return alerts.collect()


@app.get("/api/sessions")
def get_sessions():
    return sessions.collect()


@app.get("/api/cron")
def get_cron():
    return cron.collect()


@app.get("/api/directory")
def get_directory():
    return directory.collect()


# ── Host control ──────────────────────────────────────────────────────────────

@app.post("/api/host/reboot")
def host_reboot(user: dict = Depends(require_admin)):
    try:
        subprocess.Popen(
            ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--",
             "systemctl", "reboot"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return {"status": "rebooting"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Feedback ──────────────────────────────────────────────────────────────────

@app.get("/api/feedback")
def get_feedback(status: str = Query(default=None)):
    return list_feedback(status=status)


@app.post("/api/feedback")
def submit_feedback(
    title: str = Body(...),
    description: str = Body(default=""),
):
    if not title.strip():
        return JSONResponse(status_code=400, content={"error": "title required"})
    id = insert_feedback("feedback", title.strip()[:200], description.strip()[:2000])
    return {"id": id}


@app.post("/api/feedback/{id}/status")
def set_feedback_status(
    id: int,
    status: str = Body(...),
    note: str = Body(default=None),
    user: dict = Depends(require_admin),
):
    valid = {"pending", "in-progress", "done", "needs-review", "failed"}
    if status not in valid:
        return JSONResponse(status_code=400, content={"error": f"status must be one of {valid}"})
    update_feedback_status(id, status, note)
    return {"id": id, "status": status}


# ── Agent memories ────────────────────────────────────────────────────────────
# Browse/curate the durable facts the agent injects into every conversation's
# system prompt. Reading is public (read-only, like every other GET); creating,
# editing, and deleting are admin-gated — they change what the agent "knows".

MEMORY_MAX_LEN = 2000


@app.get("/api/memories")
def get_memories():
    return {"memories": memory_list()}


@app.post("/api/memories")
def add_memory(
    content: str = Body(..., embed=True),
    user: dict = Depends(require_admin),
):
    content = content.strip()
    if not content:
        return JSONResponse(status_code=400, content={"error": "content required"})
    id = memory_save(content[:MEMORY_MAX_LEN])
    return {"id": id}


@app.put("/api/memories/{id}")
def edit_memory(
    id: int,
    content: str = Body(..., embed=True),
    user: dict = Depends(require_admin),
):
    content = content.strip()
    if not content:
        return JSONResponse(status_code=400, content={"error": "content required"})
    if not memory_update(id, content[:MEMORY_MAX_LEN]):
        return JSONResponse(status_code=404, content={"error": "unknown memory"})
    return {"id": id, "content": content}


@app.delete("/api/memories/{id}")
def remove_memory(id: int, user: dict = Depends(require_admin)):
    if not memory_delete(id):
        return JSONResponse(status_code=404, content={"error": "unknown memory"})
    return {"deleted": id}


# ── Agent LLM config ────────────────────────────────────────────────────────────
# The agent's LLM endpoint/model are editable at runtime (DB-backed, survives
# rebuilds) so the dashboard can be pointed at a different machine's LLM without a
# redeploy. Resolution order is DB override → env default (see agent.main). Reading
# the config is public (no secrets leak — the API key is returned only as a boolean
# "is it set"); changing it is admin-gated.


@app.get("/api/agent/config")
def get_agent_config():
    cfg = agent_resolve_llm_config()
    overrides = db.get_app_config((agent_main._CFG_BASE_URL, agent_main._CFG_API_KEY, agent_main._CFG_MODEL))
    return {
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "api_key_set": bool(cfg["api_key"] and cfg["api_key"] != "no-key"),
        # which fields are runtime overrides vs. falling back to the env default
        "overridden": {
            "base_url": agent_main._CFG_BASE_URL in overrides,
            "model": agent_main._CFG_MODEL in overrides,
            "api_key": agent_main._CFG_API_KEY in overrides,
        },
        "defaults": {
            "base_url": agent_main.DEFAULT_LLM_BASE_URL,
            "model": agent_main.DEFAULT_MODEL,
        },
    }


@app.put("/api/agent/config")
def set_agent_config(
    base_url: str | None = Body(default=None),
    model: str | None = Body(default=None),
    api_key: str | None = Body(default=None),
    user: dict = Depends(require_admin),
):
    # Only keys present in the body are touched. An empty string clears the
    # override (falls back to the env default); a missing key is left as-is. A
    # sentinel lets the caller omit api_key without clearing the stored one.
    updates: dict[str, str] = {}
    if base_url is not None:
        updates[agent_main._CFG_BASE_URL] = base_url.strip()
    if model is not None:
        updates[agent_main._CFG_MODEL] = model.strip()
    if api_key is not None:
        updates[agent_main._CFG_API_KEY] = api_key.strip()
    if updates:
        db.set_app_config(updates)
    return get_agent_config()


@app.get("/api/agent/models")
def list_agent_models():
    """Query the configured LLM endpoint's /v1/models so the UI can offer a
    dropdown instead of a free-text model name. Returns {available, models[]};
    `available` is False (with `error`) if the endpoint is unreachable."""
    cfg = agent_resolve_llm_config()
    base = cfg["base_url"].rstrip("/")
    url = base + ("/models" if base.endswith("/v1") else "/v1/models")
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg['api_key']}"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.load(resp)
        models = [m.get("id") for m in data.get("data", []) if m.get("id")]
        return {"available": True, "models": models}
    except Exception as e:
        return {"available": False, "models": [], "error": str(e)}


# ── Investigations (boards) ───────────────────────────────────────────────────
# Public, like chat sends — investigations are part of the agent workflow, not
# server config. Only DELETE of a whole investigation is admin-gated (it cascades
# all of its cards).


@app.get("/api/boards")
def list_boards():
    return {"boards": boards_list()}


@app.post("/api/boards")
def create_board(
    title: str = Body(..., embed=True),
    description: str | None = Body(None, embed=True),
):
    title = (title or "").strip()
    if not title:
        return JSONResponse(status_code=400, content={"error": "title required"})
    return {"board": board_create(title, description)}


@app.patch("/api/boards/{id}")
def edit_board(
    id: int,
    title: str | None = Body(None, embed=True),
    description: str | None = Body(None, embed=True),
):
    board = board_update(id, title=title, description=description)
    if board is None:
        return JSONResponse(status_code=404, content={"error": "unknown investigation"})
    return {"board": board}


@app.delete("/api/boards/{id}")
def remove_board(id: int, user: dict = Depends(require_admin)):
    if not board_delete_investigation(id):
        return JSONResponse(status_code=404, content={"error": "unknown investigation"})
    return {"deleted": id}


# ── Investigation cards ───────────────────────────────────────────────────────


@app.get("/api/board")
def get_board(board_id: int | None = Query(None)):
    return {"cards": board_list(board_id)}


@app.post("/api/board")
def add_board_card(
    kind: str = Body(...),
    content: str = Body(...),
    source_conv: str | None = Body(None),
    source_title: str | None = Body(None),
    board_id: int | None = Body(None),
):
    content = content.strip()
    if kind not in ("code", "table") or not content:
        return JSONResponse(status_code=400, content={"error": "kind and content required"})
    card = board_add(kind, content, source_conv, source_title, board_id=board_id)
    return {"card": card}


@app.patch("/api/board/{id}")
def move_board_card(
    id: int,
    col: str = Body(..., embed=True),
    pos: float = Body(..., embed=True),
    board_id: int | None = Body(None, embed=True),
):
    if not board_move(id, col, pos, board_id=board_id):
        return JSONResponse(status_code=404, content={"error": "unknown card"})
    return {"id": id, "col": col, "pos": pos}


@app.delete("/api/board/{id}")
def remove_board_card(id: int):
    if not board_delete(id):
        return JSONResponse(status_code=404, content={"error": "unknown card"})
    return {"deleted": id}


@app.get("/api/jobs")
def get_jobs():
    """Scheduled APScheduler jobs and their next fire time (public, read-only)."""
    sched = getattr(app.state, "scheduler", None)
    if sched is None:
        return {"jobs": []}
    jobs = []
    for j in sched.get_jobs():
        nrt = j.next_run_time
        jobs.append({
            "id": j.id,
            "name": j.name,
            "trigger": str(j.trigger),
            "next_run_time": nrt.isoformat() if nrt else None,
        })
    return {"jobs": jobs}


@app.get("/api/reflection-runs")
def get_reflection_runs():
    """Audit log of the nightly self-reflection job (public, read-only)."""
    return {"runs": reflection_run_list()}


@app.get("/api/agent/reflection-config")
def get_reflection_config():
    """Whether the nightly memory-reflection job runs, and when (public, read-only)."""
    cfg = _reflect_config()
    next_run = None
    sched = getattr(app.state, "scheduler", None)
    if sched is not None:
        job = sched.get_job("reflect")
        if job is not None and job.next_run_time is not None:
            next_run = job.next_run_time.isoformat()
    return {**cfg, "next_run_time": next_run}


@app.put("/api/agent/reflection-config")
def set_reflection_config(
    enabled: bool | None = Body(default=None),
    hour: int | None = Body(default=None),
    minute: int | None = Body(default=None),
    user: dict = Depends(require_admin),
):
    updates: dict[str, str] = {}
    if enabled is not None:
        updates[_CFG_REFLECT_ENABLED] = "1" if enabled else "0"
    if hour is not None:
        if not 0 <= hour <= 23:
            return JSONResponse(status_code=400, content={"error": "hour must be 0-23"})
        updates[_CFG_REFLECT_HOUR] = str(hour)
    if minute is not None:
        if not 0 <= minute <= 59:
            return JSONResponse(status_code=400, content={"error": "minute must be 0-59"})
        updates[_CFG_REFLECT_MINUTE] = str(minute)
    if updates:
        db.set_app_config(updates)
    sched = getattr(app.state, "scheduler", None)
    if sched is not None:
        _apply_reflect_schedule(sched)
    return get_reflection_config()


