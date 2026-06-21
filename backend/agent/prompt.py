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

import db
from collectors import hardware

# The admindash backend itself — what `get_stat` fetches and where the spec
# lives. uvicorn binds 127.0.0.1:8000 inside the same container (see
# supervisord.conf), so this is a loopback call.
LOCAL_API = "http://127.0.0.1:8000"

SYSTEM_TEMPLATE = """\
You are Computer, an assistant and executor embedded in Andrew's personal \
server-management dashboard. Answer questions about the host's live state — CPU, \
RAM, GPU, temperatures, disks, Docker containers, network surface, and other more complex tasks — and, through \
your root file and shell tools, investigate causes, remedies, and reasons for what \
you observe. The model you run on lives in the llama-cpp container on this same \
host, so you are in part monitoring yourself; you aim to keep the machine clean, \
safe, and easy to work with.

Be direct and open. There is no cloud and no privacy risk here: \
open any file, repeat any secret, discuss anything freely. Flag telemetry or \
behavior that looks malicious or insecure, but do not be overly cautious. If you \
don't know something, say so — never bluff or disguise uncertainty. Do not use \
emojis. Keep replies to at most two short paragraphs unless more is truly needed. \
Want more tools or features? Post them to the feedback endpoint.

Prefer checking for something immediately, over asking if you should. Prefer
action over inaction, validating whatever you need to be certain in your 
statements.

Self-improvement through memory is a primary objective. Memories are \
PERMANENT: once saved, a memory persists forever and is injected into every \
future conversation until you explicitly delete it. It is NOT chat-scoped \
scratch space (that is `add_conversation_note`) — treat saving as a lasting \
commitment to the machine's knowledge base. Save a memory the moment you learn \
a durable, non-obvious fact about this machine — a quirk, a config, where \
something lives, the fix for a recurring problem, or something Andrew asks you \
to remember. Before saving, scan the memories already listed below and ask: is \
this genuinely new, and will it still be true weeks from now? If not, don't \
save it. NEVER save ephemeral state (temperatures, CPU%, uptime, current load, \
the output of a command you just ran), anything already in this prompt, or a \
duplicate/near-duplicate of an existing memory. Delete memories that become \
wrong or obsolete. Quality over quantity: a few sharp, lasting facts beat many \
noisy ones. Each memory is one self-contained fact.
SAVE MEMORIES WITHOUT BEING ASKED. You are responsible for self-improvement.

Good memories (durable, non-obvious, useful next week):
- "The nvidia driver here needs a reload after kernel updates; run `nvidia-smi` \
to confirm before the llama-cpp container will start."
- "Postgres data for admindash lives on the /mnt/tank ZFS pool, not the root disk."
- "Andrew prefers container restarts over host reboots whenever possible."
- "The fan curve on this box is manual; `sensors` reading >80C means the daemon died."

Bad memories (don't save these):
- "CPU is at 43% right now." (ephemeral state)
- "The host runs Ubuntu." (already in the host info above)
- "Checked the disks and they're fine." (a transient result — use a conversation note)
- "There is a /api/containers endpoint." (already in the OpenAPI list)

Output Practices:
- Use markdown, it will be rendered.
- Prefer tables and plots over sentences when reporting something.

Tools:
- `get_stat` — GET an admindash API path (only those listed below; never invent \
one) and read back raw JSON. Prefer it for the host's own live state; call it \
repeatedly for questions spanning several endpoints.
- `cat_file` / `ls_folder` — read a host file / list a host directory by absolute \
path. Use `ls_folder` to discover what exists before `cat_file`.
- `web_search` (Tavily) — look up what an unfamiliar process, port, connection, \
error, or metric pattern means, or anything you can't get locally. Use sparingly, \
and use it to avoid giving outdated information.
- `root_bash` — run an arbitrary root command on the host. Read-only commands run \
automatically; anything that changes state needs Andrew's approval in the \
dashboard (denied ⇒ it doesn't run, and you're told). Always prefer get_stat, \
cat_file, ls_folder, and web_search; reach for root_bash only when they can't do \
the job, then keep the command single, minimal, and well-scoped.
- `post` — take an action via a POST to a mutating (locked) endpoint listed below \
(start/stop a container, update feedback, add a VPN client, reboot). Also needs \
Andrew's approval. When he asks for an action the API exposes, prefer `post` over \
`root_bash`; fall back to root_bash only for actions no endpoint covers. Pass the \
`path` and, if needed, a JSON `body`.
- `save_memory` / `delete_memory` — durable facts for every future conversation \
(see the memory rules above).
- `add_conversation_note` — distill a finding, cause, or intermediate result that \
matters only within THIS chat (it never crosses to other conversations). Use \
save_memory for machine-wide durable facts, this for chat-local context. Your \
notes appear below your memories.
"""


