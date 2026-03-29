"""World State — Registry of all mock providers with snapshot/restore and event dispatch.

The WorldState holds all mock providers (accounting, email, etc.), the
SimulatedClock, and provides:
- Provider registration and lookup
- State snapshot/restore for replay
- Event dispatch (maps action strings to provider methods)
"""

from __future__ import annotations

import copy
import logging
from datetime import timedelta
from typing import Any

from agency.tools.email.mock import MockProvider
from proving_ground.providers.accounting_mock import MockAccountingProvider
from proving_ground.scenarios.clock import SimulatedClock

logger = logging.getLogger(__name__)


class WorldState:
    """Registry of all providers and simulation state.

    Usage:
        world = WorldState(clock)
        world.register_accounting(mock_accounting)
        world.register_email(mock_email)
        world.dispatch("add_invoice", {...})
        snap = world.snapshot()
        world.restore(snap)
    """

    def __init__(self, clock: SimulatedClock):
        self.clock = clock
        self._start_time = clock.now()
        self._accounting: MockAccountingProvider | None = None
        self._email: MockProvider | None = None
        self._providers: dict[str, Any] = {}

    def register_accounting(self, provider: MockAccountingProvider) -> None:
        self._accounting = provider
        self._providers["accounting"] = provider

    def register_email(self, provider: MockProvider) -> None:
        self._email = provider
        self._providers["email"] = provider

    def register(self, name: str, provider: Any) -> None:
        self._providers[name] = provider

    def get(self, name: str) -> Any:
        return self._providers.get(name)

    def elapsed(self) -> timedelta:
        """Time elapsed since scenario start."""
        return self.clock.now() - self._start_time

    # ----- Event dispatch -----

    _DISPATCH_MAP = {
        "add_invoice": "_do_add_invoice",
        "mark_paid": "_do_mark_paid",
        "mark_disputed": "_do_mark_disputed",
        "remove_invoice": "_do_remove_invoice",
        "add_inbox_email": "_do_add_inbox_email",
        "advance_clock": "_do_advance_clock",
    }

    def dispatch(self, action: str, params: dict[str, Any]) -> None:
        """Dispatch a scenario event to the appropriate provider."""
        method_name = self._DISPATCH_MAP.get(action)
        if method_name:
            getattr(self, method_name)(params)
        else:
            logger.warning(f"[WorldState] Unknown action: {action}")

    def _do_add_invoice(self, params: dict) -> None:
        if not self._accounting:
            return
        from proving_ground.providers.base import Invoice
        inv = Invoice(**params)
        self._accounting.add_invoice(inv)

    def _do_mark_paid(self, params: dict) -> None:
        if not self._accounting:
            return
        self._accounting.mark_paid(
            params["invoice_number"],
            amount=params.get("amount"),
        )

    def _do_mark_disputed(self, params: dict) -> None:
        if not self._accounting:
            return
        self._accounting.mark_disputed(params["invoice_number"])

    def _do_remove_invoice(self, params: dict) -> None:
        if not self._accounting:
            return
        self._accounting.remove_invoice(params["invoice_number"])

    def _do_add_inbox_email(self, params: dict) -> None:
        if not self._email:
            return
        from agency.tools.email.interface import EmailMessage
        from datetime import datetime, timezone
        params.setdefault("date", datetime.now(timezone.utc))
        msg = EmailMessage(**params)
        self._email.seed_inbox([msg])

    def _do_advance_clock(self, params: dict) -> None:
        self.clock.advance(**params)

    # ----- Snapshot/restore -----

    def snapshot(self) -> dict:
        """Deep copy of all provider state."""
        snap: dict[str, Any] = {
            "clock": self.clock.now().isoformat(),
        }
        if self._accounting:
            snap["accounting"] = self._accounting.snapshot()
        return snap

    def restore(self, snap: dict) -> None:
        """Restore all providers from a snapshot."""
        from datetime import datetime, timezone
        if "clock" in snap:
            self.clock.set(datetime.fromisoformat(snap["clock"]))
        if "accounting" in snap and self._accounting:
            self._accounting.restore(snap["accounting"])
