"""WebChannel — HITL channel backed by the web dashboard.

Thin shim that imports from the dashboard package.
Usage:

    from agency.channels.web import WebChannel
    channel = WebChannel(agent_name="ar-follow-up")

The dashboard API server must be running for HITL to work.
Start it with:
    cd dashboard && uvicorn api.main:app --reload --port 8000
"""

import sys
from pathlib import Path

_DASHBOARD = Path(__file__).resolve().parent.parent.parent.parent / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

from api.web_channel import WebChannel  # noqa: F401

__all__ = ["WebChannel"]
