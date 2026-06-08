"""FastAPI routes for the stats agent.

Paths are under /api/ so both nginx layers route them to the backend (a bare
/query/-style path would hit the SPA fallback instead).

  POST   /api/newconversation       start a conversation -> {"id": ...}
  POST   /api/send/{id}             add a turn, get the reply (404 if unknown)
  GET    /api/conversations         list active conversation ids (+ preview)
  GET    /api/conversations/{id}    renderable turns for one conversation
  DELETE /api/end/{id}              retire a conversation (deletes the DB row)
  WS     /api/ws/agent             stream tool_call events + the final answer

Conversations persist in Postgres (see store.py); they survive restarts and
reloads, and are removed only by an explicit end or the DB's most-recent-N cap.
"""
import asyncio
import json
import os
import threading
import uuid

from fastapi import APIRouter, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import auth
import db

from . import store
from .main import continue_conversation

router = APIRouter()

# How long the agent loop blocks waiting for the user to approve/deny a root_bash
# command before giving up and treating it as denied (so a never-answered prompt
# can't pin a worker thread or a busy claim forever).
APPROVAL_TIMEOUT = int(os.environ.get("AGENT_APPROVAL_TIMEOUT", "180"))


def _tool_chip(tc: dict) -> str:
    """Render a stored tool_call as the same 'name arg' chip the WS streams live.

    Mirrors AgentPanel's live formatting: show the call's primary arg (path/query)
    when there is one, falling back to the first argument value.
    """
    fn = tc.get("function", {})
    name = fn.get("name", "")
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except (json.JSONDecodeError, TypeError):
        args = {}
    arg = args.get("path") if isinstance(args, dict) else None
    if arg is None and isinstance(args, dict) and args:
        arg = next(iter(args.values()))
    return f"{name} {arg}".strip() if arg else name


def _render(messages: list[dict]) -> list[dict]:
    """Reduce a raw message list to the turns the UI shows.

    Drops the system prompt and the raw tool-result plumbing, but surfaces each
    assistant tool_call as a `tool` chip (same shape the WS streams live) so a
    reloaded conversation shows the same tool activity, then the answer bubbles.
    """
    turns = []
    for m in messages:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role == "user":
            turns.append({"role": "user", "content": m.get("content", "")})
        elif role == "assistant":
            for tc in m.get("tool_calls") or []:
                turns.append({"role": "tool", "content": _tool_chip(tc)})
            if content:
                turns.append({"role": "assistant", "content": m["content"]})
    return turns


@router.post("/api/newconversation")
def new_conversation():
    return {"id": store.create()}


@router.post("/api/send/{conv_id}")
def send(conv_id: str, message: str = Body(..., embed=True)):
    if not message.strip():
        return JSONResponse(status_code=400, content={"error": "message required"})
    messages = store.get(conv_id)
    if messages is None:
        return JSONResponse(status_code=404, content={"error": "unknown conversation"})
    if not store.try_begin(conv_id):
        return JSONResponse(status_code=409, content={"error": "conversation is busy"})
    try:
        answer = continue_conversation(messages, message.strip())
        store.save(conv_id, messages)
        return {"answer": answer}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        store.finish(conv_id)


@router.get("/api/conversations")
def list_conversations():
    return {"conversations": db.chat_list()}


@router.get("/api/conversations/{conv_id}")
def get_conversation(conv_id: str):
    messages = store.get_copy(conv_id)
    if messages is None:
        return JSONResponse(status_code=404, content={"error": "unknown conversation"})
    return {"id": conv_id, "messages": _render(messages)}


@router.delete("/api/end/{conv_id}")
def end(conv_id: str):
    return {"ended": store.end(conv_id)}


# In-flight WS turns, keyed by conv_id. A turn lives here from the moment its
# message arrives until it finishes and persists — decoupled from the socket that
# started it, so a client that drops mid-turn (tab close, refresh, network blip)
# doesn't abort or lose the answer. The task reference keeps it from being GC'd.
_running: "dict[str, _Turn]" = {}


class _Approval:
    """A pending root_bash approval. The agent's executor thread blocks on
    `event` until the WS handler (on the loop thread) resolves it; both touch
    only `approved`/`event`, which are thread-safe to hand across like this."""

    def __init__(self, command: str):
        self.command = command
        self.approved = False
        self.event = threading.Event()


