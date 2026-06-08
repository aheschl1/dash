import subprocess
import re


def _nsenter(cmd: list[str]) -> str:
    return subprocess.check_output(
        ["nsenter", "--net=/proc/1/ns/net", "--"] + cmd,
        timeout=5, text=True,
    )


def _interface_ip() -> str | None:
    try:
        out = _nsenter(["ip", "addr", "show", "wg0"])
        m = re.search(r"inet (\S+)", out)
        return m.group(1) if m else None
    except Exception:
        return None


def _peers() -> list[dict]:
    try:
        out = _nsenter(["wg", "show"])
    except Exception:
        return []

    peers = []
    current: dict | None = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("peer:"):
            if current:
                peers.append(current)
            current = {"pubkey": line.split("peer:")[1].strip(), "endpoint": None,
                       "allowed_ips": None, "last_handshake": None, "transfer": None}
        elif current:
            if line.startswith("endpoint:"):
                current["endpoint"] = line.split("endpoint:")[1].strip()
            elif line.startswith("allowed ips:"):
                current["allowed_ips"] = line.split("allowed ips:")[1].strip()
            elif line.startswith("latest handshake:"):
                current["last_handshake"] = line.split("latest handshake:")[1].strip()
            elif line.startswith("transfer:"):
                current["transfer"] = line.split("transfer:")[1].strip()
    if current:
        peers.append(current)

    return peers


def collect() -> dict:
    ip = _interface_ip()
    peers = _peers()
    return {
        "interface_ip": ip,
        "peers": peers,
        "peer_count": len(peers),
    }
