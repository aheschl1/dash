import ipaddress
import re
import subprocess

from . import wireguard

# The host's setuid VPN management CLI. Reached through the host mount/net
# namespaces (pid 1) because it lives on the host filesystem and edits the
# host's wg config — same nsenter technique the read-only collectors use.
ADMIN_BIN = "/home/andrew/.cargo/bin/admin"
# Interface name is shared with the wireguard collector (WG_INTERFACE env override).
WG_INTERFACE = wireguard.WG_INTERFACE
VPN_SUBNET = ipaddress.ip_network("10.8.0.0/24")
SERVER_IP = ipaddress.ip_address("10.8.0.1")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# admin vpn add writes the client config (incl. the freshly generated private
# key) to <cwd>/<name>/<name>.conf as root; we run it inside a throwaway temp
# dir, read the file back, and delete it. name/address arrive as positional
# args ($2/$3) rather than being interpolated into the script, so a hostile
# name can't break out into the shell.
_ADD_SCRIPT = """
set -euo pipefail
work=$(mktemp -d /tmp/admindash-vpn.XXXXXX)
trap 'rm -rf "$work"' EXIT
cd "$work"
"$1" vpn add "$2" "$3" >&2
cat "$2/$2.conf"
"""

# Apply the new peer to the live interface without tearing it down, so existing
# peers (including the dashboard's own connection over wg0) keep their handshake.
_SYNC_SCRIPT = (
    "export PATH=/usr/sbin:/usr/bin:/sbin:/bin; "
    f"wg syncconf {WG_INTERFACE} <(wg-quick strip {WG_INTERFACE})"
)


def _host(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"] + cmd,
        capture_output=True, text=True, timeout=timeout,
    )


def _validate(name: str, address: str) -> tuple[str, str]:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise ValueError("name must be 1-32 chars: letters, digits, '-' or '_'")
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        raise ValueError("address must be a valid IPv4 address")
    if ip not in VPN_SUBNET:
        raise ValueError(f"address must be within {VPN_SUBNET}")
    if ip in (SERVER_IP, VPN_SUBNET.network_address, VPN_SUBNET.broadcast_address):
        raise ValueError("address is reserved")
    return name, str(ip)


def _used_addresses() -> set[str]:
    used = set()
    for peer in wireguard._peers():
        for part in (peer.get("allowed_ips") or "").split(","):
            host = part.strip().split("/")[0]
            try:
                used.add(str(ipaddress.ip_address(host)))
            except ValueError:
                pass
    return used


def add_client(name: str, address: str) -> dict:
    name, address = _validate(name, address)
    if address in _used_addresses():
        raise ValueError(f"address {address} is already assigned to a peer")

    added = _host(["bash", "-c", _ADD_SCRIPT, "_", ADMIN_BIN, name, address])
    if added.returncode != 0 or "[Interface]" not in added.stdout:
        raise RuntimeError((added.stderr or added.stdout or "admin vpn add failed").strip())
    config = added.stdout

    synced = _host(["bash", "-c", _SYNC_SCRIPT])
    activated = synced.returncode == 0

    return {
        "name": name,
        "address": address,
        "filename": f"{name}.conf",
        "config": config,
        "activated": activated,
        "activation_error": None if activated else (synced.stderr or synced.stdout or "syncconf failed").strip(),
    }
