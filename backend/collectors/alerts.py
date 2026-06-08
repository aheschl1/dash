from collectors import (disk as disk_col, gpu as gpu_col, temps as temps_col,
                        containers as containers_col, sessions as sessions_col)


def collect() -> list[dict]:
    alerts = []

    # Disk
    try:
        for p in disk_col.collect():
            if p["pct"] >= 95:
                alerts.append({"level": "critical", "category": "disk",
                    "message": f"Disk {p['mount']} is {p['pct']}% full ({p['avail_gb']} GB free)"})
            elif p["pct"] >= 90:
                alerts.append({"level": "warning", "category": "disk",
                    "message": f"Disk {p['mount']} is {p['pct']}% full ({p['avail_gb']} GB free)"})
    except Exception:
        pass

    # GPU temp
    try:
        g = gpu_col.collect()
        if g and g["temp_c"] >= 85:
            alerts.append({"level": "critical", "category": "gpu",
                "message": f"GPU temperature critical: {g['temp_c']}°C"})
        elif g and g["temp_c"] >= 80:
            alerts.append({"level": "warning", "category": "gpu",
                "message": f"GPU temperature high: {g['temp_c']}°C"})
    except Exception:
        pass

    # CPU temp
    try:
        t = temps_col.collect()
        cpu_temp = t.get("cpu_Tctl")
        if cpu_temp and cpu_temp >= 85:
            alerts.append({"level": "critical", "category": "cpu",
                "message": f"CPU temperature critical: {cpu_temp}°C"})
        elif cpu_temp and cpu_temp >= 70:
            alerts.append({"level": "warning", "category": "cpu",
                "message": f"CPU temperature high: {cpu_temp}°C"})
    except Exception:
        pass

    # Unhealthy containers
    try:
        for c in containers_col.collect():
            if c["health"] == "unhealthy":
                alerts.append({"level": "warning", "category": "container",
                    "message": f"Container {c['name']} is unhealthy"})
            if c["status"] == "exited" and c["name"] not in ("admindash",):
                alerts.append({"level": "warning", "category": "container",
                    "message": f"Container {c['name']} has exited"})
    except Exception:
        pass

    # SSH/login sessions originating from a public (off-VPN) IP
    try:
        for s in sessions_col.collect():
            if s.get("public"):
                alerts.append({"level": "critical", "category": "session",
                    "message": f"Login session from public IP {s['host']} ({s['user']}@{s['tty']})"})
    except Exception:
        pass

    return alerts
