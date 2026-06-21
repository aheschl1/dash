"""Cooperative cancellation for a running agent turn.

A CancelToken is a one-way flag: tripped from the event-loop thread (a
{cancel} WS frame) and polled by the agent's executor thread at the top of
each step and between stream chunks (see main._stream_completion /
_run_loop). There is no way to un-cancel a token — a fresh turn gets a fresh
token.
"""
import threading


class CancelToken:
    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()


class TurnCanceled(Exception):
    """Raised by the agent loop to unwind at the next safe boundary (a clean
    user turn or a complete set of tool results)."""
