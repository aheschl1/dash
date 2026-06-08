"""Dump the kernel conntrack flow table as JSON.

Run inside the host network namespace via nsenter; conntrack is read over a
netlink socket, which is namespace-scoped, so the process must already be in
the target netns. Output is a JSON list of flows on stdout. Any failure prints
an empty list and exits 0 so the caller degrades gracefully.
"""
import json
import sys
from socket import AF_INET, AF_INET6

# conntrack TCP state enum (net/netfilter/nf_conntrack_tcp.h)
TCP_STATES = [
    "NONE", "SYN_SENT", "SYN_RECV", "ESTABLISHED", "FIN_WAIT",
    "CLOSE_WAIT", "LAST_ACK", "TIME_WAIT", "CLOSE", "SYN_SENT2",
]

L4PROTO = {1: "icmp", 6: "tcp", 17: "udp", 58: "icmpv6", 132: "sctp", 136: "udplite"}


def _tuple(nla):
    """Extract (addr, port, l4proto_num) from a CTA_TUPLE_* nested attr."""
    if nla is None:
        return None
    ip = nla.get_attr("CTA_TUPLE_IP")
    proto = nla.get_attr("CTA_TUPLE_PROTO")
    addr = None
    if ip is not None:
        addr = (ip.get_attr("CTA_IP_V4_SRC") or ip.get_attr("CTA_IP_V6_SRC"),
                ip.get_attr("CTA_IP_V4_DST") or ip.get_attr("CTA_IP_V6_DST"))
    l4 = sport = dport = None
    if proto is not None:
        l4 = proto.get_attr("CTA_PROTO_NUM")
        sport = proto.get_attr("CTA_PROTO_SRC_PORT")
        dport = proto.get_attr("CTA_PROTO_DST_PORT")
    return {"addr": addr, "l4": l4, "sport": sport, "dport": dport}


def _flow(msg):
    orig = _tuple(msg.get_attr("CTA_TUPLE_ORIG"))
    reply = _tuple(msg.get_attr("CTA_TUPLE_REPLY"))
    if not orig or not orig["addr"] or not orig["addr"][0]:
        return None

    l4 = orig["l4"]
    proto = L4PROTO.get(l4, str(l4) if l4 is not None else "?")

    state = None
    pi = msg.get_attr("CTA_PROTOINFO")
    if pi is not None:
        tcp = pi.get_attr("CTA_PROTOINFO_TCP")
        if tcp is not None:
            n = tcp.get_attr("CTA_PROTOINFO_TCP_STATE")
            if n is not None and 0 <= n < len(TCP_STATES):
                state = TCP_STATES[n]

    # NAT detected when the reply destination differs from the original source
    nat = bool(reply and reply["addr"] and orig["addr"]
               and reply["addr"][1] != orig["addr"][0])

    return {
        "proto": proto,
        "state": state,
        "src_addr": orig["addr"][0],
        "src_port": orig["sport"],
        "dst_addr": orig["addr"][1],
        "dst_port": orig["dport"],
        "reply_src": reply["addr"][0] if reply and reply["addr"] else None,
        "reply_dst": reply["addr"][1] if reply and reply["addr"] else None,
        "nat": nat,
    }


def main():
    flows = []
    try:
        from pyroute2 import Conntrack
    except Exception:
        json.dump([], sys.stdout)
        return

    seen = set()
    for family in (AF_INET, AF_INET6):
        try:
            ct = Conntrack(nfgen_family=family)
        except TypeError:
            ct = Conntrack()
        try:
            for msg in ct.dump():
                f = _flow(msg)
                if not f:
                    continue
                key = (f["proto"], f["src_addr"], f["src_port"],
                       f["dst_addr"], f["dst_port"])
                if key in seen:
                    continue
                seen.add(key)
                flows.append(f)
        except Exception:
            pass
        finally:
            try:
                ct.close()
            except Exception:
                pass
        # AF_INET dump may already return every family; AF_INET6 pass dedups
        if family == AF_INET and any(":" in (f["src_addr"] or "") for f in flows):
            break

    json.dump(flows, sys.stdout)


if __name__ == "__main__":
    main()
