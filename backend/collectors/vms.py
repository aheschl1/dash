import subprocess
import time

# name -> (cumulative cpu.time in ns, monotonic timestamp) for CPU% rate calc
_last_cpu: dict = {}


def _virsh(args: list[str], timeout: int = 8) -> str | None:
    """Run virsh in the host mount+net namespace so it reaches the host's
    libvirtd socket (the container has its own /run otherwise). Mirrors the
    nsenter pattern used by disk.py / network.py."""
    try:
        r = subprocess.run(
            ["nsenter", "--mount=/proc/1/ns/mnt", "--net=/proc/1/ns/net", "--",
             "virsh", "-c", "qemu:///system"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def _int(val):
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def _kib_to_mb(val) -> int | None:
    # dominfo reports memory as e.g. "1048576 KiB"
    n = _int(str(val).split()[0]) if val else None
    return round(n / 1024) if n is not None else None


def _strip_s(val):
    # "CPU time: 819.5s"
    if not val:
        return None
    try:
        return round(float(str(val).strip().rstrip("s")), 1)
    except ValueError:
        return None


def _parse_kv(text: str) -> dict:
    info = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    return info


def _parse_domstats(text: str) -> dict:
    """Parse `virsh domstats` blocks into {domain_name: {stat_key: value}}."""
    blocks: dict = {}
    current = None
    for line in text.splitlines():
        if line.startswith("Domain:"):
            name = line.split("'")[1] if "'" in line else None
            current = {}
            if name:
                blocks[name] = current
        elif "=" in line and current is not None:
            k, v = line.strip().split("=", 1)
            current[k] = v
    return blocks


def _domain_names() -> list[str]:
    listing = _virsh(["list", "--all", "--name"])
    if listing is None:
        return []
    return [n.strip() for n in listing.splitlines() if n.strip()]


def _valid_name(name: str) -> bool:
    """Gate every per-VM call: `name` flows into a file path (logs) and virsh
    args, so only accept names that are actually defined domains."""
    return name in _domain_names()


def logs(name: str, lines: int = 100) -> dict:
    if not _valid_name(name):
        return {"error": f"VM '{name}' not found"}
    # qemu writes per-domain logs to the host's /var/log/libvirt/qemu/<name>.log;
    # read it in the host mount namespace like the nsenter collectors do.
    try:
        r = subprocess.run(
            ["nsenter", "--mount=/proc/1/ns/mnt", "--",
             "tail", "-n", str(lines), f"/var/log/libvirt/qemu/{name}.log"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception as e:
        return {"error": str(e)}
    if r.returncode != 0:
        return {"error": (r.stderr.strip() or "could not read log file")}
    log_lines = [l for l in r.stdout.splitlines() if l]
    return {"name": name, "lines": log_lines}


def _parse_table(text: str) -> list[list[str]]:
    """Parse a virsh table (header row, dashed separator, then data rows) into a
    list of column-token lists."""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s or set(s) <= set("- "):
            continue
        rows.append(s.split())
    return rows[1:] if rows else []


def net_info(name: str) -> dict:
    if not _valid_name(name):
        return {"error": f"VM '{name}' not found"}

    iflist = _virsh(["domiflist", name]) or ""
    interfaces: dict = {}
    order: list[str] = []
    for cols in _parse_table(iflist):
        # Interface Type Source Model MAC
        if len(cols) < 5:
            continue
        mac = cols[4].lower()
        interfaces[mac] = {
            "interface": cols[0],
            "type": cols[1],
            "source": cols[2],
            "model": cols[3],
            "mac": cols[4],
            "addresses": [],
        }
        order.append(mac)

    source = None
    addr_text = _virsh(["domifaddr", name, "--source", "lease"]) or ""
    addr_rows = _parse_table(addr_text)
    if addr_rows:
        source = "lease"
    else:
        addr_text = _virsh(["domifaddr", name, "--source", "arp"]) or ""
        addr_rows = _parse_table(addr_text)
        if addr_rows:
            source = "arp"

    for cols in addr_rows:
        # Name MAC Protocol Address(/prefix)
        if len(cols) < 4:
            continue
        mac = cols[1].lower()
        addr = cols[3]
        prefix = None
        if "/" in addr:
            addr, _, p = addr.partition("/")
            # arp source reports a bogus /0 prefix; ignore it
            prefix = _int(p) if source != "arp" else None
        entry = interfaces.get(mac)
        if entry is not None:
            entry["addresses"].append({"protocol": cols[2], "address": addr, "prefix": prefix})

    ifaces = [interfaces[m] for m in order]
    return {
        "name": name,
        "available": bool(ifaces),
        "source": source,
        "interfaces": ifaces,
    }


def collect() -> dict:
    listing = _virsh(["list", "--all", "--name"])
    if listing is None:
        # libvirt not reachable (no virsh, daemon down, or no VMs host)
        return {"available": False, "vms": []}

    names = [n.strip() for n in listing.splitlines() if n.strip()]
    stats = _parse_domstats(_virsh(["domstats", "--balloon", "--vcpu", "--cpu-total"]) or "")

    now = time.monotonic()
    vms = []
    for name in names:
        raw = _virsh(["dominfo", name])
        if raw is None:
            continue
        di = _parse_kv(raw)
        st = stats.get(name, {})

        vcpus = _int(di.get("CPU(s)"))

        # Guest-side memory actually in use, from the balloon driver.
        guest_avail = _int(st.get("balloon.available"))
        guest_unused = _int(st.get("balloon.unused"))
        guest_used_mb = guest_used_pct = None
        if guest_avail and guest_unused is not None:
            used_kib = guest_avail - guest_unused
            guest_used_mb = round(used_kib / 1024)
            guest_used_pct = round(used_kib / guest_avail * 100, 1)

        rss_kib = _int(st.get("balloon.rss"))
        rss_mb = round(rss_kib / 1024) if rss_kib else None

        # CPU% as a fraction of the VM's allocated vCPUs, from the delta of the
        # cumulative cpu.time counter between two polls.
        cpu_pct = None
        cpu_time_ns = _int(st.get("cpu.time"))
        if cpu_time_ns is not None:
            prev = _last_cpu.get(name)
            _last_cpu[name] = (cpu_time_ns, now)
            if prev:
                prev_ns, prev_ts = prev
                dt = now - prev_ts
                if dt > 0 and cpu_time_ns >= prev_ns:
                    busy = (cpu_time_ns - prev_ns) / 1e9
                    cpu_pct = min(round(busy / dt / (vcpus or 1) * 100, 1), 100.0)
        else:
            _last_cpu.pop(name, None)

        vms.append({
            "name": name,
            "id": _int(di.get("Id")),
            "state": di.get("State", "unknown"),
            "vcpus": vcpus,
            "max_mem_mb": _kib_to_mb(di.get("Max memory")),
            "alloc_mem_mb": _kib_to_mb(di.get("Used memory")),
            "guest_used_mb": guest_used_mb,
            "guest_used_pct": guest_used_pct,
            "rss_mb": rss_mb,
            "cpu_time_s": _strip_s(di.get("CPU time")),
            "cpu_pct": cpu_pct,
            "autostart": di.get("Autostart") == "enable",
            "persistent": di.get("Persistent") == "yes",
        })

    live = {v["name"] for v in vms}
    for stale in [n for n in _last_cpu if n not in live]:
        _last_cpu.pop(stale, None)

    vms.sort(key=lambda v: (v["state"] != "running", v["name"]))
    return {"available": True, "vms": vms}
