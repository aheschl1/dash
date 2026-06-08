import subprocess
import re


def collect() -> list[dict]:
    try:
        out = subprocess.check_output(
            ["nsenter", "--net=/proc/1/ns/net", "--", "ss", "-tlnp"],
            timeout=5,
            text=True,
        )
    except Exception:
        return []

    results = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue

        local = parts[3]
        process = ""
        if len(parts) >= 6:
            proc_match = re.search(r'users:\(\("([^"]+)"', parts[5])
            if proc_match:
                process = proc_match.group(1)

        # split address:port — handle IPv6 like [::]:8080
        if local.startswith("["):
            addr, port = local.rsplit(":", 1)
            addr = addr.strip("[]") or "::"
        elif ":" in local:
            addr, port = local.rsplit(":", 1)
        else:
            continue

        results.append({
            "address": addr,
            "port": int(port),
            "process": process,
        })

    results.sort(key=lambda x: x["port"])
    return results
