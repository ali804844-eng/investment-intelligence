"""Chat sessions: persistence, context assembly, engine selection (LLM or deterministic)."""
from __future__ import annotations

import logging

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import llm, planner
from app.agent.tools import AgentContext
from app.db import utcnow
from app.models import AgentMemory, ChatMessage, ChatSession, Portfolio, User, Watchlist
from app.providers.sample import last_business_day

log = logging.getLogger(__name__)
HISTORY_TURNS = 10


def _context_block(db: Session, user: User) -> str:
    pfs = db.scalars(select(Portfolio).where(Portfolio.user_id == user.id)).all()
    wls = db.scalars(select(Watchlist).where(Watchlist.user_id == user.id)).all()
    prefs = db.scalars(select(AgentMemory).where(AgentMemory.user_id == user.id, AgentMemory.kind == "preference")
                       .order_by(AgentMemory.created_at.desc()).limit(8)).all()
    lines = [f"<context>Today (UTC): {utcnow().date()}; latest completed market day: {last_business_day()}.",
             "Portfolios: " + ("; ".join(f"#{p.id} {p.name} ({p.kind}, {'hypothetical' if p.is_hypothetical else 'actual'}, {p.base_currency})" for p in pfs) or "none"),
             "Watchlists: " + ("; ".join(f"#{w.id} {w.name} ({len(w.items)} items)" for w in wls) or "none")]
    if prefs:
        lines.append("User preferences from memory: " + " | ".join(m.content[:200] for m in prefs))
    lines.append("</context>")
    return "\n".join(lines)


def _history(db: Session, session_id: int) -> tuple[list[dict], list[dict]]:
    msgs = db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)).all()
    full: list[dict] = []
    text: list[dict] = []
    pending_user = None
    turns = []
    for m in msgs:
        if m.role == "user":
            pending_user = m
        elif m.role == "assistant" and pending_user is not None:
            turns.append((pending_user, m))
            pending_user = None
    for u, a in turns[-HISTORY_TURNS:]:
        text += [{"role": "user", "content": u.content}, {"role": "assistant", "content": a.content or "(no answer)"}]
        if a.raw:
            full += a.raw
        else:
            full += [{"role": "user", "content": u.content}, {"role": "assistant", "content": a.content or "(no answer)"}]
    return full, text


def handle_message(db: Session, user: User, text: str, session_id: int | None = None) -> dict:
    text = text.strip()[:8000]
    if not text:
        raise ValueError("empty message")
    if session_id:
        sess = db.get(ChatSession, session_id)
        if sess is None or sess.user_id != user.id:
            raise ValueError("session not found")
    else:
        sess = ChatSession(user_id=user.id, title=text[:80])
        db.add(sess)
        db.flush()
    full, text_hist = _history(db, sess.id)
    db.add(ChatMessage(session_id=sess.id, role="user", content=text))
    db.flush()
    ctx = AgentContext(db=db, user=user, actor="agent")
    use_llm = llm.llm_configured() and llm.budget_status(db, user.id)["ok"]
    result = None
    error_note = None
    if use_llm:
        try:
            result = llm.run_turn(ctx, full, _context_block(db, user) + "\n\n" + text, text_history=text_hist)
        except anthropic.AuthenticationError:
            error_note = "The configured Anthropic API key was rejected."
        except anthropic.RateLimitError:
            error_note = "The AI provider is rate-limiting requests; try again shortly."
        except anthropic.APIConnectionError:
            error_note = "Could not reach the AI provider (network)."
        except anthropic.APIStatusError as e:
            error_note = f"AI provider error ({e.status_code})."
    if result is None:
        result = planner.respond(ctx, text)
        if error_note:
            result["text"] = f"> ⚠️ {error_note} Falling back to deterministic mode.\n\n" + result["text"]
    msg = ChatMessage(session_id=sess.id, role="assistant", content=result["text"], tool_calls=result["tool_calls"],
                      raw=result.get("raw"), engine=result.get("engine", ""))
    db.add(msg)
    sess.updated_at = utcnow()
    db.flush()
    return {"session_id": sess.id, "message": {"id": msg.id, "role": "assistant", "content": msg.content,
                                               "tool_calls": msg.tool_calls, "engine": msg.engine,
                                               "created_at": msg.created_at.isoformat()},
            "pending_actions": ctx.pending_actions, "created": ctx.created}
