"""
Business Process Monitors — Proactive anomaly detection for business processes.

Inspired by Ramp's monitor-driven maintenance: define checks, run on cron,
triage alerts (real issue vs noise), notify human selectively.

Monitors run as standalone checks — not inside the agent loop.
They query the feedback/ticket DB and fire alerts when conditions are met.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MonitorDef:
    """A monitor definition — what to check and when."""
    id: str
    monitor_type: str       # "volume_spike", "kb_gaps", "response_rate_drop", "draft_quality_drift"
    cron: str               # cron expression (for display — actual scheduling is external)
    config: dict = field(default_factory=dict)
    enabled: bool = True


@dataclass
class MonitorAlert:
    """An alert fired by a monitor check."""
    monitor_id: str
    severity: str           # "info", "warning", "critical"
    message: str
    data: dict = field(default_factory=dict)
    triage_result: str = ""  # "real_issue", "noise", "tuned_out"
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


# ─── Built-in monitor checks ─────────────────────────────────────────


def check_volume_spike(db_path: str, config: dict) -> MonitorAlert | None:
    """Alert when ticket volume exceeds N× the rolling average.

    Config:
        multiplier: float (default 2.0) — alert if current > avg × multiplier
        window_hours: int (default 24) — look-back window for current count
        baseline_days: int (default 7) — rolling average baseline
    """
    multiplier = config.get("multiplier", 2.0)
    window_hours = config.get("window_hours", 24)
    baseline_days = config.get("baseline_days", 7)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        now = datetime.now()
        window_start = (now - timedelta(hours=window_hours)).isoformat()
        baseline_start = (now - timedelta(days=baseline_days)).isoformat()

        # Current window count
        current = conn.execute(
            "SELECT COUNT(*) FROM support_tickets WHERE created_at >= ?",
            (window_start,),
        ).fetchone()[0]

        # Baseline average (per window_hours period)
        total_baseline = conn.execute(
            "SELECT COUNT(*) FROM support_tickets WHERE created_at >= ?",
            (baseline_start,),
        ).fetchone()[0]

        periods = (baseline_days * 24) / window_hours
        avg_per_period = total_baseline / periods if periods > 0 else 0

        if avg_per_period > 0 and current > avg_per_period * multiplier:
            return MonitorAlert(
                monitor_id="volume_spike",
                severity="warning",
                message=(
                    f"📈 Volume spike: {current} tickets in last {window_hours}h "
                    f"(average: {avg_per_period:.1f}, threshold: {avg_per_period * multiplier:.1f})"
                ),
                data={
                    "current": current,
                    "average": round(avg_per_period, 1),
                    "multiplier": multiplier,
                    "window_hours": window_hours,
                },
            )
        return None
    finally:
        conn.close()


def check_kb_gaps(db_path: str, config: dict) -> MonitorAlert | None:
    """Alert when multiple tickets had no KB match.

    Config:
        min_occurrences: int (default 3) — minimum times a query must appear
        days: int (default 7) — look-back window
    """
    min_occ = config.get("min_occurrences", 3)
    days = config.get("days", 7)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()

        gaps = conn.execute(
            """SELECT kb_query, COUNT(*) as cnt
               FROM support_tickets
               WHERE kb_matched = 0 AND kb_query != '' AND created_at >= ?
               GROUP BY kb_query
               HAVING cnt >= ?
               ORDER BY cnt DESC""",
            (since, min_occ),
        ).fetchall()

        if gaps:
            gap_list = [{"query": r["kb_query"], "count": r["cnt"]} for r in gaps]
            return MonitorAlert(
                monitor_id="kb_gaps",
                severity="info",
                message=(
                    f"📋 KB Gaps: {len(gap_list)} topics asked {min_occ}+ times with no FAQ match\n"
                    + "\n".join(f"  • \"{g['query']}\" ({g['count']}×)" for g in gap_list[:5])
                ),
                data={"gaps": gap_list},
            )
        return None
    finally:
        conn.close()


def check_response_rate_drop(db_path: str, config: dict) -> MonitorAlert | None:
    """Alert when approval rate drops below threshold.

    Config:
        threshold: float (default 0.70) — minimum approval rate
        days: int (default 7) — look-back window
    """
    threshold = config.get("threshold", 0.70)
    days = config.get("days", 7)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()

        total = conn.execute(
            "SELECT COUNT(*) FROM feedback_decisions WHERE created_at >= ?",
            (since,),
        ).fetchone()[0]

        if total < 5:  # Not enough data
            return None

        approved = conn.execute(
            "SELECT COUNT(*) FROM feedback_decisions WHERE created_at >= ? AND human_action = 'approve'",
            (since,),
        ).fetchone()[0]

        rate = approved / total
        if rate < threshold:
            return MonitorAlert(
                monitor_id="response_rate_drop",
                severity="warning",
                message=(
                    f"⚠️ Approval rate dropped to {rate:.0%} "
                    f"(threshold: {threshold:.0%}, last {days} days)\n"
                    f"Total decisions: {total} | Approved: {approved} | Edited/Skipped: {total - approved}"
                ),
                data={"rate": round(rate, 3), "total": total, "approved": approved},
            )
        return None
    finally:
        conn.close()


def check_draft_quality_drift(db_path: str, config: dict) -> MonitorAlert | None:
    """Alert when human edit rate spikes (drafts getting worse).

    Config:
        max_edit_rate: float (default 0.30) — max acceptable edit rate
        days: int (default 7)
    """
    max_edit_rate = config.get("max_edit_rate", 0.30)
    days = config.get("days", 7)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()

        total = conn.execute(
            "SELECT COUNT(*) FROM feedback_decisions WHERE created_at >= ?",
            (since,),
        ).fetchone()[0]

        if total < 5:
            return None

        edits = conn.execute(
            "SELECT COUNT(*) FROM feedback_decisions WHERE created_at >= ? AND human_action = 'edit'",
            (since,),
        ).fetchone()[0]

        edit_rate = edits / total
        if edit_rate > max_edit_rate:
            return MonitorAlert(
                monitor_id="draft_quality_drift",
                severity="warning",
                message=(
                    f"✏️ Draft quality drift: {edit_rate:.0%} of responses edited "
                    f"(threshold: {max_edit_rate:.0%})\n"
                    f"Humans are editing more drafts than expected — prompts may need tuning."
                ),
                data={"edit_rate": round(edit_rate, 3), "total": total, "edits": edits},
            )
        return None
    finally:
        conn.close()


# ─── Monitor registry ─────────────────────────────────────────────────

_MONITOR_CHECKS = {
    "volume_spike": check_volume_spike,
    "kb_gaps": check_kb_gaps,
    "response_rate_drop": check_response_rate_drop,
    "draft_quality_drift": check_draft_quality_drift,
}


# ─── Monitor Engine ───────────────────────────────────────────────────


class MonitorEngine:
    """Runs monitors and collects alerts."""

    def __init__(self, monitors: list[MonitorDef], db_path: str):
        self.monitors = monitors
        self.db_path = db_path
        self._alert_history: list[MonitorAlert] = []

    def run_all(self) -> list[MonitorAlert]:
        """Run all enabled monitors and return alerts."""
        alerts = []
        for monitor in self.monitors:
            if not monitor.enabled:
                continue

            check_fn = _MONITOR_CHECKS.get(monitor.monitor_type)
            if check_fn is None:
                logger.warning(f"Unknown monitor type: {monitor.monitor_type}")
                continue

            try:
                alert = check_fn(self.db_path, monitor.config)
                if alert:
                    alert.monitor_id = monitor.id
                    alerts.append(alert)
                    self._alert_history.append(alert)
                    logger.info(f"Monitor {monitor.id} fired: {alert.severity}")
            except Exception as e:
                logger.error(f"Monitor {monitor.id} failed: {e}")

        return alerts

    def format_alerts(self, alerts: list[MonitorAlert]) -> str:
        """Format alerts for display via channel."""
        if not alerts:
            return "✅ All monitors clear — no alerts."

        lines = [f"🔔 Monitor Alerts ({len(alerts)}):\n"]
        for alert in alerts:
            icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(alert.severity, "🔔")
            lines.append(f"{icon} [{alert.monitor_id}] {alert.message}\n")

        return "\n".join(lines)

    def get_history(self) -> list[dict]:
        """Get alert history for analysis."""
        return [
            {
                "monitor_id": a.monitor_id,
                "severity": a.severity,
                "message": a.message,
                "data": a.data,
                "created_at": a.created_at,
            }
            for a in self._alert_history
        ]


def load_monitors_from_config(config_list: list[dict]) -> list[MonitorDef]:
    """Parse monitor definitions from YAML config."""
    monitors = []
    for item in config_list:
        monitors.append(MonitorDef(
            id=item.get("id", f"monitor-{len(monitors)}"),
            monitor_type=item.get("type", ""),
            cron=item.get("cron", ""),
            config=item.get("config", {}),
            enabled=item.get("enabled", True),
        ))
    return monitors
