"""
src/publisher/manual_export.py
───────────────────────────────
Default publisher: Mode A — manual copy-paste workflow.

All output files are already written to outputs/{market_id}/{YYYY-WW}/ by
the report generators. This module just logs what to do next + confirms
the file checklist is complete.

No external calls. No automation. Works in DRY_RUN and live mode equally.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_EXPECTED_FILES = [
    "report.md",
    "top_skills.csv",
    "growth_skills.csv",
    "charts.json",
    "run_summary.json",
]


def publish(output_dir: Path, market_id: str, week: str) -> bool:
    """
    Verify all expected output files exist and log publishing instructions.
    Returns True if all files are present.
    """
    missing = [f for f in _EXPECTED_FILES if not (output_dir / f).exists()]

    if missing:
        logger.error(
            "[publisher] Output check FAILED for %s/%s — missing: %s",
            market_id, week, missing,
        )
        return False

    report_path = output_dir / "report.md"

    logger.info(
        "\n"
        "════════════════════════════════════════════════════\n"
        "  ✅ WEEK %s — %s READY TO PUBLISH\n"
        "════════════════════════════════════════════════════\n"
        "  📄 Report:  %s\n"
        "\n"
        "  📋 Next steps (Mode A — Manual Publish):\n"
        "     1. Open the report file above\n"
        "     2. Copy the content\n"
        "     3. Paste into Substack editor\n"
        "     4. Schedule or publish\n"
        "════════════════════════════════════════════════════",
        week, market_id, report_path,
    )
    return True
