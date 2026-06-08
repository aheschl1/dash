import subprocess
import json


def _scan_devices() -> list[str]:
    """Return block devices that smartctl can open."""
    try:
        out = subprocess.check_output(["smartctl", "--scan"], timeout=5, text=True)
        return [line.split()[0] for line in out.splitlines() if line.strip()]
    except Exception:
        return []


def _query(dev: str) -> dict | None:
    try:
        # Run smartctl directly — container is privileged so block devices are accessible
        out = subprocess.check_output(
            ["smartctl", "--json=c", "-A", dev],
            timeout=10, text=True
        )
        data = json.loads(out)
        if data.get("smartctl", {}).get("exit_status", 1) & 0b00000110:
            return None  # SMART not available or errors
    except Exception:
        return None

    result = {"device": dev, "type": None, "attrs": {}}

    # NVMe
    if "nvme_smart_health_information_log" in data:
        result["type"] = "nvme"
        log = data["nvme_smart_health_information_log"]
        result["attrs"] = {
            "temperature_c": data.get("temperature", {}).get("current"),
            "available_spare_pct": log.get("available_spare"),
            "percentage_used": log.get("percentage_used"),
            "power_on_hours": data.get("power_on_time", {}).get("hours"),
            "unsafe_shutdowns": log.get("unsafe_shutdowns"),
        }
    # SATA/HDD
    elif "ata_smart_attributes" in data:
        result["type"] = "ata"
        attrs = {}
        for entry in data["ata_smart_attributes"].get("table", []):
            name = entry.get("name")
            val = entry.get("raw", {}).get("value")
            if name in ("Reallocated_Sector_Ct", "Power_On_Hours", "Temperature_Celsius",
                        "Current_Pending_Sector", "Offline_Uncorrectable"):
                attrs[name] = val
        result["attrs"] = attrs
        if "temperature" in data:
            result["attrs"]["Temperature_Celsius"] = data["temperature"].get("current")

    return result


def collect() -> list[dict]:
    results = []
    for dev in _scan_devices():
        r = _query(dev)
        if r:
            results.append(r)
    return results
