import docker


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
