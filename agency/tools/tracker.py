"""
Tracker — SQLite CRUD for tracked email items and follow-up schedules.

The tracker is the agent's "notebook." One row per tracked email,
with a linked follow-up schedule and activity log.

Shared across all workflows that track outbound communications.
"""

import json
import sqlite3
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def _init_db(db_path: str) -> sqlite3.Connection:
    """Create tables if they don't exist and return a connection."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tracked_items (
            id              TEXT PRIMARY KEY,
            email_id        TEXT NOT NULL,
            thread_id       TEXT NOT NULL,
            to_email        TEXT NOT NULL,
            to_name         TEXT DEFAULT '',
            subject         TEXT NOT NULL,
            sent_date       TEXT NOT NULL,
            context_summary TEXT NOT NULL,
            item_type       TEXT DEFAULT 'general',
            status          TEXT DEFAULT 'awaiting_response',
            response_date   TEXT,
            cancelled_reason TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS follow_ups (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            tracked_item_id TEXT NOT NULL REFERENCES tracked_items(id),
            follow_up_number INTEGER NOT NULL,
            day_offset      INTEGER NOT NULL,
            template        TEXT NOT NULL,
            status          TEXT DEFAULT 'pending',
            draft_body      TEXT,
            draft_subject   TEXT,
            sent_date       TEXT,
            approved_by     TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            tracked_item_id TEXT REFERENCES tracked_items(id),
            action          TEXT NOT NULL,
            details         TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_tracked_status ON tracked_items(status);
        CREATE INDEX IF NOT EXISTS idx_followups_item ON follow_ups(tracked_item_id);
        CREATE INDEX IF NOT EXISTS idx_followups_status ON follow_ups(status);
        CREATE INDEX IF NOT EXISTS idx_activity_item ON activity_log(tracked_item_id);
    """)

    conn.commit()
    return conn


# ─── Module-level state ───────────────────────────────────────────────

_db_path: str | None = None


def init_tracker(db_path: str):
    """Initialize the tracker with a database path. Call before using tool functions."""
    global _db_path
    _db_path = db_path
    # Create tables on init
    conn = _init_db(db_path)
    conn.close()
    logger.info(f"Tracker initialized: {db_path}")


def _get_conn() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("Tracker not initialized. Call init_tracker() first.")
    return _init_db(_db_path)


# ─── Tool functions (exposed to Agent) ────────────────────────────────


