import subprocess
import re
import sys
import json
import os
import ipaddress

MAX_ROWS = 400
HELPER = os.path.join(os.path.dirname(__file__), "conntrack_helper.py")
SCOPE_RANK = {"public": 0, "private": 1, "other": 2, "loopback": 3}


def _split_addr(endpoint: str) -> tuple[str, str]:
    if endpoint.startswith("["):
        addr, port = endpoint.rsplit(":", 1)
        addr = addr.strip("[]") or "::"
    elif ":" in endpoint:
        addr, port = endpoint.rsplit(":", 1)
    else:
        addr, port = endpoint, ""
    return addr, port


def _scope(addr: str) -> str:
    if not addr:
        return "other"
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return "other"
    if ip.is_loopback:
        return "loopback"
    if ip.is_global:
        return "public"
    return "private"


def _ss_process_map() -> dict[tuple[str, str], str]:
    """Map (proto, local_port) -> process name, for enriching conntrack flows."""
    out = _run(["ss", "-tunap"])
    proc_map: dict[tuple[str, str], str] = {}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 7:
            continue
        proto = parts[0]
        _, local_port = _split_addr(parts[4])
        m = re.search(r'users:\(\("([^"]+)"', parts[6])
        if m and local_port:
            proc_map.setdefault((proto, local_port), m.group(1))
    return proc_map


def _run(args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["nsenter", "--net=/proc/1/ns/net", "--", *args],
            timeout=8, text=True,
        )
    except Exception:
        return ""


def _conntrack_flows() -> list[dict]:
    try:
        out = subprocess.check_output(
            ["nsenter", "--net=/proc/1/ns/net", "--", sys.executable, HELPER],
            timeout=10, text=True,
        )
        return json.loads(out) if out.strip() else []
    except Exception:
        return []


def _ss_connections() -> list[dict]:
    """Fallback: established sockets straight from ss (TCP/UDP with a peer)."""
    out = _run(["ss", "-tunap"])
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        proto, state = parts[0], parts[1]
        local_addr, local_port = _split_addr(parts[4])
        peer_addr, peer_port = _split_addr(parts[5])
        if peer_port in ("*", "") or state == "LISTEN":
            continue
        process = ""
        if len(parts) >= 7:
            m = re.search(r'users:\(\("([^"]+)"', parts[6])
            if m:
                process = m.group(1)
        rows.append({
            "proto": proto, "state": state,
            "src_addr": local_addr, "src_port": local_port,
            "dst_addr": peer_addr, "dst_port": peer_port,
            "process": process, "nat": False,
        })
    return rows


def _finalize(rows: list[dict]) -> dict:
    by_proto: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    for r in rows:
        src_scope = _scope(r["src_addr"])
        dst_scope = _scope(r["dst_addr"])
        # a flow's scope is its most-remote endpoint
        flow_scope = src_scope if SCOPE_RANK[src_scope] <= SCOPE_RANK[dst_scope] else dst_scope
        r["src_scope"] = src_scope
        r["dst_scope"] = dst_scope
        r["scope"] = flow_scope
        by_proto[r["proto"]] = by_proto.get(r["proto"], 0) + 1
        by_scope[flow_scope] = by_scope.get(flow_scope, 0) + 1

    rows.sort(key=lambda r: (SCOPE_RANK.get(r["scope"], 9), r["proto"],
                             r["dst_addr"], str(r["dst_port"])))
    return {
        "connections": rows[:MAX_ROWS],
        "by_proto": by_proto,
        "by_scope": by_scope,
        "total": len(rows),
    }


def collect() -> dict:
    flows = _conntrack_flows()
    if flows:
        proc_map = _ss_process_map()
        for f in flows:
            sp = str(f["src_port"]) if f["src_port"] is not None else ""
            dp = str(f["dst_port"]) if f["dst_port"] is not None else ""
            f["src_port"] = sp
            f["dst_port"] = dp
            # the host's local port is whichever side ss knows a process for
            f["process"] = (proc_map.get((f["proto"], sp))
                            or proc_map.get((f["proto"], dp)) or "")
        result = _finalize(flows)
        result["source"] = "conntrack"
        return result

    result = _finalize(_ss_connections())
    result["source"] = "ss"
    return result
