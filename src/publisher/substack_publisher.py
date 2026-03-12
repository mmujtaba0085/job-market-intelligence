"""
src/publisher/substack_publisher.py
─────────────────────────────────────
⚠️  PLACEHOLDER ONLY — No active implementation.

Automated Substack posting is NOT assumed as a feature.
This module exists so orchestrator imports don't break when Mode B
is eventually wired up.

This will only be implemented if:
  - An official Substack API or supported integration becomes available
  - Session/cookie automation is explicitly accepted as a known risk

DO NOT add any session replay, cookie injection, or browser automation here.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def publish_draft(report_md_path: Path, market_id: str, week: str) -> bool:
    """
    Placeholder. Does nothing, returns False to signal "not implemented".
    """
    logger.warning(
        "[substack_publisher] Automated Substack publishing is not implemented. "
        "Use manual_export.publish() (Mode A) instead."
    )
    return False