class _Turn:
    """A running agent turn other sockets can subscribe to, even after the
    originating socket is gone. Lives on the event loop thread; `dispatch` is
    fed from the agent's executor thread via call_soon_threadsafe, so all access
    to `events`/`subscribers`/`approvals` happens single-threaded on the loop."""

    def __init__(self, conv_id: str, user_message: str):
        self.conv_id = conv_id
        self.user_message = user_message
        self.events: list[dict] = []           # buffered tool_call events for replay
        self.subscribers: "set[asyncio.Queue]" = set()
        self.approvals: "dict[str, _Approval]" = {}
        self.task: "asyncio.Task | None" = None

    def attach(self, q: "asyncio.Queue") -> list[dict]:
        """Subscribe a queue and atomically snapshot what already happened.

        No await between the add and the snapshot, so on the single-threaded loop
        no event can slip in between — the caller gets every past event exactly
        once via the snapshot and every future event exactly once via the queue."""
        self.subscribers.add(q)
        return list(self.events)

    def detach(self, q: "asyncio.Queue") -> None:
        self.subscribers.discard(q)

    def dispatch(self, ev: dict) -> None:
        if ev.get("type") == "tool_call":
            self.events.append(ev)
        for q in self.subscribers:
            q.put_nowait(ev)

    def add_approval(self, approval_id: str, approval: "_Approval") -> None:
        self.approvals[approval_id] = approval

    def pop_approval(self, approval_id: str) -> None:
        self.approvals.pop(approval_id, None)

    def resolve_approval(self, approval_id: str, approved: bool) -> None:
        approval = self.approvals.get(approval_id)
        if approval is not None:
            approval.approved = approved
            approval.event.set()

    def pending_approvals(self) -> list[dict]:
        return [{"approval_id": k, "command": a.command} for k, a in self.approvals.items()]


async def _run_turn(turn: "_Turn", messages: list[dict]) -> None:
    """Drive one turn to completion and persist it, regardless of who's listening.

    Runs the blocking agent loop (and the DB save) in the executor, fanning
    tool_call/answer/error/done events out to any subscribed sockets. Always
    saves on success and always releases the busy claim, so the turn survives a
    dropped socket and a reconnecting client sees the finished, persisted chat."""
    loop = asyncio.get_running_loop()
    conv_id = turn.conv_id

    def on_event(ev):
        loop.call_soon_threadsafe(turn.dispatch, {**ev, "conv_id": conv_id})

    # Blocking approval gate for root_bash. Runs on the executor thread: register
    # the pending approval and emit an approval_request, then block on the Event
    # until the WS handler resolves it (or we time out and deny). All loop-thread
    # state (approvals/subscribers) is touched only via call_soon_threadsafe.
    def request_approval(command: str) -> bool:
        approval = _Approval(command)
        approval_id = uuid.uuid4().hex
        loop.call_soon_threadsafe(turn.add_approval, approval_id, approval)
        loop.call_soon_threadsafe(turn.dispatch, {
            "type": "approval_request",
            "conv_id": conv_id,
            "approval_id": approval_id,
            "command": command,
        })
        got = approval.event.wait(timeout=APPROVAL_TIMEOUT)
        loop.call_soon_threadsafe(turn.pop_approval, approval_id)
        return bool(got and approval.approved)

    try:
        answer = await loop.run_in_executor(
            None, continue_conversation, messages, turn.user_message, on_event, request_approval
        )
        await loop.run_in_executor(None, store.save, conv_id, messages)
        turn.dispatch({"type": "answer", "conv_id": conv_id, "content": answer})
    except Exception as e:
        turn.dispatch({"type": "error", "conv_id": conv_id, "error": str(e)})
    finally:
        turn.dispatch({"type": "done", "conv_id": conv_id})
        _running.pop(conv_id, None)
        store.finish(conv_id)


