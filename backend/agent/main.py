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

from .prompt import LOCAL_API, build_system_prompt

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
                "Run an arbitrary bash command as ROOT on the host. Powerful and "
                "potentially destructive, so EVERY invocation must be approved by "
                "the user in the dashboard before it runs; if they decline it does "
                "not execute. Prefer the read-only tools (get_stat, cat_file, "
                "ls_folder, web_search) for inspection and answering questions — "
                "only reach for root_bash when the user explicitly asks you to take "
                "an action those tools cannot perform. Returns the command's exit "
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


def _run_loop(messages: list[dict], on_event=None, request_approval=None) -> str:
    """Drive `messages` through tool calls to a final answer.

    Appends assistant and tool messages in place so callers that retain the
    list (conversations) accumulate full history, then returns the answer text.
    If `on_event` is given, it is called with a `{"type": "tool_call", ...}` dict
    each time a tool is dispatched (used to stream activity over the WebSocket).
    If `request_approval` is given, it is a blocking callback `(command) -> bool`
    consulted before any `root_bash` command runs; when it's absent or returns
    False, the command is refused and never executed.
    """
    # Ephemeral retry prompts: sent to the model when it returns an empty answer,
    # but never written into `messages`, so the persisted/rendered history stays
    # clean (no synthetic user turns leak into the UI or back into the model's
    # durable context).
    nudges: list[dict] = []
    empty_retries = 0

    for _ in range(MAX_STEPS):
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages + nudges,
            tools=TOOLS,
        )
        msg = resp.choices[0].message

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
            messages.append({"role": "assistant", "content": answer})
            return answer

        # A real tool call arrived: drop any pending nudge so it can't sit in
        # front of the fresh tool results on the next call, and reset the counter.
        nudges = []
        empty_retries = 0
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
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
                elif request_approval is None:
                    output = "(root_bash requires interactive approval, unavailable on this path; not executed)"
                elif request_approval(command):
                    output = root_bash(command)
                else:
                    output = "(command was not approved by the user; not executed)"
            else:
                output = f"(unknown tool: {name})"
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": output}
            )

    return "Reached the step limit before producing a final answer."


def new_messages() -> list[dict]:
    """Seed a fresh conversation with just the system prompt."""
    return [{"role": "system", "content": build_system_prompt()}]


def continue_conversation(messages: list[dict], query: str, on_event=None, request_approval=None) -> str:
    """Append a user turn to an existing conversation and run to an answer."""
    messages.append({"role": "user", "content": query})
    return _run_loop(messages, on_event, request_approval)
