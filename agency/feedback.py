"""
Feedback — SQLite storage for HITL decisions, approval streaks, and learning.

Records every human approve/edit/skip decision with full context.
Tracks consecutive approval streaks per (client, tool, category) for
auto-promotion (confidence routing skips HITL after N approvals).

Same pattern as tracker.py: WAL mode, module-level _db_path, _init_db/_get_conn.
"""

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def _init_db(db_path: str) -> sqlite3.Connection:
    """Create tables if they don't exist and return a connection."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS feedback_decisions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_name       TEXT NOT NULL,
            tool_input_hash TEXT NOT NULL,
            tool_input_json TEXT NOT NULL,
            confidence      REAL,
            confidence_band TEXT,
            route_taken     TEXT NOT NULL DEFAULT 'hitl_with_draft',
            human_action    TEXT,
            human_edit_text TEXT,
            original_draft  TEXT,
            client_id       TEXT DEFAULT '',
            agent_name      TEXT NOT NULL DEFAULT '',
            category        TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS approval_streaks (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id             TEXT NOT NULL,
            tool_name             TEXT NOT NULL,
            category              TEXT DEFAULT '',
            consecutive_approvals INTEGER DEFAULT 0,
            last_action           TEXT,
            auto_promoted         INTEGER DEFAULT 0,
            promoted_at           TEXT,
            updated_at            TEXT DEFAULT (datetime('now')),
            UNIQUE(client_id, tool_name, category)
        );

        CREATE TABLE IF NOT EXISTS support_tickets (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id        TEXT,
            thread_id       TEXT,
            from_email      TEXT NOT NULL,
            from_name       TEXT DEFAULT '',
            subject         TEXT NOT NULL,
            body_preview    TEXT DEFAULT '',
            category        TEXT DEFAULT '',
            urgency         TEXT DEFAULT 'medium',
            sentiment       TEXT DEFAULT 'neutral',
            kb_matched      INTEGER DEFAULT 0,
            kb_query        TEXT DEFAULT '',
            response_draft  TEXT DEFAULT '',
            response_sent   INTEGER DEFAULT 0,
            escalated       INTEGER DEFAULT 0,
            client_id       TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_fb_tool ON feedback_decisions(tool_name);
        CREATE INDEX IF NOT EXISTS idx_fb_client ON feedback_decisions(client_id);
        CREATE INDEX IF NOT EXISTS idx_fb_category ON feedback_decisions(category);
        CREATE INDEX IF NOT EXISTS idx_fb_created ON feedback_decisions(created_at);
        CREATE INDEX IF NOT EXISTS idx_streak_lookup ON approval_streaks(client_id, tool_name, category);
        CREATE INDEX IF NOT EXISTS idx_tickets_created ON support_tickets(created_at);
        CREATE INDEX IF NOT EXISTS idx_tickets_category ON support_tickets(category);
    """)

    conn.commit()
    return conn


# ─── Module-level state ───────────────────────────────────────────────

_db_path: str | None = None


def init_feedback(db_path: str):
    """Initialize the feedback DB. Call before using any functions."""
    global _db_path
    _db_path = db_path
    conn = _init_db(db_path)
    conn.close()
    logger.info(f"Feedback DB initialized: {db_path}")


def _get_conn() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("Feedback not initialized. Call init_feedback() first.")
    return _init_db(_db_path)


# ─── Core feedback functions ──────────────────────────────────────────


def record_decision(
    tool_name: str,
    tool_input: dict,
    human_action: str,
    confidence: float | None = None,
    confidence_band: str = "",
    route_taken: str = "hitl_with_draft",
    original_draft: str = "",
    human_edit_text: str = "",
    client_id: str = "",
    agent_name: str = "",
    category: str = "",
) -> int:
    """Record a HITL decision into the feedback DB. Returns the row ID."""
    conn = _get_conn()
    try:
        input_json = json.dumps(tool_input, default=str)
        input_hash = hashlib.sha256(input_json.encode()).hexdigest()[:16]

        cursor = conn.execute(
            """INSERT INTO feedback_decisions
               (tool_name, tool_input_hash, tool_input_json, confidence, confidence_band,
                route_taken, human_action, original_draft, human_edit_text,
                client_id, agent_name, category)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tool_name, input_hash, input_json, confidence, confidence_band,
             route_taken, human_action, original_draft, human_edit_text,
             client_id, agent_name, category),
        )
        conn.commit()
        row_id = cursor.lastrowid
        logger.info(f"Feedback recorded: {tool_name} → {human_action} (confidence: {confidence})")
        return row_id
    finally:
        conn.close()


def update_streak(
    client_id: str,
    tool_name: str,
    category: str,
    human_action: str,
    auto_promote_threshold: int = 20,
) -> dict:
    """Update the approval streak. Returns streak info including if auto-promoted."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM approval_streaks WHERE client_id = ? AND tool_name = ? AND category = ?",
            (client_id, tool_name, category),
        ).fetchone()

        now = datetime.now().isoformat()

        if row is None:
            # First decision for this combo
            streak = 1 if human_action == "approve" else 0
            conn.execute(
                """INSERT INTO approval_streaks
                   (client_id, tool_name, category, consecutive_approvals, last_action, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (client_id, tool_name, category, streak, human_action, now),
            )
        else:
            if human_action == "approve":
                streak = dict(row)["consecutive_approvals"] + 1
            else:
                streak = 0  # Reset on edit or skip

            conn.execute(
                """UPDATE approval_streaks
                   SET consecutive_approvals = ?, last_action = ?, updated_at = ?
                   WHERE client_id = ? AND tool_name = ? AND category = ?""",
                (streak, human_action, now, client_id, tool_name, category),
            )

        # Check for auto-promotion
        promoted = False
        if streak >= auto_promote_threshold:
            existing = conn.execute(
                "SELECT auto_promoted FROM approval_streaks WHERE client_id = ? AND tool_name = ? AND category = ?",
                (client_id, tool_name, category),
            ).fetchone()

            if existing and not dict(existing)["auto_promoted"]:
                conn.execute(
                    """UPDATE approval_streaks
                       SET auto_promoted = 1, promoted_at = ?
                       WHERE client_id = ? AND tool_name = ? AND category = ?""",
                    (now, client_id, tool_name, category),
                )
                promoted = True
                logger.info(f"🎓 Auto-promoted: {tool_name}/{category} for {client_id} (streak: {streak})")

        conn.commit()
        return {"streak": streak, "promoted": promoted, "threshold": auto_promote_threshold}

    finally:
        conn.close()


def get_streak(client_id: str, tool_name: str, category: str) -> int:
    """Get current consecutive approval count."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT consecutive_approvals FROM approval_streaks WHERE client_id = ? AND tool_name = ? AND category = ?",
            (client_id, tool_name, category),
        ).fetchone()
        return dict(row)["consecutive_approvals"] if row else 0
    finally:
        conn.close()


