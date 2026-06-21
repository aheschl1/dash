"""Tool-calling agent over an OpenAI-compatible endpoint.

The LLM runs on the local llama-cpp container (OpenAI-compatible server on
wg-network), not api.openai.com. The core `_run_loop` advances a message list
through tool calls to a final answer; `continue_conversation` drives it for the
multi-turn conversation store, which keeps the list alive between requests (see
store.py).
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
    build_conversation_notes_block,
    build_memory_block,
    build_runtime_context,
    build_system_prompt,
)

# llama-cpp's OpenAI-compatible server, reachable by container name over
# wg-network. The SDK appends /chat/completions to this base. llama.cpp does
# not authenticate, but the SDK requires a non-empty key, so send a placeholder.
LLM_BASE_URL = os.environ.get("AGENT_LLM_BASE_URL", "http://llama-cpp:8080/v1")
LLM_API_KEY = os.environ.get("AGENT_LLM_API_KEY", "no-key")
MODEL = os.environ.get("AGENT_MODEL", "Qwen_Qwen3.5-9B-Q6_K.gguf")
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "6"))

# Qwen3.5 is a reasoning model: llama.cpp's chat template (needs --jinja) routes
# <think>…</think> into a separate `reasoning_content` field. It intermittently
# spends the whole generation there and emits an EMPTY user-facing `content` — or
# "thinks" about a follow-up tool call but never emits it, then stops. Both surface
# as a blank answer. Rather than disable thinking (it improves tool selection), we
# nudge the model off the record to actually emit its answer (see _run_loop).
EMPTY_NUDGE = "(Your last reply was empty. Give your final answer now, in plain text.)"
EMPTY_RETRIES = int(os.environ.get("AGENT_EMPTY_RETRIES", "2"))

client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

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
            "name": "add_conversation_note",
            "description": (
                "Record a finding scoped to the CURRENT conversation only (it never "
                "crosses into other chats) — a tool-result finding, a cause you "
                "pinned down, or an intermediate result worth keeping across turns, "
                "so you don't re-derive it later in this chat. For durable, "
                "machine-wide facts future conversations should also know, use "
                "save_memory instead. Notes appear right after your memories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "The note to record for this conversation."}
                },
                "required": ["note"],
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


def add_conversation_note(conv_id: str | None, note: str) -> str:
    """Persist a note scoped to a single conversation for recall on later turns."""
    note = (note or "").strip()
    if not note:
        return "(nothing to save: empty note)"
    if not conv_id:
        return "(conversation notes unavailable on this path; not saved)"
    try:
        nid = db.conv_note_save(conv_id, note)
    except Exception as e:
        return f"(failed to save note: {e})"
    return f"saved conversation note [{nid}]"


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
    OpenAPI endpoint list) and current memories — and, when `conv_id` is given,
    this conversation's notes after them — appended to the system prompt's content.
    All are injected per-call and never persisted, so resumed chats and chats
    created before a deploy/save still see the current endpoint set and the latest
    memories, and notes added earlier in a conversation stay visible on later turns
    (the stored system prompt deliberately holds none of them — see
    prompt.build_runtime_context / build_memory_block /
    build_conversation_notes_block)."""
    block = build_runtime_context() + "\n\n" + build_memory_block()
    if conv_id:
        block += "\n\n" + build_conversation_notes_block(conv_id)
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
    stream (aborting the HTTP request, so llama.cpp stops generating) and discard
    the partial output, leaving nothing appended to the caller's message list.

    We stream so a long generation can be interrupted, AND — when `on_delta` is
    given — so each content/reasoning fragment can be forwarded live to the UI as
    `answer_delta`/`reasoning_delta` events; the reassembled message is still handed
    back whole, exactly as the blocking call used to return it (the loop and the
    persisted history are unchanged — the deltas are display-only)."""
    stream = client.chat.completions.create(
        model=MODEL,
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

        # Reasoning models (Qwen3.5, Gemma-4) route their chain-of-thought into a
        # separate `reasoning_content` field (llama.cpp --jinja --reasoning). Stream
        # it live so the panel can show the thinking, and attach it to the persisted
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
            elif name == "add_conversation_note":
                output = add_conversation_note(conv_id, args.get("note", ""))
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
