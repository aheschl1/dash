import csv
from pathlib import Path

# Static service directory, edited as a CSV alongside the backend so it ships
# in the image. Maps friendly names → Cloudflare DNS domains and/or host:port
# addresses for the services running on this box.
_CSV = Path(__file__).resolve().parent.parent / "directory.csv"


def collect() -> list[dict]:
    try:
        with _CSV.open(newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return []

    results = []
    for row in rows:
        name = (row.get("Name") or "").strip()
        domain = (row.get("Domain") or "").strip()
        ip = (row.get("Ip") or "").strip()
        if not name:
            continue
        results.append({"name": name, "domain": domain, "ip": ip})
    return results
