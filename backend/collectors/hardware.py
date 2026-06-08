import subprocess

import psutil


def _lscpu() -> dict:
    """Static CPU facts from lscpu. The CPU is physical, so the values are
    identical inside the container and on the host — no nsenter needed."""
    try:
        out = subprocess.check_output(["lscpu"], timeout=5, text=True)
    except Exception:
        return {}
    info = {}
    for line in out.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        info[key.strip()] = val.strip()
    return info


def _int(val):
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def _float(val):
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return None


def collect() -> dict:
    lc = _lscpu()

    freq = psutil.cpu_freq()
    try:
        load = list(psutil.getloadavg())
    except (OSError, AttributeError):
        load = None

    per_core = psutil.cpu_percent(interval=0.3, percpu=True)

    freq_max = (freq.max if freq and freq.max else None) or _float(lc.get("CPU max MHz"))
    freq_min = (freq.min if freq and freq.min else None) or _float(lc.get("CPU min MHz"))

    return {
        "model": lc.get("Model name"),
        "vendor": lc.get("Vendor ID"),
        "arch": lc.get("Architecture"),
        "sockets": _int(lc.get("Socket(s)")),
        "cores_per_socket": _int(lc.get("Core(s) per socket")),
        "threads_per_core": _int(lc.get("Thread(s) per core")),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "freq_current_mhz": round(freq.current) if freq and freq.current else None,
        "freq_min_mhz": round(freq_min) if freq_min else None,
        "freq_max_mhz": round(freq_max) if freq_max else None,
        "l1d_cache": lc.get("L1d cache"),
        "l1i_cache": lc.get("L1i cache"),
        "l2_cache": lc.get("L2 cache"),
        "l3_cache": lc.get("L3 cache"),
        "load_avg": [round(x, 2) for x in load] if load else None,
        "per_core_pct": [round(p, 1) for p in per_core],
    }
