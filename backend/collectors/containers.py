import docker


def _cpu_percent(stats: dict) -> float | None:
    """Container CPU% of the whole host, via Docker's standard delta formula.

    Uses the daemon-provided `precpu_stats` (its prior 1s sample) so a single
    non-one-shot read is enough; returns None if the deltas aren't available."""
    try:
        cpu = stats["cpu_stats"]
        pre = stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        system_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        online = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
    except (KeyError, TypeError):
        return None
    if system_delta <= 0 or cpu_delta < 0:
        return 0.0
    return round((cpu_delta / system_delta) * online * 100, 2)


def _memory(stats: dict) -> dict:
    """Memory usage split into application load vs. reclaimable page cache.

    Docker reports `usage` inclusive of filesystem cache; subtracting the cache
    gives the figure `docker stats` shows. The cache key differs by cgroup
    version: v1 exposes `cache`, v2 exposes `inactive_file`."""
    mem = stats.get("memory_stats") or {}
    usage = mem.get("usage") or 0
    limit = mem.get("limit") or 0
    detail = mem.get("stats") or {}
    cache = detail.get("cache")
    if cache is None:
        cache = detail.get("inactive_file") or 0
    app_usage = usage - cache if usage >= cache else usage
    return {
        "usage_bytes": usage,
        "limit_bytes": limit,
        "cache_bytes": cache,
        "app_usage_bytes": app_usage,
        "usage_pct": round(app_usage / limit * 100, 2) if limit else None,
    }


def _network(stats: dict) -> dict:
    """Cumulative bytes rx/tx since the container started, summed over interfaces."""
    nets = stats.get("networks") or {}
    return {
        "rx_bytes_total": sum((n or {}).get("rx_bytes", 0) for n in nets.values()),
        "tx_bytes_total": sum((n or {}).get("tx_bytes", 0) for n in nets.values()),
    }


def _block_io(stats: dict) -> dict:
    """Cumulative bytes read/written to block devices since the container started."""
    entries = (stats.get("blkio_stats") or {}).get("io_service_bytes_recursive") or []
    read = sum(e.get("value", 0) for e in entries if (e.get("op") or "").lower() == "read")
    write = sum(e.get("value", 0) for e in entries if (e.get("op") or "").lower() == "write")
    return {"read_bytes_total": read, "write_bytes_total": write}


def stats(name: str) -> dict:
    """Granular live resource stats for one container (single snapshot).

    Raises docker.errors.NotFound for an unknown name (the endpoint maps it to a
    404). CPU% is instantaneous; network and block I/O are cumulative totals since
    container start — sample twice and difference them for a rate."""
    client = docker.from_env()
    c = client.containers.get(name)
    raw = c.stats(stream=False)
    return {
        "name": c.name,
        "status": c.status,
        "cpu_pct": _cpu_percent(raw),
        "memory": _memory(raw),
        "network": _network(raw),
        "block_io": _block_io(raw),
    }


def collect() -> list[dict]:
    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)
    except Exception:
        return []

    result = []
    for c in containers:
        ports = []
        for container_port, bindings in (c.ports or {}).items():
            if bindings:
                for b in bindings:
                    ports.append(f"{b['HostIp']}:{b['HostPort']}->{container_port}")
            else:
                ports.append(container_port)

        health = "none"
        if c.attrs.get("State", {}).get("Health"):
            health = c.attrs["State"]["Health"].get("Status", "none")

        result.append({
            "name": c.name,
            "image": c.image.tags[0] if c.image.tags else c.image.short_id,
            "status": c.status,
            "health": health,
            "ports": ports,
        })

    result.sort(key=lambda x: (x["status"] != "running", x["name"]))
    return result
