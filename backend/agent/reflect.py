"""Nightly self-reflection: mine recently-changed conversations for durable facts
worth remembering, and prune memories a conversation shows are now wrong.

Runs once a day (APScheduler cron, see main.py's lifespan). This is an
unattended batch job, not a conversational turn, so each conversation gets one
tool-free completion that returns a JSON add/delete decision directly — it
skips _run_loop's tool-calling and approval gating entirely (save_memory /
delete_memory are already ungated for the interactive agent; this just decides
what to call them with, via db.memory_save / db.memory_delete directly). Every
run is logged to reflection_runs (db.reflection_run_save) so the dashboard can
show what happened while unattended.
"""
import json
import os
import time

import db
from .main import resolve_llm_config, _client_for

BATCH_SIZE = int(os.environ.get("AGENT_REFLECTION_BATCH", "20"))

REFLECTION_SYSTEM_PROMPT = """\
You are reviewing one past conversation from a server-management assistant's \
history, plus its current durable memory list, to decide what to remember or \
forget. Memories are PERMANENT and injected into every future conversation — \
save only durable, non-obvious, machine-wide facts (a quirk, a config, where \
something lives, the fix for a recurring problem, something the user asked to \
be remembered). Never save ephemeral state (temperatures, CPU%, uptime, the \
output of a one-off command) or a duplicate/near-duplicate of an existing \
memory. Delete a memory only if the conversation shows it is now wrong or \
obsolete. There is NO minimum number of memories to produce — most \
conversations should yield zero. Add a memory ONLY when it carries genuine \
added value over what is already remembered; never pad the list to hit a quota. \
If nothing qualifies, propose no changes.

Reply with ONLY a JSON object, no prose, no markdown fences:
{"add": ["new durable fact", ...], "delete": [id, ...]}
"""


def _transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _parse_decision(raw: str) -> dict:
    """Pull the first {...} block out of the reply — reasoning models sometimes
    wrap JSON in code fences or stray prose despite the instruction not to."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {"add": [], "delete": []}
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return {"add": [], "delete": []}
    add = [str(a).strip() for a in (data.get("add") or []) if str(a).strip()]
    delete = []
    for d in data.get("delete") or []:
        try:
            delete.append(int(d))
        except (TypeError, ValueError):
            pass
    return {"add": add, "delete": delete}


def _reflect_one(conv_id: str, memories: list[dict]) -> dict:
    """Ask the LLM what to add/delete after reading one conversation. Returns
    {"add": [...], "delete": [...]}, plus an "error" key on failure."""
    messages = db.chat_get(conv_id)
    if not messages:
        return {"add": [], "delete": []}
    transcript = _transcript(messages)
    if not transcript:
        return {"add": [], "delete": []}
    mem_block = "\n".join(f"[{m['id']}] {m['content']}" for m in memories) or "(none yet)"
    prompt = f"Current memories:\n{mem_block}\n\nConversation transcript:\n{transcript}"
    try:
        cfg = resolve_llm_config()
        resp = _client_for(cfg["base_url"], cfg["api_key"]).chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return {"add": [], "delete": [], "error": str(e)}
    return _parse_decision(raw)


def run_nightly_reflection() -> None:
    started = int(time.time())
    added: list[str] = []
    deleted: list[int] = []
    errors: list[str] = []
    conv_ids: list[str] = []

    try:
        conv_ids = db.chat_unreflected(BATCH_SIZE)
        for conv_id in conv_ids:
            decision = _reflect_one(conv_id, db.memory_list())
            if decision.get("error"):
                errors.append(f"{conv_id}: {decision['error']}")
                continue
            for content in decision["add"]:
                try:
                    db.memory_save(content)
                    added.append(content)
                except Exception as e:
                    errors.append(f"{conv_id}: save failed: {e}")
            for mid in decision["delete"]:
                try:
                    if db.memory_delete(mid):
                        deleted.append(mid)
                except Exception as e:
                    errors.append(f"{conv_id}: delete failed: {e}")
            try:
                db.chat_mark_reflected(conv_id)
            except Exception as e:
                errors.append(f"{conv_id}: mark_reflected failed: {e}")
    except Exception as e:
        errors.append(f"reflection run failed: {e}")

    finished = int(time.time())
    try:
        db.reflection_run_save(
            started_at=started, finished_at=finished, conversations=len(conv_ids),
            added=added, deleted=deleted, errors=errors,
        )
    except Exception:
        pass  # best-effort audit log; a logging failure shouldn't mask the run
