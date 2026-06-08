import psutil


def collect() -> list:
    procs = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'status']):
        try:
            info = proc.info
            procs.append({
                "pid": info['pid'],
                "name": info['name'] or "",
                "cpu_pct": round(float(info['cpu_percent'] or 0.0), 1),
                "mem_pct": round(float(info['memory_percent'] or 0.0), 2),
                "status": info['status'] or "",
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda p: p['cpu_pct'], reverse=True)
    return procs[:10]
