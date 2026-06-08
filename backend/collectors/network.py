import subprocess
import time
import re

_last: dict = {}  # {iface: (rx_bytes, tx_bytes, timestamp)}

_SKIP_PATTERN = re.compile(r'^(lo|docker\d*|br-[a-f0-9]+|veth[a-z0-9]+)$')


def _get_counters() -> dict:
    """Run nsenter to read host /proc/net/dev counters."""
    try:
        result = subprocess.run(
            ["nsenter", "--net=/proc/1/ns/net", "--", "cat", "/proc/net/dev"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = result.stdout.strip().splitlines()
    except Exception:
        return {}

    counters = {}
    # Skip first two header lines
    for line in lines[2:]:
        line = line.strip()
        if not line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            rx_bytes = int(fields[0])
            tx_bytes = int(fields[8])
        except (ValueError, IndexError):
            continue
        counters[iface] = (rx_bytes, tx_bytes)
    return counters


def collect() -> list:
    global _last

    now = time.time()
    current = _get_counters()
    results = []

    for iface, (rx_bytes, tx_bytes) in current.items():
        if _SKIP_PATTERN.match(iface):
            continue

        rx_bps = 0.0
        tx_bps = 0.0

        if iface in _last:
            prev_rx, prev_tx, prev_time = _last[iface]
            dt = now - prev_time
            if dt > 0:
                rx_bps = max(0.0, (rx_bytes - prev_rx) / dt)
                tx_bps = max(0.0, (tx_bytes - prev_tx) / dt)

        _last[iface] = (rx_bytes, tx_bytes, now)

        results.append({
            "iface": iface,
            "rx_bps": round(rx_bps, 2),
            "tx_bps": round(tx_bps, 2),
            "rx_total_gb": round(rx_bytes / 1024**3, 3),
            "tx_total_gb": round(tx_bytes / 1024**3, 3),
        })

    return results
