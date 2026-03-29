"""Fault Wrapper — Transparent proxy that injects faults at the provider level.

Wraps any provider and intercepts method calls to simulate API-level
failures. Unlike FaultInjectorHook (which operates at the tool-call level),
this operates at the provider method level — testing how tool functions
handle API errors.

Usage:
    accounting = MockAccountingProvider(clock=clock)
    wrapped = FaultWrapper(accounting)
    wrapped.add_fault("get_overdue_invoices", "error", probability=0.3)
    # Now 30% of get_overdue_invoices calls will raise RuntimeError
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

logger = logging.getLogger(__name__)


class FaultWrapper:
    """Transparent fault-injecting proxy around any object."""

    def __init__(self, target: Any, seed: int | None = None):
        self._target = target
        self._rng = random.Random(seed)
        self._faults: dict[str, list[dict]] = {}  # method_name -> [fault_configs]
        self._active = True

    def add_fault(
        self,
        method: str,
        fault_type: str,
        probability: float = 1.0,
        message: str = "",
        delay_seconds: float = 0,
    ) -> None:
        """Add a fault rule for a specific method."""
        if method not in self._faults:
            self._faults[method] = []
        self._faults[method].append({
            "type": fault_type,
            "probability": probability,
            "message": message or f"Simulated {fault_type}",
            "delay_seconds": delay_seconds,
        })

    def activate(self) -> None:
        self._active = True

    def deactivate(self) -> None:
        self._active = False

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._target, name)

        if not self._active or not callable(attr) or name not in self._faults:
            return attr

        faults = self._faults[name]

        def wrapper(*args, **kwargs):
            for fault in faults:
                if self._rng.random() <= fault["probability"]:
                    logger.warning(f"[FaultWrapper] Injecting {fault['type']} on {name}")
                    if fault["type"] == "error":
                        raise RuntimeError(fault["message"])
                    elif fault["type"] == "timeout":
                        raise TimeoutError(fault["message"])
                    elif fault["type"] == "slow":
                        time.sleep(fault["delay_seconds"])
                    elif fault["type"] == "empty":
                        return [] if "list" in str(type(attr)) else {}
            return attr(*args, **kwargs)

        return wrapper
