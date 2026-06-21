"""Human-in-the-loop approval for gated tools (root_bash, post).

ApprovalGate is owned per-turn (see endpoint._Turn). `request` is called from
the agent's executor thread — it registers a pending approval, dispatches an
approval_request event onto the event loop (dispatch is fed via
call_soon_threadsafe, since we're off-loop here), then blocks on a
threading.Event until `resolve` (a WS reply) or `cancel_all` (turn cancelled)
wakes it, or AGENT_APPROVAL_TIMEOUT elapses — a timeout or a cancel both
resolve to denied, so a never-answered prompt can't pin the worker forever.
"""
import threading
import uuid
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class ApprovalRequest:
    kind: str       # "bash" | "post"
    summary: str
    detail: str


@dataclass
class ApprovalResult:
    approved: bool
    token: Optional[str] = None


class ApprovalGate:
    def __init__(self, loop, dispatch: Callable[[dict], None], conv_id: str, timeout: float):
        self._loop = loop
        self._dispatch = dispatch
        self._conv_id = conv_id
        self._timeout = timeout
        self._lock = threading.Lock()
        # approval_id -> {"event": threading.Event, "result": ApprovalResult|None, "request": ApprovalRequest}
        self._pending: dict[str, dict] = {}

    def request(self, req: ApprovalRequest) -> ApprovalResult:
        approval_id = uuid.uuid4().hex
        event = threading.Event()
        with self._lock:
            self._pending[approval_id] = {"event": event, "result": None, "request": req}
        self._loop.call_soon_threadsafe(
            self._dispatch,
            {
                "type": "approval_request",
                "conv_id": self._conv_id,
                "approval_id": approval_id,
                "kind": req.kind,
                "summary": req.summary,
                "detail": req.detail,
            },
        )
        event.wait(self._timeout)
        with self._lock:
            entry = self._pending.pop(approval_id, None)
        if entry is None or entry["result"] is None:
            return ApprovalResult(approved=False, token=None)
        return entry["result"]

    def resolve(self, approval_id: str, approved: bool, token: Optional[str]) -> None:
        with self._lock:
            entry = self._pending.get(approval_id)
            if entry is None or entry["result"] is not None:
                return
            entry["result"] = ApprovalResult(approved=approved, token=token)
            event = entry["event"]
        event.set()

    def cancel_all(self) -> None:
        with self._lock:
            entries = [e for e in self._pending.values() if e["result"] is None]
            for e in entries:
                e["result"] = ApprovalResult(approved=False, token=None)
        for e in entries:
            e["event"].set()

    def pending(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "approval_id": approval_id,
                    "kind": e["request"].kind,
                    "summary": e["request"].summary,
                    "detail": e["request"].detail,
                }
                for approval_id, e in self._pending.items()
                if e["result"] is None
            ]
