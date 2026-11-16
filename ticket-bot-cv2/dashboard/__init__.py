"""Florida State Dashboard - web control surface for the ticket bot."""

from __future__ import annotations

from pathlib import Path

DASHBOARD_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = DASHBOARD_ROOT.parent
STATIC_ROOT = DASHBOARD_ROOT / "static"
