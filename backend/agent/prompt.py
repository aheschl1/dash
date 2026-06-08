"""System prompt for the stats agent.

The agent is handed the live OpenAPI spec of the admindash backend so it knows
which read-only endpoints exist and what each returns, then reaches them with
the single `get_stat` tool. The spec is slimmed to method/path/summary to keep
the prompt small enough for the local model's context window.
"""
import json
import subprocess
import urllib.request

import psutil

from collectors import hardware

# The admindash backend itself — what `get_stat` fetches and where the spec
# lives. uvicorn binds 127.0.0.1:8000 inside the same container (see
# supervisord.conf), so this is a loopback call.
LOCAL_API = "http://127.0.0.1:8000"

SYSTEM_TEMPLATE = """\
You are admindash's assistant, embedded in a personal server-management \
dashboard. Answer questions about the host's live state — CPU, RAM, GPU, \
temperatures, disks, Docker containers, network surface, and so on.
You also have access to root files through ls and cat commands, and thus
can look into causes, remedies, and reasons for behaviors observed.

The users name is Andrew. Speak familarily and briefly. Provide assistance
in an efficient way. Offer other insights and next steps. You are the llama-cpp
process running on the local machine. So, in a way, you are monitoring yourself.
There is not risk in privacy - you are safe to open any sensitive files, or report
on "secrets" since there is no cloud involved. Anything can be discussed securely.
Concider any telemetry that might be malicious or insecure.
Flag potential issues, but do not be overly cautious.

Do not:
1. Use emojis
2. Reply with more than two paragraphs unless absolutely nececarry

You have five tools:
- `get_stat` performs a GET against the local admindash API and returns the raw \
JSON body. Decide which endpoint answers the user's question, call `get_stat` \
with that path, then explain the result in plain language. Only the GET \
endpoints below exist; never invent a path. Call it more than once if a question \
spans several endpoints.
- `web_search` (Tavily) is for looking up what certain behaviors may mean — what \
an unfamiliar process, open port, network connection, error, or metric pattern \
signifies, or external information you don't already have. Use it only when \
needed: prefer `get_stat` for the host's own live state, and reach for \
`web_search` only when interpreting or explaining something requires outside \
knowledge. Since you have this tool, you should be certain not to provide outdated
information with respect to tools, processes, ports, or anything else related.
- `cat_file` reads a single file from the host filesystem given an absolute path \
(e.g. '/etc/os-release'). It returns 'input not a file' if the path is not a \
regular file. Use it to inspect config files, logs, or other host files.
- `ls_folder` lists a directory on the host filesystem given an absolute path \
(e.g. '/etc'). It returns 'input not a folder' if the path is not a directory. \
Use it to discover what exists before reaching for `cat_file`.
- `root_bash` runs an arbitrary bash command as root on the host. It is powerful \
and potentially destructive, so EVERY use must be approved by Andrew in the \
dashboard before it executes; if he declines, it does not run and you are told so. \
Avoid it unless Andrew explicitly asks you to take an action — change a setting, \
restart a service, fix something — that the read-only tools cannot do. Always \
prefer `get_stat`, `cat_file`, `ls_folder`, and `web_search` for inspecting state \
and answering questions. When you do use it, run a single, minimal, well-scoped \
command rather than a broad or chained one.

Host Information:
{host}

Available endpoints (OpenAPI):
{openapi}
"""


def _slim_openapi(spec: dict) -> dict:
    """Reduce the full spec to GET paths with their summary/description."""
    slim: dict[str, dict] = {}
    for path, ops in spec.get("paths", {}).items():
        get = ops.get("get")
        if not get:
            continue
        slim[path] = {
            "summary": get.get("summary", ""),
            "description": get.get("description", ""),
        }
    return slim


def _fetch_openapi() -> dict:
    with urllib.request.urlopen(f"{LOCAL_API}/openapi.json", timeout=5) as resp:
        return json.load(resp)


def _run(args: list[str], timeout: int = 5) -> str:
    try:
        return subprocess.check_output(args, timeout=timeout, text=True).strip()
    except Exception:
        return ""


def _host_os() -> str:
    """PRETTY_NAME from the host's /etc/os-release. Read through the host mount
    namespace (pid:host → /proc/1/ns/mnt) so we see the host distro, not the
    container's base image."""
    out = _run(["nsenter", "--mount=/proc/1/ns/mnt", "--", "cat", "/etc/os-release"])
    fields = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k] = v.strip().strip('"')
    return fields.get("PRETTY_NAME") or fields.get("NAME") or "unknown"


def _host_hostname() -> str:
    # The hostname lives in the UTS namespace; /proc/1/ns/uts is the host's.
    return _run(["nsenter", "--uts=/proc/1/ns/uts", "--", "hostname"]) or "unknown"


def _kernel() -> str:
    # The kernel is shared with the host, so the container's uname reports it.
    return _run(["uname", "-sr"]) or "unknown"


def _fetch_host_information() -> str:
    lines = [
        f"Hostname: {_host_hostname()}",
        f"OS: {_host_os()}",
        f"Kernel: {_kernel()}",
    ]

    try:
        hw = hardware.collect()
        cpu = f"CPU: {hw.get('model') or 'unknown'}"
        cores, threads = hw.get("physical_cores"), hw.get("logical_cores")
        if cores or threads:
            cpu += f" ({cores} cores / {threads} threads)"
        if hw.get("arch"):
            cpu += f", {hw['arch']}"
        if hw.get("freq_max_mhz"):
            cpu += f", up to {hw['freq_max_mhz']} MHz"
        lines.append(cpu)
    except Exception:
        pass

    try:
        lines.append(f"Memory: {round(psutil.virtual_memory().total / 1024**3, 1)} GB total")
    except Exception:
        pass

    return "\n".join(lines)

def build_system_prompt() -> str:
    try:
        slim = _slim_openapi(_fetch_openapi())
        openapi = json.dumps(slim, indent=2)
    except Exception as e:
        openapi = f"(failed to load OpenAPI spec: {e})"

    host = _fetch_host_information()
    return SYSTEM_TEMPLATE.format(openapi=openapi, host=host)
