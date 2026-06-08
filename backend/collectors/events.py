import docker
import time

_last_ts = None

def poll_and_store(store_fn) -> None:
    global _last_ts
    now = int(time.time())
    since = _last_ts if _last_ts else now - 60
    _last_ts = now
    try:
        client = docker.from_env()
        for ev in client.events(since=since, until=now, decode=True):
            if ev.get("Type") != "container":
                continue
            action = ev.get("Action", "")
            # Only store meaningful actions
            if action not in ("start", "stop", "die", "kill", "restart", "oom", "create", "destroy",
                              "health_status: healthy", "health_status: unhealthy"):
                continue
            attrs = ev.get("Actor", {}).get("Attributes", {})
            container = attrs.get("name", ev.get("id", "")[:12])
            image = attrs.get("image", "")
            store_fn(int(ev.get("time", now)), action, container, image)
    except Exception:
        pass
