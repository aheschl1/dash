"""Tool-calling agent over an OpenAI-compatible endpoint.

The endpoint/model are resolved per request (see resolve_llm_config). The core
`_run_loop` advances a message list through tool calls to a final answer;
`continue_conversation` drives it for the multi-turn conversation store, which
keeps the list alive between requests (see store.py).
"""
import json
import os
import re
import subprocess
import urllib.request

from openai import OpenAI

import db
from .approval import ApprovalRequest
from .cancel import CancelToken, TurnCanceled
from .safe_commands import is_safe_command
from .prompt import (
    LOCAL_API,
    build_memory_block,
    build_runtime_context,
    build_scope_block,
    build_system_prompt,
)

# OpenAI-compatible base URL; the SDK appends /chat/completions to it. The SDK
# requires a non-empty key even when the endpoint doesn't authenticate, so a
# placeholder is sent by default. These env values are only the DEFAULTS: the
# live endpoint/model are resolved per request from DB → env → default (see
# resolve_llm_config), so an admin can point the agent at a different machine's
# LLM at runtime with no redeploy.
DEFAULT_LLM_BASE_URL = os.environ.get("AGENT_LLM_BASE_URL", "http://llama-cpp:8080/v1")
DEFAULT_LLM_API_KEY = os.environ.get("AGENT_LLM_API_KEY", "no-key")
DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "Qwen_Qwen3.5-9B-Q6_K.gguf")
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "6"))

# Keys under which the runtime LLM overrides live in the app_config table.
_CFG_BASE_URL = "agent_llm_base_url"
_CFG_API_KEY = "agent_llm_api_key"
_CFG_MODEL = "agent_model"

# Reasoning models route <think>…</think> into a separate `reasoning_content`
# field. One intermittently spends the whole generation there and emits an EMPTY
# user-facing `content` — or "thinks" about a follow-up tool call but never emits
# it, then stops. Both surface as a blank answer. Rather than disable thinking (it
# improves tool selection), we nudge the model off the record to actually emit its
# answer (see _run_loop).
EMPTY_NUDGE = "(Your last reply was empty. Give your final answer now, in plain text.)"
EMPTY_RETRIES = int(os.environ.get("AGENT_EMPTY_RETRIES", "2"))

# OpenAI SDK clients are cached per (base_url, api_key) so we don't rebuild one
# on every completion, but a runtime endpoint change is picked up automatically
# (the new tuple misses the cache). Constructing a client does no network I/O.
_client_cache: dict[tuple[str, str], OpenAI] = {}


def resolve_llm_config() -> dict[str, str]:
    """Resolve the live LLM endpoint/key/model: DB override → env → default.

    Read fresh per request so an admin editing the config (PUT /api/agent/config)
    takes effect on the next turn without a redeploy or restart."""
    cfg = db.get_app_config((_CFG_BASE_URL, _CFG_API_KEY, _CFG_MODEL))
    return {
        "base_url": cfg.get(_CFG_BASE_URL) or DEFAULT_LLM_BASE_URL,
        "api_key": cfg.get(_CFG_API_KEY) or DEFAULT_LLM_API_KEY,
        "model": cfg.get(_CFG_MODEL) or DEFAULT_MODEL,
    }


