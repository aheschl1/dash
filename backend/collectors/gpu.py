import subprocess


def collect() -> dict | None:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            timeout=5,
            text=True,
        ).strip()
    except Exception:
        return None

    parts = [p.strip() for p in out.split(",")]
    if len(parts) < 5:
        return None

    return {
        "name": parts[0],
        "temp_c": int(parts[1]),
        "util_pct": int(parts[2]),
        "vram_used_mib": int(parts[3]),
        "vram_total_mib": int(parts[4]),
    }


def collect_processes() -> list[dict]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory,name", "--format=csv,noheader,nounits"],
            timeout=5, text=True
        ).strip()
    except Exception:
        return []
    if not out:
        return []
    procs = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            procs.append({"pid": int(parts[0]), "vram_mib": int(parts[1]), "name": parts[2]})
    return procs
