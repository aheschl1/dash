import subprocess
import ipaddress


def _scope(host: str) -> str:
    if not host:
        return "local"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "other"
    if ip.is_loopback:
        return "loopback"
    if ip.is_global:
        return "public"
    return "private"


def collect() -> list[dict]:
    try:
        out = subprocess.check_output(
            ["nsenter", "--mount=/proc/1/ns/mnt", "--", "who"],
            timeout=5, text=True
        )
    except Exception:
        return []
    sessions = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        user = parts[0]
        tty = parts[1]
        login_time = f"{parts[2]} {parts[3]}"
        host = ""
        if len(parts) >= 5:
            host = parts[4].strip("()")
        scope = _scope(host)
        sessions.append({
            "user": user,
            "tty": tty,
            "login_time": login_time,
            "host": host,
            "scope": scope,
            "public": scope == "public",
        })
    return sessions
