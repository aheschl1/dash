import subprocess
import re

# Host cron lives behind the host mount namespace (container is pid:host).
_NSENTER = ["nsenter", "--mount=/proc/1/ns/mnt", "--"]

# System crontabs carry a user field between the schedule and the command;
# per-user spool files do not (the user is the filename).
_SYSTEM_FILE = "/etc/crontab"
_SYSTEM_DIR = "/etc/cron.d"
_SPOOL_DIRS = ["/var/spool/cron/crontabs", "/var/spool/cron"]

_ENV_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=")


def _run(args: list[str]) -> str | None:
    try:
        return subprocess.check_output(
            _NSENTER + args, timeout=5, text=True, stderr=subprocess.DEVNULL
        )
    except Exception:
        return None


def _list_dir(path: str) -> list[str]:
    out = _run(["ls", "-1", path])
    if not out:
        return []
    # Skip backup/dotfiles the way cron itself does.
    return [
        f for f in out.split()
        if not f.startswith(".") and not f.endswith(("~", ".dpkg-dist", ".dpkg-old"))
    ]


def _split_schedule(line: str, has_user: bool) -> dict | None:
    """Parse one crontab line into {schedule, user, command} or None to skip."""
    line = line.strip()
    if not line or line.startswith("#") or _ENV_RE.match(line):
        return None

    if line.startswith("@"):
        # Nickname schedules: @reboot, @daily, … then optional user, then command.
        parts = line.split(None, 1)
        schedule, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    else:
        parts = line.split(None, 5)
        if len(parts) < 6:
            return None
        schedule = " ".join(parts[:5])
        rest = parts[5]

    user = ""
    command = rest
    if has_user and rest:
        sub = rest.split(None, 1)
        user = sub[0]
        command = sub[1] if len(sub) > 1 else ""

    return {"schedule": schedule, "user": user, "command": command.strip()}


def _parse(text: str, source: str, has_user: bool, default_user: str = "") -> list[dict]:
    jobs = []
    for line in text.splitlines():
        job = _split_schedule(line, has_user)
        if not job:
            continue
        if not job["user"]:
            job["user"] = default_user
        job["source"] = source
        jobs.append(job)
    return jobs


def collect() -> list[dict]:
    jobs: list[dict] = []

    system_text = _run(["cat", _SYSTEM_FILE])
    if system_text:
        jobs += _parse(system_text, "/etc/crontab", has_user=True, default_user="root")

    for name in _list_dir(_SYSTEM_DIR):
        path = f"{_SYSTEM_DIR}/{name}"
        text = _run(["cat", path])
        if text:
            jobs += _parse(text, path, has_user=True, default_user="root")

    seen_users: set[str] = set()
    for spool in _SPOOL_DIRS:
        for user in _list_dir(spool):
            if user in seen_users:
                continue
            text = _run(["cat", f"{spool}/{user}"])
            if text:
                seen_users.add(user)
                jobs += _parse(text, f"crontab:{user}", has_user=False, default_user=user)

    return jobs