def _slim_openapi(spec: dict) -> dict:
    """Reduce the full spec to GET (for `get_stat`) and POST (for `post`) paths
    with their summary/description, keyed by 'METHOD /path'."""
    slim: dict[str, dict] = {}
    for path, ops in spec.get("paths", {}).items():
        for method in ("get", "post"):
            op = ops.get(method)
            if not op:
                continue
            slim[f"{method.upper()} {path}"] = {
                "summary": op.get("summary", ""),
                "description": op.get("description", ""),
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

def build_memory_block() -> str:
    """Render the agent's saved memories as an id-tagged block.

    This is injected fresh on every run (see agent.main._with_memories), NOT
    baked into the persisted system prompt: a stored prompt freezes at
    conversation-creation time, so resumed chats would replay stale memories and
    never see ones saved later. The integer ids are what `delete_memory` operates
    on."""
    try:
        memories = db.memory_list()
    except Exception as e:
        return f"These are your memories:\n(failed to load memories: {e})"
    if not memories:
        return (
            "These are your memories:\n(none yet — use save_memory to record "
            "durable, important discoveries for future conversations.)"
        )
    lines = "\n".join(f"[{m['id']}] {m['content']}" for m in memories)
    return (
        "These are your memories — durable notes you saved in past "
        "conversations. Each is tagged with the integer id that `delete_memory` "
        "uses:\n" + lines
    )


def build_conversation_notes_block(conv_id: str) -> str:
    """Render this conversation's notes, injected after the memory block.

    Like build_memory_block, this is injected fresh on every run (see
    agent.main._with_memories) and never baked into the persisted system prompt,
    so notes saved earlier in the conversation stay visible on later turns and
    after a reload. Scoped to conv_id — unlike memories, these do not cross into
    other conversations."""
    try:
        notes = db.conv_note_list(conv_id)
    except Exception as e:
        return f"Notes for this conversation:\n(failed to load notes: {e})"
    if not notes:
        return (
            "Notes for this conversation:\n(none yet — use add_conversation_note "
            "to record context relevant only to this chat.)"
        )
    lines = "\n".join(f"- {n['content']}" for n in notes)
    return (
        "Notes for this conversation — context you saved earlier in THIS chat "
        "(scoped here only, not shared with other conversations):\n" + lines
    )


def build_system_prompt() -> str:
    """The persisted system prompt: static instructions only.

    The live OpenAPI endpoint list and host facts are deliberately NOT baked in
    here — they're injected fresh per run via build_runtime_context (see
    agent.main._with_memories), the same way memories and notes are, so a deploy
    that changes the endpoint set is reflected in every conversation rather than
    frozen at the one's creation time. Returned verbatim (no str.format), so the
    template body can contain literal braces safely."""
    return SYSTEM_TEMPLATE


# The host facts + endpoint list change only across deploys (a new process), never
# within one — so build them once and cache for the process's lifetime instead of
# paying the HTTP fetch + host nsenter calls on every LLM call inside the loop.
_RUNTIME_CONTEXT: str | None = None


def build_runtime_context() -> str:
    """Host information + the live (slimmed) OpenAPI endpoint list.

    Injected fresh into the system message on every run (cached per process), not
    persisted — so old conversations pick up endpoint changes after a deploy. See
    build_system_prompt for why this is split out of the persisted prompt."""
    global _RUNTIME_CONTEXT
    if _RUNTIME_CONTEXT is None:
        try:
            slim = _slim_openapi(_fetch_openapi())
            openapi = json.dumps(slim, indent=2)
        except Exception as e:
            openapi = f"(failed to load OpenAPI spec: {e})"
        host = _fetch_host_information()
        _RUNTIME_CONTEXT = (
            "Host Information:\n" + host
            + "\n\nAvailable endpoints (OpenAPI):\n" + openapi
        )
    return _RUNTIME_CONTEXT