def check_auto_promote(client_id: str, tool_name: str, category: str) -> bool:
    """Check if a (client, tool, category) combo has been auto-promoted."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT auto_promoted FROM approval_streaks WHERE client_id = ? AND tool_name = ? AND category = ?",
            (client_id, tool_name, category),
        ).fetchone()
        return bool(dict(row)["auto_promoted"]) if row else False
    finally:
        conn.close()


def get_approval_rate(client_id: str = "", tool_name: str = "", days: int = 30) -> float:
    """Get approval rate (0.0-1.0) for the given filters over the last N days."""
    conn = _get_conn()
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        conditions = ["created_at >= ?"]
        params: list = [since]

        if client_id:
            conditions.append("client_id = ?")
            params.append(client_id)
        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)

        where = " AND ".join(conditions)

        total = conn.execute(
            f"SELECT COUNT(*) FROM feedback_decisions WHERE {where}", params
        ).fetchone()[0]

        if total == 0:
            return 0.0

        approved = conn.execute(
            f"SELECT COUNT(*) FROM feedback_decisions WHERE {where} AND human_action = 'approve'",
            params,
        ).fetchone()[0]

        return approved / total
    finally:
        conn.close()


def get_feedback_stats(client_id: str = "", days: int = 7) -> dict:
    """Get summary stats for health reports."""
    conn = _get_conn()
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        conditions = ["created_at >= ?"]
        params: list = [since]

        if client_id:
            conditions.append("client_id = ?")
            params.append(client_id)

        where = " AND ".join(conditions)

        total = conn.execute(
            f"SELECT COUNT(*) FROM feedback_decisions WHERE {where}", params
        ).fetchone()[0]

        by_action = conn.execute(
            f"SELECT human_action, COUNT(*) as cnt FROM feedback_decisions WHERE {where} GROUP BY human_action",
            params,
        ).fetchall()

        by_category = conn.execute(
            f"SELECT category, COUNT(*) as cnt FROM feedback_decisions WHERE {where} GROUP BY category ORDER BY cnt DESC",
            params,
        ).fetchall()

        auto_approved = conn.execute(
            f"SELECT COUNT(*) FROM feedback_decisions WHERE {where} AND route_taken = 'auto_execute'",
            params,
        ).fetchone()[0]

        avg_confidence = conn.execute(
            f"SELECT AVG(confidence) FROM feedback_decisions WHERE {where} AND confidence IS NOT NULL",
            params,
        ).fetchone()[0]

        # Get promoted categories
        streak_conditions = []
        streak_params: list = []
        if client_id:
            streak_conditions.append("client_id = ?")
            streak_params.append(client_id)

        streak_where = " AND ".join(streak_conditions) if streak_conditions else "1=1"
        promoted = conn.execute(
            f"SELECT tool_name, category, consecutive_approvals FROM approval_streaks WHERE {streak_where} AND auto_promoted = 1",
            streak_params,
        ).fetchall()

        return {
            "period_days": days,
            "total_decisions": total,
            "by_action": {str(r["human_action"]): r["cnt"] for r in by_action},
            "by_category": {str(r["category"]): r["cnt"] for r in by_category},
            "auto_approved": auto_approved,
            "auto_approve_rate": auto_approved / total if total > 0 else 0.0,
            "approval_rate": sum(r["cnt"] for r in by_action if r["human_action"] == "approve") / total if total > 0 else 0.0,
            "avg_confidence": round(avg_confidence, 3) if avg_confidence else None,
            "promoted_categories": [
                {"tool": r["tool_name"], "category": r["category"], "streak": r["consecutive_approvals"]}
                for r in promoted
            ],
        }
    finally:
        conn.close()


def record_ticket(
    from_email: str,
    subject: str,
    category: str = "",
    urgency: str = "medium",
    sentiment: str = "neutral",
    kb_matched: bool = False,
    kb_query: str = "",
    from_name: str = "",
    body_preview: str = "",
    email_id: str = "",
    thread_id: str = "",
    client_id: str = "",
) -> int:
    """Record a support ticket for monitor tracking."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            """INSERT INTO support_tickets
               (email_id, thread_id, from_email, from_name, subject, body_preview,
                category, urgency, sentiment, kb_matched, kb_query, client_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (email_id, thread_id, from_email, from_name, subject, body_preview,
             category, urgency, sentiment, int(kb_matched), kb_query, client_id),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()