@router.websocket("/api/ws/agent")
async def ws_agent(ws: WebSocket):
    """One socket per panel. The reader (this loop) and a writer task run
    concurrently so the socket can RECEIVE an approval reply while a turn is still
    streaming events out — otherwise a root_bash approval would deadlock (the loop
    parked on the event queue, never reading the reply that would unblock the turn).

    Client → server messages:
      {conv_id, message}        start a turn (or, if one is already running for
                                that conv, attach to it instead of double-running)
      {conv_id, subscribe:true} rejoin an in-flight turn after a reconnect; server
                                replies {type:idle} if nothing is running
      {conv_id, approval_id, approved[, token]}
                                answer a root_bash approval prompt (approve needs a
                                valid admin token; deny needs none)

    Server → client events all carry conv_id: tool_call, approval_request, answer,
    error, done, plus resume (subscribe to a live turn), idle (nothing running), and
    approval_unauthorized (approve without a valid admin token).

    Every outbound frame goes through the single writer draining `out`; Starlette
    sockets can't be written from two coroutines at once. The turn itself runs in
    _run_turn, decoupled from this socket, so a drop mid-turn neither aborts it nor
    loses the persisted answer.
    """
    await ws.accept()
    out: asyncio.Queue = asyncio.Queue()
    # Turns this socket currently follows, by conv_id, so we can detach on close.
    attached: "dict[str, _Turn]" = {}

    async def writer():
        try:
            while True:
                ev = await out.get()
                await ws.send_json(ev)
                # Stop following a turn once it ends, mirroring the old per-turn
                # stream lifetime (the client only ever views one conv at a time).
                if ev.get("type") == "done":
                    t = attached.pop(ev.get("conv_id"), None)
                    if t is not None:
                        t.detach(out)
        except Exception:
            # Socket closed mid-send (client vanished). The turn persists in
            # _run_turn regardless; the reader's disconnect drives cleanup.
            pass

    def subscribe(turn: "_Turn", *, resume: bool) -> None:
        # Attach this socket's out-queue and, for a resume, enqueue the snapshot
        # synchronously (no await between attach and put) so the resume frame can't
        # be overtaken by a freshly dispatched live event.
        prev = attached.get(turn.conv_id)
        if prev is not None and prev is not turn:
            prev.detach(out)
        buffered = turn.attach(out)
        attached[turn.conv_id] = turn
        if resume:
            out.put_nowait({
                "type": "resume",
                "conv_id": turn.conv_id,
                "user_message": turn.user_message,
                "events": buffered,
                "approvals": turn.pending_approvals(),
            })

    writer_task = asyncio.create_task(writer())
    try:
        while True:
            data = await ws.receive_json()
            conv_id = (data or {}).get("conv_id")
            approval_id = (data or {}).get("approval_id")
            subscribe_req = bool((data or {}).get("subscribe"))
            message = ((data or {}).get("message") or "").strip()

            # A reply to a root_bash approval prompt: resolve the pending approval
            # on its turn, which unblocks the agent's executor thread. Approving runs
            # arbitrary root, so it requires a valid admin token; an invalid/missing
            # one leaves the approval pending and tells the client to log in. Denying
            # needs no auth (anyone may cancel — the fail-safe direction). Any
            # subscribed socket can answer.
            if approval_id is not None:
                turn = _running.get(conv_id)
                if turn is None:
                    continue
                approved = bool((data or {}).get("approved"))
                if approved and not auth.verify_token((data or {}).get("token") or "", db.get_auth_secret()):
                    out.put_nowait({"type": "approval_unauthorized", "conv_id": conv_id, "approval_id": approval_id})
                    continue
                turn.resolve_approval(approval_id, approved)
                continue

            if subscribe_req:
                turn = _running.get(conv_id)
                if turn is None:
                    out.put_nowait({"type": "idle", "conv_id": conv_id})
                else:
                    subscribe(turn, resume=True)
                continue

            if not message:
                out.put_nowait({"type": "error", "conv_id": conv_id, "error": "message required"})
                continue

            # Already running (e.g. a duplicate send after reconnect): attach
            # rather than start a second, list-corrupting turn.
            existing = _running.get(conv_id)
            if existing is not None:
                subscribe(existing, resume=True)
                continue

            messages = store.get(conv_id)
            if messages is None:
                out.put_nowait({"type": "error", "conv_id": conv_id, "error": "unknown conversation"})
                continue
            if not store.try_begin(conv_id):
                out.put_nowait({"type": "error", "conv_id": conv_id, "error": "conversation is busy"})
                continue

            turn = _Turn(conv_id, message)
            _running[conv_id] = turn
            subscribe(turn, resume=False)
            turn.task = asyncio.create_task(_run_turn(turn, messages))
    except WebSocketDisconnect:
        pass
    finally:
        writer_task.cancel()
        for turn in attached.values():
            turn.detach(out)