def create_tracked_item(
    email_id: str,
    thread_id: str,
    to_email: str,
    to_name: str,
    subject: str,
    sent_date: str,
    context_summary: str,
    item_type: str,
    follow_up_schedule_json: str,
) -> str:
    """Create a new tracked item for an email that needs follow-up. The follow_up_schedule_json should be a JSON array like: [{"number": 1, "day_offset": 3, "template": "gentle_check_in"}, ...]"""
    conn = _get_conn()
    item_id = f"tr-{uuid.uuid4().hex[:8]}"

    try:
        conn.execute(
            """INSERT INTO tracked_items
               (id, email_id, thread_id, to_email, to_name, subject, sent_date, context_summary, item_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, email_id, thread_id, to_email, to_name, subject, sent_date, context_summary, item_type),
        )

        # Create follow-up schedule entries
        schedule = json.loads(follow_up_schedule_json)
        for step in schedule:
            conn.execute(
                """INSERT INTO follow_ups (tracked_item_id, follow_up_number, day_offset, template)
                   VALUES (?, ?, ?, ?)""",
                (item_id, step["number"], step["day_offset"], step["template"]),
            )

        # Log the creation
        conn.execute(
            "INSERT INTO activity_log (tracked_item_id, action, details) VALUES (?, ?, ?)",
            (item_id, "tracked", json.dumps({"to": to_email, "subject": subject, "type": item_type})),
        )

        conn.commit()
        logger.info(f"Created tracked item {item_id}: {subject} → {to_email}")
        return json.dumps({"item_id": item_id, "status": "created", "follow_ups_scheduled": len(schedule)})

    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating tracked item: {e}")
        raise
    finally:
        conn.close()


def get_pending_items() -> str:
    """Get all tracked items that are still awaiting a response. Use this to check which emails need response monitoring."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT id, email_id, thread_id, to_email, to_name, subject,
                      sent_date, context_summary, item_type, status
               FROM tracked_items
               WHERE status = 'awaiting_response'
               ORDER BY sent_date DESC"""
        ).fetchall()

        items = [dict(row) for row in rows]
        return json.dumps({"count": len(items), "items": items})
    finally:
        conn.close()


def get_due_follow_ups() -> str:
    """Get follow-ups that are due to be sent right now. A follow-up is due when the current date minus the original sent date is >= the day_offset, and the follow-up hasn't been sent yet."""
    conn = _get_conn()
    try:
        now = datetime.now()

        rows = conn.execute(
            """SELECT f.id, f.tracked_item_id, f.follow_up_number, f.day_offset,
                      f.template, f.status,
                      t.email_id, t.thread_id, t.to_email, t.to_name,
                      t.subject, t.sent_date, t.context_summary, t.item_type
               FROM follow_ups f
               JOIN tracked_items t ON f.tracked_item_id = t.id
               WHERE f.status = 'pending'
                 AND t.status = 'awaiting_response'
               ORDER BY t.sent_date ASC, f.follow_up_number ASC"""
        ).fetchall()

        due = []
        for row in rows:
            row_dict = dict(row)
            sent_date = datetime.fromisoformat(row_dict["sent_date"])
            days_elapsed = (now - sent_date).days

            if days_elapsed >= row_dict["day_offset"]:
                # Check that previous follow-ups are done
                prev = conn.execute(
                    """SELECT status FROM follow_ups
                       WHERE tracked_item_id = ? AND follow_up_number < ?
                       AND status = 'pending'""",
                    (row_dict["tracked_item_id"], row_dict["follow_up_number"]),
                ).fetchall()

                if not prev:  # All previous are sent/skipped
                    row_dict["days_elapsed"] = days_elapsed
                    due.append(row_dict)

        return json.dumps({"count": len(due), "due_follow_ups": due})
    finally:
        conn.close()


def mark_response_received(tracked_item_id: str) -> str:
    """Mark a tracked item as having received a response. This stops all pending follow-ups for this item."""
    conn = _get_conn()
    try:
        now = datetime.now().isoformat()

        conn.execute(
            "UPDATE tracked_items SET status = 'response_received', response_date = ?, updated_at = ? WHERE id = ?",
            (now, now, tracked_item_id),
        )

        # Cancel all pending follow-ups
        conn.execute(
            "UPDATE follow_ups SET status = 'skipped' WHERE tracked_item_id = ? AND status = 'pending'",
            (tracked_item_id,),
        )

        conn.execute(
            "INSERT INTO activity_log (tracked_item_id, action, details) VALUES (?, ?, ?)",
            (tracked_item_id, "response_received", json.dumps({"date": now})),
        )

        conn.commit()
        logger.info(f"Response received for {tracked_item_id}")
        return json.dumps({"status": "updated", "item_id": tracked_item_id})
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_follow_up_sent(tracked_item_id: str, follow_up_number: int, draft_subject: str, draft_body: str) -> str:
    """Mark a specific follow-up as sent after it has been approved and delivered."""
    conn = _get_conn()
    try:
        now = datetime.now().isoformat()

        conn.execute(
            """UPDATE follow_ups
               SET status = 'sent', sent_date = ?, draft_subject = ?, draft_body = ?
               WHERE tracked_item_id = ? AND follow_up_number = ?""",
            (now, draft_subject, draft_body, tracked_item_id, follow_up_number),
        )

        conn.execute(
            "UPDATE tracked_items SET updated_at = ? WHERE id = ?",
            (now, tracked_item_id),
        )

        conn.execute(
            "INSERT INTO activity_log (tracked_item_id, action, details) VALUES (?, ?, ?)",
            (tracked_item_id, "follow_up_sent", json.dumps({"number": follow_up_number, "date": now})),
        )

        # If this was the last follow-up, mark item as completed
        remaining = conn.execute(
            "SELECT COUNT(*) FROM follow_ups WHERE tracked_item_id = ? AND status = 'pending'",
            (tracked_item_id,),
        ).fetchone()[0]

        if remaining == 0:
            conn.execute(
                "UPDATE tracked_items SET status = 'completed', updated_at = ? WHERE id = ?",
                (now, tracked_item_id),
            )

        conn.commit()
        return json.dumps({"status": "marked_sent", "item_id": tracked_item_id, "follow_up_number": follow_up_number})
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def cancel_tracking(tracked_item_id: str, reason: str) -> str:
    """Cancel all follow-up tracking for a specific item. Use when the user hits 'Stop all follow-ups'."""
    conn = _get_conn()
    try:
        now = datetime.now().isoformat()

        conn.execute(
            "UPDATE tracked_items SET status = 'cancelled', cancelled_reason = ?, updated_at = ? WHERE id = ?",
            (reason, now, tracked_item_id),
        )

        conn.execute(
            "UPDATE follow_ups SET status = 'skipped' WHERE tracked_item_id = ? AND status = 'pending'",
            (tracked_item_id,),
        )

        conn.execute(
            "INSERT INTO activity_log (tracked_item_id, action, details) VALUES (?, ?, ?)",
            (tracked_item_id, "cancelled", json.dumps({"reason": reason})),
        )

        conn.commit()
        return json.dumps({"status": "cancelled", "item_id": tracked_item_id})
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_weekly_stats() -> str:
    """Get summary statistics for the weekly report. Returns counts of tracked items, follow-ups sent, responses received, and response rates."""
    conn = _get_conn()
    try:
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()

        total_active = conn.execute(
            "SELECT COUNT(*) FROM tracked_items WHERE status = 'awaiting_response'"
        ).fetchone()[0]

        follow_ups_sent = conn.execute(
            "SELECT COUNT(*) FROM follow_ups WHERE status = 'sent' AND sent_date >= ?",
            (week_ago,),
        ).fetchone()[0]

        responses_received = conn.execute(
            "SELECT COUNT(*) FROM tracked_items WHERE status = 'response_received' AND response_date >= ?",
            (week_ago,),
        ).fetchone()[0]

        completed = conn.execute(
            "SELECT COUNT(*) FROM tracked_items WHERE status = 'completed' AND updated_at >= ?",
            (week_ago,),
        ).fetchone()[0]

        # Response rate by follow-up number
        by_number = conn.execute(
            """SELECT f.follow_up_number, COUNT(*) as count
               FROM follow_ups f
               WHERE f.status = 'sent' AND f.sent_date >= ?
               GROUP BY f.follow_up_number""",
            (week_ago,),
        ).fetchall()

        return json.dumps({
            "period": f"Last 7 days (since {week_ago[:10]})",
            "active_tracked_items": total_active,
            "follow_ups_sent_this_week": follow_ups_sent,
            "responses_received_this_week": responses_received,
            "items_completed_this_week": completed,
            "follow_ups_by_number": {str(r[0]): r[1] for r in by_number},
        })
    finally:
        conn.close()


def is_already_tracked(thread_id: str) -> str:
    """Check if a thread is already being tracked. Use this to avoid duplicate tracking."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, status FROM tracked_items WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()

        if row:
            return json.dumps({"is_tracked": True, "item_id": row["id"], "status": row["status"]})
        return json.dumps({"is_tracked": False})
    finally:
        conn.close()
