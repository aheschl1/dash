import psutil
import time


def collect() -> dict:
    cpu = psutil.cpu_percent(interval=0.5)
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime_secs = int(time.time() - psutil.boot_time())

    hours, rem = divmod(uptime_secs, 3600)
    minutes = rem // 60

    return {
        "cpu_pct": cpu,
        "ram_used_gb": round(vm.used / 1024**3, 2),
        "ram_total_gb": round(vm.total / 1024**3, 2),
        "ram_pct": vm.percent,
        "disk_used_gb": round(disk.used / 1024**3, 1),
        "disk_total_gb": round(disk.total / 1024**3, 1),
        "disk_pct": disk.percent,
        "uptime": f"{hours}h {minutes}m",
    }