def _client_for(base_url: str, api_key: str) -> OpenAI:
    key = (base_url, api_key)
    c = _client_cache.get(key)
    if c is None:
        c = OpenAI(base_url=base_url, api_key=api_key)
        _client_cache[key] = c
    return c

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stat",
            "description": (
                "Fetch a metric from the admindash API by issuing a GET to the "
                "given path. Use only paths listed in the OpenAPI spec, e.g. "
                "'/api/stats' or '/api/gpu'. Returns the raw JSON response body."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "API path starting with '/api/'.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web via Tavily to interpret what a behavior, process, "
                "port, connection, error, or metric pattern means, or to fetch "
                "external information not available from get_stat. Use only when needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cat_file",
            "description": (
                "Read a single file from the HOST filesystem and return its "
                "contents. Pass an absolute host path, e.g. '/etc/os-release'. "
                "Returns 'input not a file' if the path is not a regular file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute host file path."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ls_folder",
            "description": (
                "List the contents of a directory on the HOST filesystem. Pass an "
                "absolute host path, e.g. '/etc' or '/home/andrew'. Returns "
                "'input not a folder' if the path is not a directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute host directory path."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "root_bash",
            "description": (
                "Run an arbitrary bash command as ROOT on the host. Most "
                "non-mutating (read-only) commands pass automatically and run "
                "without a prompt; anything that could change state must be "
                "approved by the user in the dashboard before it runs, and if "
                "they decline it does not execute. Prefer the read-only tools (get_stat, cat_file, "
                "ls_folder, web_search) for inspection and answering questions — "
                "only reach for root_bash when the other tools are insufficient."
                "Returns the command's exit "
                "code and combined stdout/stderr, or a note if it was denied."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to run on the host."}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "post",
            "description": (
                "Take an ACTION by issuing a POST to a mutating admindash API "
                "endpoint (the locked ones, e.g. '/api/containers/{name}/action', "
                "'/api/host/reboot', '/api/feedback/{id}/status'). Like root_bash, "
                "EVERY invocation must be approved by the user in the dashboard "
                "before it runs; if they decline it does not execute. Prefer this "
                "over root_bash whenever the API already exposes the action — it is "
                "structured and safer. Pass the API `path` and an optional JSON "
                "`body`. Returns the raw JSON response body, or a note if it was "
                "denied."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "API path starting with '/api/'.",
                    },
                    "body": {
                        "type": "object",
                        "description": "JSON request body (omit if the endpoint takes none).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "Persist one durable, non-obvious fact about this machine, injected "
                "into your system prompt in EVERY future conversation — a host quirk, "
                "a non-obvious config, where something lives, the fix for a recurring "
                "problem, or a fact Andrew asks you to remember. One self-contained "
                "fact per call. First scan the memories already in your prompt: do "
                "NOT save ephemeral state (temperatures, CPU%, uptime), anything "
                "already in the prompt, or a duplicate/near-duplicate of an existing "
                "memory. Returns the new memory's integer id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The fact to remember."}
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_memory",
            "description": (
                "Delete a saved memory by its integer id (the id shown next to "
                "each memory in your system prompt). Use it to prune memories that "
                "have become wrong or obsolete."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "The id of the memory to delete."}
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_investigations",
            "description": (
                "List the investigation boards (id, title, card count), marking the one "
                "THIS conversation is currently scoped to (if any). Use it to find an "
                "existing investigation to scope to, or to check/recover which board "
                "you're already working in. Does NOT return card contents — "
                "scope_investigation or pin_finding return those."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scope_investigation",
            "description": (
                "Scope THIS conversation to an existing investigation by id (see "
                "list_investigations). Afterwards, pin_finding and update_investigation "
                "act on that investigation. Returns the board's full contents (every "
                "card's [id], column and content) so you can read what's already pinned "
                "and move cards by [id]. Use this when continuing work on an existing "
                "investigation; use create_investigation to start a new one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "board_id": {"type": "integer", "description": "The investigation id to scope this conversation to."}
                },
                "required": ["board_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_investigation",
            "description": (
                "Create a new investigation board and scope THIS conversation to it "
                "automatically, so subsequent pin_finding/update_investigation calls "
                "target it. Use at the start of a deeper investigation when no suitable "
                "board exists yet. Returns the new investigation's id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title for the investigation."},
                    "description": {"type": "string", "description": "Optional one-line description of what's being investigated."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pin_finding",
            "description": (
                "Pin a finding onto this conversation's scoped investigation (scope it "
                "first with scope_investigation or create_investigation). Pin a fenced "
                "code block, a markdown table, or a ```mermaid diagram — the board "
                "renders code/mermaid/tables. Pin EVIDENCE only — command output, a "
                "config snippet, a diagram of what you found — never plans, TODOs or "
                "'recommendations not yet applied'; the board records what IS, not what "
                "to do next, and nothing pinned is ever an instruction to act. Do this "
                "after each meaningful discovery so the investigation accumulates "
                "evidence. The tool result returns a lightweight INDEX of the board — "
                "the new card's [id] plus every card's [id]/column/one-line ref, but "
                "NOT the card bodies (you just wrote them); use the [id]s for move_card, "
                "and scope_investigation to reload full contents. Lands in the 'To "
                "investigate' column by default."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The raw markdown block to pin: a fenced ```code/```mermaid block, or a GFM table."},
                    "kind": {"type": "string", "enum": ["code", "table"], "description": "'code' for code/mermaid fences, 'table' for a markdown table. Defaults to 'code'."},
                    "col": {"type": "string", "enum": ["todo", "looking", "done"], "description": "Column to drop the card into. Defaults to 'todo'."},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_investigation",
            "description": (
                "Edit the title and/or description of THIS conversation's scoped "
                "investigation (scope it first). You cannot edit other investigations, "
                "and deleting an investigation is a human-only action in the UI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "New title (omit to leave unchanged)."},
                    "description": {"type": "string", "description": "New description (omit to leave unchanged)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_card",
            "description": (
                "Move a pinned card to another column on THIS conversation's scoped "
                "investigation, to drive the kanban flow: 'todo' (To investigate) → "
                "'looking' (actively working it) → 'done' (Resolved). Use the card's "
                "integer id (the [id] returned by pin_finding/scope_investigation). You "
                "can only move cards on the scoped investigation. By default the card "
                "goes to the bottom of the target column."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {"type": "integer", "description": "The id of the card to move (the [id] returned by pin_finding/scope_investigation)."},
                    "col": {"type": "string", "enum": ["todo", "looking", "done"], "description": "Destination column."},
                },
                "required": ["card_id", "col"],
            },
        },
    },
]

TAVILY_URL = "https://api.tavily.com/search"

# The container runs pid:host, so /proc/1/ns/mnt is the host mount namespace.
# Entering it lets cat/ls see the real host filesystem (as root), the same way
# the collectors reach host state.
HOST_NSENTER = ["nsenter", "--mount=/proc/1/ns/mnt", "--"]
MAX_FILE_OUTPUT = 20000

# root_bash needs a full host root shell, not just the mount namespace: enter all
# of PID 1's namespaces (mount/uts/ipc/net/pid) so the command sees the host the
# same way a root login would. pid:host means PID 1 is the host's init.
HOST_ROOT_NSENTER = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"]
BASH_TIMEOUT = int(os.environ.get("AGENT_BASH_TIMEOUT", "60"))

# Two independent layers keep a model-supplied path from doing anything but name
# a file/dir:
#   1. We never invoke a shell — the path is one argv element to nsenter/cat/ls,
#      so ';', '|', '&&', '$()', backticks etc. are literal bytes, not syntax.
#   2. _validate_host_path is a strict ALLOWLIST: an absolute path made only of
#      ordinary path characters. Anything with a shell metacharacter, whitespace,
#      newline, or NUL is rejected before it reaches subprocess at all. So
#      "/etc/whatever; rm -rf /" fails validation outright (the ';' and space are
#      not in the allowlist) and never runs.
# A denylist ("strip out bad chars") would be the wrong call here — easy to miss
# a case. The allowlist can only ever permit the characters we explicitly chose.
_HOST_PATH_RE = re.compile(r"^/[A-Za-z0-9._/+@:,\-]*$")


def _validate_host_path(path: str) -> str | None:
    """Return a sanitized absolute host path, or None if it isn't one we'll touch."""
    path = (path or "").strip()
    if not path or len(path) > 4096:
        return None
    if not _HOST_PATH_RE.fullmatch(path):
        return None
    return path


def cat_file(path: str) -> str:
    """`cat` a host file, but only after validating the path AND confirming it IS
    a regular file on the host."""
    path = _validate_host_path(path)
    if path is None:
        return "input not a file"
    check = subprocess.run(HOST_NSENTER + ["test", "-f", path], capture_output=True, timeout=5)
    if check.returncode != 0:
        return "input not a file"
    result = subprocess.run(
        HOST_NSENTER + ["cat", path],
        capture_output=True, text=True, timeout=10,
    )
    out = result.stdout or result.stderr or "(empty file)"
    if len(out) > MAX_FILE_OUTPUT:
        out = out[:MAX_FILE_OUTPUT] + "\n...(truncated)"
    return out


def ls_folder(path: str) -> str:
    """`ls -la` a host directory, but only after validating the path AND confirming
    it IS a directory on the host."""
    path = _validate_host_path(path)
    if path is None:
        return "input not a folder"
    check = subprocess.run(HOST_NSENTER + ["test", "-d", path], capture_output=True, timeout=5)
    if check.returncode != 0:
        return "input not a folder"
    result = subprocess.run(
        HOST_NSENTER + ["ls", "-la", path],
        capture_output=True, text=True, timeout=10,
    )
    out = result.stdout or result.stderr or "(empty folder)"
    if len(out) > MAX_FILE_OUTPUT:
        out = out[:MAX_FILE_OUTPUT] + "\n...(truncated)"
    return out


def root_bash(command: str) -> str:
    """Run an arbitrary command as root in the host's namespaces.

    Gated by user approval at the call site (see _run_loop) — this function
    assumes approval already happened and does NOT validate the command. We pass
    it to `bash -c` on purpose: arbitrary shell is the whole point of the tool,
    which is why it can only run behind an explicit per-command approval."""
    try:
        result = subprocess.run(
            HOST_ROOT_NSENTER + ["bash", "-c", command],
            capture_output=True, text=True, timeout=BASH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"(command timed out after {BASH_TIMEOUT}s)"
    except Exception as e:
        return f"(failed to run: {e})"
    out = ((result.stdout or "") + (result.stderr or "")).strip() or "(no output)"
    if len(out) > MAX_FILE_OUTPUT:
        out = out[:MAX_FILE_OUTPUT] + "\n...(truncated)"
    return f"exit {result.returncode}\n{out}"


def save_memory(content: str) -> str:
    """Persist a free-text memory for recall in future conversations."""
    content = (content or "").strip()
    if not content:
        return "(nothing to save: empty memory)"
    try:
        mid = db.memory_save(content)
    except Exception as e:
        return f"(failed to save memory: {e})"
    return f"saved memory [{mid}]"


def delete_memory(id) -> str:
    """Delete a saved memory by id."""
    try:
        mid = int(id)
    except (TypeError, ValueError):
        return f"(invalid memory id: {id!r})"
    try:
        deleted = db.memory_delete(mid)
    except Exception as e:
        return f"(failed to delete memory: {e})"
    return f"deleted memory [{mid}]" if deleted else f"(no memory with id {mid})"


# ── Investigation tools (scope-enforced) ─────────────────────────────────────
# pin/edit always act on the CURRENT conversation's scoped investigation, looked
# up via conv_id — so the model can never touch an investigation it isn't scoped
# to. scope_investigation / create_investigation are the only ways to set scope.

_BOARD_COL_LABELS = {"todo": "To investigate", "looking": "Looking", "done": "Resolved"}
_CARD_MAX = 2000


def _board_snapshot(board_id: int) -> str:
    """Render every card currently on `board_id` (id, column, content).

    This is the agent's *working view* of the investigation. It used to be injected
    into the system prompt on every step; instead it now rides back on the
    pin/scope/move tool results, so the model reads its evidence (and the [id]s it
    needs for move_card) on demand without bloating — and re-rendering — the system
    prompt every turn."""
    try:
        cards = db.board_list(board_id)
    except Exception as e:
        return f"(failed to read board: {e})"
    if not cards:
        return "Board contents: (empty — nothing pinned yet)."
    parts = ["Board contents — every card pinned here (move_card by [id]):"]
    for c in cards:
        body = c["content"]
        if len(body) > _CARD_MAX:
            body = body[:_CARD_MAX] + "\n… (truncated)"
        label = _BOARD_COL_LABELS.get(c["col"], c["col"])
        parts.append(f"[{c['id']}] ({label})\n{body}")
    return "\n\n".join(parts)


def _board_index(board_id: int) -> str:
    """A lightweight listing of the board — [id], column and a one-line ref per
    card, WITHOUT the card bodies. Rides back on pin_finding (not the full
    _board_snapshot) so the model keeps the [id]s it needs for move_card and stays
    oriented on the layout, but the pinned content is NOT re-injected on every pin:
    it just emitted that content, and re-echoing cards verbatim — especially ones
    phrased as recommendations/TODOs — invites the model to read its own notes as
    instructions and act on them. Full bodies reload on demand via
    scope_investigation."""
    try:
        cards = db.board_list(board_id)
    except Exception as e:
        return f"(failed to read board: {e})"
    if not cards:
        return "Board is empty (nothing pinned yet)."
    lines = ["Board now holds (move_card by [id]; scope_investigation to re-read full card contents):"]
    for c in cards:
        first = next((ln.strip() for ln in c["content"].splitlines() if ln.strip()), "")
        if len(first) > 80:
            first = first[:80] + "…"
        label = _BOARD_COL_LABELS.get(c["col"], c["col"])
        lines.append(f"[{c['id']}] ({label}) {first}")
    return "\n".join(lines)


def list_investigations(conv_id: str | None = None) -> str:
    try:
        boards = db.boards_list()
    except Exception as e:
        return f"(failed to list investigations: {e})"
    if not boards:
        return "(no investigations yet — create_investigation to start one)"
    # Mark the board THIS conversation is scoped to, so the model can recover its
    # scope without the board being injected into the prompt every turn.
    scoped = db.chat_get_board(conv_id) if conv_id else None
    lines = []
    for b in boards:
        line = (f"[{b['id']}] {b['title']} — {b['card_count']} card(s)"
                + (f" — {b['description']}" if b.get("description") else ""))
        if b["id"] == scoped:
            line += "  ← this conversation is scoped here"
        lines.append(line)
    if scoped is None:
        lines.append("(this conversation is not scoped to any investigation yet)")
    return "\n".join(lines)


def scope_investigation(conv_id: str | None, board_id) -> str:
    if not conv_id:
        return "(scoping unavailable on this path; not set)"
    try:
        bid = int(board_id)
    except (TypeError, ValueError):
        return f"(invalid investigation id: {board_id!r})"
    if not db.board_exists(bid):
        return f"(no investigation with id {bid}; list_investigations to see them)"
    db.chat_set_board(conv_id, bid)
    return (
        f"this conversation is now scoped to investigation [{bid}].\n\n"
        + _board_snapshot(bid)
    )


def create_investigation(conv_id: str | None, title: str, description: str | None = None) -> str:
    title = (title or "").strip()
    if not title:
        return "(nothing created: empty title)"
    try:
        board = db.board_create(title, (description or "").strip() or None)
    except Exception as e:
        return f"(failed to create investigation: {e})"
    if conv_id:
        db.chat_set_board(conv_id, board["id"])
        return (
            f"created investigation [{board['id']}] '{board['title']}' and scoped "
            f"this conversation to it. The board starts empty — pin_finding adds "
            f"evidence (and returns the board's index of card [id]s)."
        )
    return f"created investigation [{board['id']}] '{board['title']}'"


def pin_finding(conv_id: str | None, content: str, kind: str = "code", col: str = "todo") -> str:
    content = (content or "").strip()
    if not content:
        return "(nothing to pin: empty content)"
    if kind not in ("code", "table"):
        kind = "code"
    if col not in ("todo", "looking", "done"):
        col = "todo"
    board_id = db.chat_get_board(conv_id) if conv_id else None
    if board_id is None:
        return ("(this conversation isn't scoped to an investigation — call "
                "scope_investigation or create_investigation first, then pin)")
    try:
        card = db.board_add(kind, content, conv_id, None, col=col, board_id=board_id)
    except Exception as e:
        return f"(failed to pin: {e})"
    return (
        f"pinned card [{card['id']}] to investigation [{board_id}] in column "
        f"'{col}'.\n\n" + _board_index(board_id)
    )


def update_investigation(conv_id: str | None, title: str | None = None,
                         description: str | None = None) -> str:
    board_id = db.chat_get_board(conv_id) if conv_id else None
    if board_id is None:
        return ("(this conversation isn't scoped to an investigation — scope it "
                "first; you can only edit the scoped one)")
    title = title.strip() if isinstance(title, str) else None
    description = description.strip() if isinstance(description, str) else None
    if not title and description is None:
        return "(nothing to update: provide a title and/or description)"
    board = db.board_update(board_id, title=title or None, description=description)
    if board is None:
        return f"(investigation [{board_id}] no longer exists)"
    return f"updated investigation [{board_id}]"


def move_card(conv_id: str | None, card_id, col: str) -> str:
    board_id = db.chat_get_board(conv_id) if conv_id else None
    if board_id is None:
        return ("(this conversation isn't scoped to an investigation — scope it "
                "first; you can only move cards on the scoped one)")
    try:
        cid = int(card_id)
    except (TypeError, ValueError):
        return f"(invalid card id: {card_id!r})"
    if col not in _BOARD_COL_LABELS:
        return f"(invalid column: {col!r}; use todo, looking, or done)"
    cards = db.board_list(board_id)
    if not any(c["id"] == cid for c in cards):
        return (f"(card [{cid}] isn't on this conversation's scoped investigation "
                f"[{board_id}]; you can only move cards pinned there)")
    # Append to the bottom of the destination column (skip the card being moved).
    others = [c["pos"] for c in cards if c["col"] == col and c["id"] != cid]
    pos = (max(others) + 1) if others else 1.0
    if not db.board_move(cid, col, pos):
        return f"(card [{cid}] no longer exists)"
    return f"moved card [{cid}] to {_BOARD_COL_LABELS[col]}"


def get_stat(path: str) -> str:
    """Curl a GET endpoint on the local admindash API and return the body."""
    if not path.startswith("/"):
        path = "/" + path
    url = f"{LOCAL_API}{path}"
    result = subprocess.run(
        ["curl", "-s", "--max-time", "10", url],
        capture_output=True,
        text=True,
    )
    return result.stdout or result.stderr or "(empty response)"


def post_stat(path: str, body, token: str | None) -> str:
    """POST to a mutating endpoint on the local admindash API and return the body.

    Authenticates with the admin token the user supplied when approving (the 🔒
    endpoints re-verify it server-side). Only loopback `/api/` paths are allowed —
    a model-supplied path can't be turned into an arbitrary URL. Approval at the
    call site (see _run_loop) is the real guard; this is defense in depth."""
    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith("/api/"):
        return "(post refused: path must start with /api/)"
    url = f"{LOCAL_API}{path}"
    args = ["curl", "-s", "--max-time", "15", "-X", "POST", url,
            "-H", "Content-Type: application/json"]
    if token:
        args += ["-H", f"Authorization: Bearer {token}"]
    if body is not None:
        args += ["-d", json.dumps(body)]
    result = subprocess.run(args, capture_output=True, text=True)
    return result.stdout or result.stderr or "(empty response)"


def web_search(query: str) -> str:
    """Tavily web search → synthesized answer + top results (or an error string).

    The key comes from the TAVILY_API_KEY env var, loaded from admindash's .env
    via the compose env_file — never hardcoded. Returns a plain-text summary the
    model can read directly.
    """
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return "(web search unavailable: TAVILY_API_KEY not set)"
    if not query.strip():
        return "(web search needs a query)"
    payload = json.dumps({
        "query": query,
        "max_results": 5,
        "search_depth": "basic",
        "include_answer": True,
    }).encode()
    req = urllib.request.Request(
        TAVILY_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except Exception as e:
        return f"(web search failed: {e})"
    parts = []
    if data.get("answer"):
        parts.append("Answer: " + data["answer"])
    for r in data.get("results", [])[:5]:
        snippet = (r.get("content") or "")[:300]
        parts.append(f"- {r.get('title', '')} ({r.get('url', '')})\n  {snippet}")
    return "\n".join(parts) or "(no results)"


def _with_memories(messages: list[dict], conv_id: str | None = None) -> list[dict]:
    """Return a copy of `messages` with the live runtime context (host facts +
    OpenAPI endpoint list) and current memories appended to the system prompt's
    content. Both are injected per-call and never persisted, so resumed chats and
    chats created before a deploy/save still see the current endpoint set and the
    latest memories (the stored system prompt deliberately holds none of them — see
    prompt.build_runtime_context / build_memory_block).

    The scoped investigation board is deliberately NOT injected here: re-rendering
    it into the system prompt every step cost a DB read per turn and, worse, changed
    the system message on every pin (defeating prompt caching). Instead the agent
    reads its board from the pin_finding/scope_investigation/move_card tool results,
    which return the full board snapshot — so the evidence reaches its context only
    when it acts on the board, not on every unrelated step. A *user*-set scope is
    the exception: the model can't see a scope it didn't set itself, so a one-line
    note (build_scope_block) is injected here when the user scoped this conversation
    by hand — it changes only when the scope does, not on every pin, so it doesn't
    churn the cached prompt the way the full board would."""
    block = build_runtime_context() + "\n\n" + build_memory_block()
    scope = build_scope_block(conv_id)
    if scope:
        block += "\n\n" + scope
    out = list(messages)
    if out and out[0].get("role") == "system":
        out[0] = {**out[0], "content": out[0]["content"] + "\n\n" + block}
    else:
        out = [{"role": "system", "content": block}] + out
    return out


def _strip_reasoning(messages: list[dict]) -> list[dict]:
    """Drop our persisted `reasoning` key before sending to the LLM.

    We attach the model's `reasoning_content` to assistant turns so the UI can
    show the thinking, but it's a display-only field — feeding it back as part of
    an assistant message is non-standard and pointlessly inflates context, so each
    message is shallow-copied without it on the way out."""
    return [
        {k: v for k, v in m.items() if k != "reasoning"} if "reasoning" in m else m
        for m in messages
    ]


class _StreamedFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _StreamedToolCall:
    def __init__(self, id: str, name: str, arguments: str):
        self.id = id
        self.type = "function"
        self.function = _StreamedFunction(name, arguments)


class _StreamedMessage:
    """Duck-types the non-streaming `resp.choices[0].message` the loop consumes
    (`.content`, `.reasoning_content`, `.tool_calls`) so `_run_loop` is agnostic to
    how the message was produced."""

    def __init__(self, content: str, reasoning: str, tool_calls: list):
        self.content = content
        self.reasoning_content = reasoning
        self.tool_calls = tool_calls or None


def _stream_completion(messages: list[dict], cancel: "CancelToken | None", on_delta=None) -> "_StreamedMessage | None":
    """Run one chat completion in streaming mode, reassembling the deltas into a
    single message. Returns None if `cancel` is tripped mid-stream — we close the
    stream (aborting the HTTP request, so the server stops generating) and discard
    the partial output, leaving nothing appended to the caller's message list.

    We stream so a long generation can be interrupted, AND — when `on_delta` is
    given — so each content/reasoning fragment can be forwarded live to the UI as
    `answer_delta`/`reasoning_delta` events; the reassembled message is still handed
    back whole, exactly as the blocking call used to return it (the loop and the
    persisted history are unchanged — the deltas are display-only)."""
    cfg = resolve_llm_config()
    stream = _client_for(cfg["base_url"], cfg["api_key"]).chat.completions.create(
        model=cfg["model"],
        messages=messages,
        tools=TOOLS,
        stream=True,
    )
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    # tool_calls arrive across deltas keyed by index; id/name land once, arguments
    # accumulate as a JSON string fragment by fragment.
    tool_calls: dict[int, dict] = {}
    try:
        for chunk in stream:
            if cancel is not None and cancel.is_set():
                return None
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning_parts.append(rc)
                if on_delta:
                    on_delta({"type": "reasoning_delta", "content": rc})
            if delta.content:
                content_parts.append(delta.content)
                if on_delta:
                    on_delta({"type": "answer_delta", "content": delta.content})
            for tcd in (delta.tool_calls or []):
                slot = tool_calls.setdefault(tcd.index, {"id": "", "name": "", "arguments": ""})
                if tcd.id:
                    slot["id"] = tcd.id
                fn = getattr(tcd, "function", None)
                if fn is not None:
                    if fn.name:
                        slot["name"] = fn.name
                    if fn.arguments:
                        slot["arguments"] += fn.arguments
    finally:
        stream.close()

    assembled = [
        _StreamedToolCall(tc["id"], tc["name"], tc["arguments"])
        for _, tc in sorted(tool_calls.items())
    ]
    return _StreamedMessage("".join(content_parts), "".join(reasoning_parts).strip(), assembled)


def _run_loop(messages: list[dict], on_event=None, request_approval=None, conv_id=None, cancel=None) -> str:
    """Drive `messages` through tool calls to a final answer.

    Appends assistant and tool messages in place so callers that retain the
    list (conversations) accumulate full history, then returns the answer text.
    If `on_event` is given, it is called with a `{"type": "tool_call", ...}` dict
    each time a tool is dispatched (used to stream activity over the WebSocket).
    If `request_approval` is given, it is a blocking callback
    `(ApprovalRequest) -> ApprovalResult` consulted before any gated tool
    (`root_bash`, `post`) runs; when it's absent or returns a non-approved result,
    the action is refused and never executed. The result also carries the admin
    token the user supplied, which `post` reuses to authenticate its call.
    """
    # Ephemeral retry prompts: sent to the model when it returns an empty answer,
    # but never written into `messages`, so the persisted/rendered history stays
    # clean (no synthetic user turns leak into the UI or back into the model's
    # durable context).
    nudges: list[dict] = []
    empty_retries = 0

    for _ in range(MAX_STEPS):
        # Stop before starting the next step. The message list ends on a clean
        # boundary here (user turn or a complete set of tool results), so unwinding
        # now leaves it valid for the next turn — see TurnCanceled.
        if cancel is not None and cancel.is_set():
            raise TurnCanceled()
        msg = _stream_completion(
            _strip_reasoning(_with_memories(messages, conv_id) + nudges), cancel, on_event
        )
        # Cancelled mid-generation: the stream was aborted and nothing appended.
        if msg is None:
            raise TurnCanceled()

        # Reasoning models route their chain-of-thought into a separate
        # `reasoning_content` field. Stream it live so the panel can show the
        # thinking, and attach it to the persisted
        # assistant turn below so a reloaded chat shows it too. It is stripped back
        # out before the next LLM call (see _strip_reasoning) so it never re-enters
        # the model's context.
        reasoning = (getattr(msg, "reasoning_content", None) or "").strip()
        if reasoning and on_event:
            on_event({"type": "reasoning", "content": reasoning})

        if not msg.tool_calls:
            answer = (msg.content or "").strip()
            if not answer and empty_retries < EMPTY_RETRIES:
                # Thinking model burned the turn in its reasoning channel and
                # returned empty content. Nudge it (off the record) to actually
                # emit the answer; don't persist the blank turn.
                if not nudges:
                    nudges = [{"role": "user", "content": EMPTY_NUDGE}]
                empty_retries += 1
                continue
            final = {"role": "assistant", "content": answer}
            if reasoning:
                final["reasoning"] = reasoning
            messages.append(final)
            return answer

        # A real tool call arrived: drop any pending nudge so it can't sit in
        # front of the fresh tool results on the next call, and reset the counter.
        nudges = []
        empty_retries = 0
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                **({"reasoning": reasoning} if reasoning else {}),
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if on_event:
                on_event({"type": "tool_call", "name": tc.function.name, "args": args})
            name = tc.function.name
            if name == "get_stat":
                output = get_stat(args.get("path", ""))
            elif name == "web_search":
                output = web_search(args.get("query", ""))
            elif name == "cat_file":
                output = cat_file(args.get("path", ""))
            elif name == "ls_folder":
                output = ls_folder(args.get("path", ""))
            elif name == "root_bash":
                command = args.get("command", "")
                if not command.strip():
                    output = "(no command provided)"
                elif is_safe_command(command):
                    # Provably read-only — auto-approved, no prompt (see safe_commands).
                    output = root_bash(command)
                elif request_approval is None:
                    output = "(root_bash requires interactive approval, unavailable on this path; not executed)"
                elif request_approval(
                    ApprovalRequest(kind="bash", summary=command, detail=command)
                ).approved:
                    output = root_bash(command)
                else:
                    output = "(command was not approved by the user; not executed)"
            elif name == "post":
                path = args.get("path", "")
                body = args.get("body")
                if not path.strip():
                    output = "(no path provided)"
                elif request_approval is None:
                    output = "(post requires interactive approval, unavailable on this path; not executed)"
                else:
                    detail = f"POST {path}"
                    if body is not None:
                        detail += "\n" + json.dumps(body, indent=2)
                    result = request_approval(
                        ApprovalRequest(kind="post", summary=f"POST {path}", detail=detail)
                    )
                    if result.approved:
                        output = post_stat(path, body, result.token)
                    else:
                        output = "(post was not approved by the user; not executed)"
            elif name == "save_memory":
                output = save_memory(args.get("content", ""))
            elif name == "delete_memory":
                output = delete_memory(args.get("id"))
            elif name == "list_investigations":
                output = list_investigations(conv_id)
            elif name == "scope_investigation":
                output = scope_investigation(conv_id, args.get("board_id"))
            elif name == "create_investigation":
                output = create_investigation(conv_id, args.get("title", ""), args.get("description"))
            elif name == "pin_finding":
                output = pin_finding(conv_id, args.get("content", ""),
                                     args.get("kind", "code"), args.get("col", "todo"))
            elif name == "update_investigation":
                output = update_investigation(conv_id, args.get("title"), args.get("description"))
            elif name == "move_card":
                output = move_card(conv_id, args.get("card_id"), args.get("col", ""))
            else:
                output = f"(unknown tool: {name})"
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": output}
            )

    return "Reached the step limit before producing a final answer."


def new_messages() -> list[dict]:
    """Seed a fresh conversation with just the system prompt."""
    return [{"role": "system", "content": build_system_prompt()}]


def continue_conversation(messages: list[dict], query: str, on_event=None, request_approval=None, conv_id=None, cancel=None) -> str:
    """Append a user turn to an existing conversation and run to an answer."""
    messages.append({"role": "user", "content": query})
    return _run_loop(messages, on_event, request_approval, conv_id, cancel)
