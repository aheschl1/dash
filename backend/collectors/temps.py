import subprocess
import json


def collect() -> dict:
    try:
        out = subprocess.check_output(["sensors", "-j"], timeout=5, text=True)
        data = json.loads(out)
    except Exception:
        return {}

    result = {}

    for chip, sensors in data.items():
        if "k10temp" in chip:
            for key, vals in sensors.items():
                if isinstance(vals, dict):
                    for subkey, v in vals.items():
                        if "input" in subkey and isinstance(v, (int, float)):
                            label = key.replace(" ", "_")
                            result[f"cpu_{label}"] = round(v, 1)

        elif "nvme" in chip.lower():
            for key, vals in sensors.items():
                if isinstance(vals, dict):
                    for subkey, v in vals.items():
                        if "input" in subkey and isinstance(v, (int, float)):
                            label = key.replace(" ", "_")
                            result[f"nvme_{label}"] = round(v, 1)

    return result
