"""
Self-Tuning — Analyze feedback data to improve agent behavior.

NOT real-time — runs as a scheduled task (weekly) or on-demand.
Produces health reports, detects drift, suggests KB additions,
and analyzes edit patterns for prompt improvement.
"""

import logging
import sqlite3
from datetime import datetime, timedelta

from agency import feedback
from agency.monitors import MonitorEngine, load_monitors_from_config

logger = logging.getLogger(__name__)


def generate_health_report(db_path: str, client_id: str = "", days: int = 7) -> dict:
    """Generate a comprehensive agent health report.

    Returns a dict with all key metrics for display via channel.
    """
    stats = feedback.get_feedback_stats(client_id=client_id, days=days)

    # Drift detection
    drift = detect_drift(db_path, client_id=client_id)

    # KB gap suggestions
    kb_gaps = suggest_kb_additions(db_path, client_id=client_id)

    # Edit pattern analysis
    edit_analysis = analyze_edit_patterns(db_path, client_id=client_id, days=days)

    return {
        "period_days": days,
        "total_decisions": stats["total_decisions"],
        "approval_rate": stats["approval_rate"],
        "auto_approve_rate": stats["auto_approve_rate"],
        "avg_confidence": stats["avg_confidence"],
        "promoted_categories": stats["promoted_categories"],
        "by_action": stats["by_action"],
        "by_category": stats["by_category"],
        "drift_detected": drift,
        "kb_gaps": kb_gaps,
        "edit_analysis": edit_analysis,
    }


def format_health_report(report: dict) -> str:
    """Format health report as a readable message for the channel."""
    lines = [
        f"🏥 Agent Health Report — Last {report['period_days']} days\n",
        f"📊 Decisions: {report['total_decisions']}",
        f"✅ Approval rate: {report['approval_rate']:.0%}",
        f"🤖 Auto-approve rate: {report['auto_approve_rate']:.0%}",
    ]

    if report["avg_confidence"]:
        lines.append(f"🎯 Avg confidence: {report['avg_confidence']:.0%}")

    # Actions breakdown
    actions = report.get("by_action", {})
    if actions:
        lines.append("\n📋 Actions:")
        for action, count in actions.items():
            emoji = {"approve": "✅", "edit": "✏️", "skip": "❌", "auto_approve": "🤖"}.get(action, "•")
            lines.append(f"  {emoji} {action}: {count}")

    # Categories
    categories = report.get("by_category", {})
    if categories:
        lines.append("\n📁 Categories:")
        for cat, count in list(categories.items())[:5]:
            lines.append(f"  • {cat or 'uncategorized'}: {count}")

    # Promoted
    promoted = report.get("promoted_categories", [])
    if promoted:
        lines.append(f"\n🎓 Auto-promoted ({len(promoted)}):")
        for p in promoted:
            lines.append(f"  • {p['tool']}/{p['category']} (streak: {p['streak']})")

    # Drift
    if report.get("drift_detected"):
        lines.append("\n⚠️ DRIFT DETECTED — approval rate declining!")

    # KB Gaps
    kb_gaps = report.get("kb_gaps", [])
    if kb_gaps:
        lines.append(f"\n📋 KB Gaps ({len(kb_gaps)} topics need FAQ entries):")
        for gap in kb_gaps[:3]:
            lines.append(f"  • \"{gap['query']}\" ({gap['count']}× asked)")

    # Edit analysis
    edit = report.get("edit_analysis", {})
    if edit.get("edit_rate", 0) > 0.15:
        lines.append(f"\n✏️ Edit rate: {edit['edit_rate']:.0%} — consider tuning prompts")

    return "\n".join(lines)


def detect_drift(db_path: str, client_id: str = "", window_weeks: int = 4) -> bool:
    """Compare current week approval rate to rolling average.

    Returns True if approval rate dropped more than 15%.
    """
    try:
        current_rate = feedback.get_approval_rate(client_id=client_id, days=7)
        historical_rate = feedback.get_approval_rate(client_id=client_id, days=window_weeks * 7)

        if historical_rate == 0:
            return False

        drop = historical_rate - current_rate
        if drop > 0.15:
            logger.warning(
                f"Drift detected: current {current_rate:.0%} vs historical {historical_rate:.0%} "
                f"(drop: {drop:.0%})"
            )
            return True

        return False
    except Exception as e:
        logger.error(f"Drift detection failed: {e}")
        return False


def suggest_kb_additions(db_path: str, client_id: str = "", min_occurrences: int = 3, days: int = 14) -> list[dict]:
    """Find questions that had no KB match but came up multiple times."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()

        conditions = ["kb_matched = 0", "kb_query != ''", "created_at >= ?"]
        params: list = [since]

        if client_id:
            conditions.append("client_id = ?")
            params.append(client_id)

        where = " AND ".join(conditions)

        rows = conn.execute(
            f"""SELECT kb_query, COUNT(*) as cnt
                FROM support_tickets
                WHERE {where}
                GROUP BY kb_query
                HAVING cnt >= ?
                ORDER BY cnt DESC""",
            params + [min_occurrences],
        ).fetchall()

        return [{"query": r["kb_query"], "count": r["cnt"]} for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def analyze_edit_patterns(db_path: str, client_id: str = "", days: int = 30) -> dict:
    """Analyze human edit patterns to identify weak areas."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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

        edits = conn.execute(
            f"SELECT COUNT(*) FROM feedback_decisions WHERE {where} AND human_action = 'edit'",
            params,
        ).fetchone()[0]

        # Edits by category
        edits_by_cat = conn.execute(
            f"""SELECT category, COUNT(*) as cnt
                FROM feedback_decisions
                WHERE {where} AND human_action = 'edit'
                GROUP BY category
                ORDER BY cnt DESC""",
            params,
        ).fetchall()

        return {
            "edit_rate": edits / total if total > 0 else 0.0,
            "total": total,
            "edits": edits,
            "worst_categories": [{"category": r["category"], "count": r["cnt"]} for r in edits_by_cat[:5]],
        }
    except Exception:
        return {"edit_rate": 0.0, "total": 0, "edits": 0, "worst_categories": []}
    finally:
        conn.close()
