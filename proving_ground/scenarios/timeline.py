"""Timeline — Event scheduling and dispatch for scenario execution.

Events use relative timestamps (T+0, T+2d, T+4h) resolved against the
scenario's SimulatedClock. The TimelineHook fires events at tool-call
boundaries by checking for pending events in pre_tool_use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from agency.agent import Hook, ToolCall

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """A scheduled event in the scenario timeline."""
    offset: timedelta          # When to fire (relative to scenario start)
    action: str                # Action name (add_invoice, mark_paid, advance_clock, etc.)
    params: dict[str, Any] = field(default_factory=dict)
    fired: bool = False


@dataclass
class Timeline:
    """Ordered sequence of events to dispatch during a scenario run."""
    events: list[Event] = field(default_factory=list)

    def add(self, offset: timedelta, action: str, params: dict | None = None) -> None:
        self.events.append(Event(offset=offset, action=action, params=params or {}))
        self.events.sort(key=lambda e: e.offset)

    def pending(self, elapsed: timedelta) -> list[Event]:
        """Return unfired events whose offset <= elapsed time."""
        ready = []
        for event in self.events:
            if not event.fired and event.offset <= elapsed:
                ready.append(event)
        return ready

    def mark_fired(self, event: Event) -> None:
        event.fired = True

    def all_fired(self) -> bool:
        return all(e.fired for e in self.events)

    def reset(self) -> None:
        for e in self.events:
            e.fired = False


def parse_offset(s: str) -> timedelta:
    """Parse a relative time string into a timedelta.

    Supports formats:
        T+0, T+2d, T+4h, T+30m, T+2d4h30m, 0, 2d, 4h, etc.
    """
    s = s.strip()
    if s.startswith("T+"):
        s = s[2:]
    if s == "0":
        return timedelta()

    days = hours = minutes = 0
    current = ""
    for c in s:
        if c.isdigit() or c == ".":
            current += c
        elif c == "d":
            days = float(current)
            current = ""
        elif c == "h":
            hours = float(current)
            current = ""
        elif c == "m":
            minutes = float(current)
            current = ""

    return timedelta(days=days, hours=hours, minutes=minutes)


class TimelineHook(Hook):
    """Hook that dispatches timeline events at tool-call boundaries.

    Sits first in the hook chain. Before each tool call, checks if any
    timeline events are due (based on elapsed iterations mapped to simulated
    time) and dispatches them to the WorldState.

    This avoids modifying Agent.run() while still injecting events.
    """

    def __init__(
        self,
        timeline: Timeline,
        dispatch_fn: Callable[[str, dict], None],
        elapsed_fn: Callable[[], timedelta],
        tracer_fn: Callable[[str, dict], None] | None = None,
    ):
        """
        Args:
            timeline: The event timeline to process.
            dispatch_fn: Called with (action, params) to execute each event.
            elapsed_fn: Returns elapsed simulated time since scenario start.
            tracer_fn: Optional callback to log events to the tracer.
        """
        self._timeline = timeline
        self._dispatch = dispatch_fn
        self._elapsed = elapsed_fn
        self._tracer_fn = tracer_fn

    def pre_tool_use(self, tool_call: ToolCall) -> bool:
        elapsed = self._elapsed()
        pending = self._timeline.pending(elapsed)

        for event in pending:
            logger.info(f"[Timeline] Firing: {event.action} at {event.offset}")
            try:
                self._dispatch(event.action, event.params)
                if self._tracer_fn:
                    self._tracer_fn(event.action, event.params)
            except Exception as e:
                logger.error(f"[Timeline] Event dispatch failed: {event.action} — {e}")
            self._timeline.mark_fired(event)

        return True

    def post_tool_use(self, tool_call: ToolCall, result: Any) -> None:
        pass
