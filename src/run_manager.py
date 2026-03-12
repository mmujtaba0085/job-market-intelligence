"""
src/run_manager.py
──────────────────
Manages run-level identity, timing, and observability.

CHANGES (per requirements):
- Added source_stats: dict tracking per-source fetched/inserted/deduped counts
- Added record_source_jobs() to track stats per source and add to sources_used only if fetched > 0
- Updated to_dict() to include source_stats in output

Each pipeline run gets:
  - A unique RUN_ID (uuid4)
  - A started_at timestamp
  - Counts tracked during the run (jobs_fetched, inserted, deduped, skills, metrics)
  - Per-source statistics in source_stats
  - Error samples collected
  - A run_summary.json written to outputs/{market_id}/{YYYY-WW}/

Usage:
    run = RunContext(market_id="ai_ml_global", week="2026-09")
    run.record_source_jobs("remotive", fetched=12, inserted=7, deduped=5)
    run.record_skills(1840)
    run.record_metrics(94)
    run.add_error("jsearch: rate limit at page 4")
    run.finish()   # writes run_summary.json
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUTS_DIR

logger = logging.getLogger(__name__)

_MAX_ERROR_SAMPLES = 10


@dataclass
class RunContext:
    market_id: str
    week: str           # e.g. "2026-09"

    # Auto-generated at construction
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str = ""

    # Pipeline counters
    sources_attempted: int = 0  # New: total sources tried
    sources_succeeded: int = 0  # New: sources that fetched > 0 jobs
    sources_used: list[str] = field(default_factory=list)
    source_stats: dict[str, dict[str, int]] = field(default_factory=dict)  # per-source stats
    jobs_fetched: int = 0
    jobs_inserted: int = 0
    jobs_deduped: int = 0
    skills_extracted: int = 0
    metrics_written: int = 0
    
    # Remote type breakdown
    remote_breakdown: dict[str, int] = field(default_factory=dict)  # New: {remote: X, hybrid: Y, on-site: Z}

    # Error tracking
    errors_count: int = 0
    error_samples: list[str] = field(default_factory=list)

    def record_jobs(self, fetched: int, inserted: int, deduped: int) -> None:
        self.jobs_fetched += fetched
        self.jobs_inserted += inserted
        self.jobs_deduped += deduped

    def record_source_jobs(self, source_id: str, fetched: int, inserted: int, deduped: int) -> None:
        """Record per-source job stats and add to sources_used only if fetched > 0."""
        if source_id not in self.source_stats:
            self.source_stats[source_id] = {"fetched": 0, "inserted": 0, "deduped": 0}
        
        self.source_stats[source_id]["fetched"] += fetched
        self.source_stats[source_id]["inserted"] += inserted
        self.source_stats[source_id]["deduped"] += deduped
        
        # Only add to sources_used if this source actually fetched jobs
        if fetched > 0 and source_id not in self.sources_used:
            self.sources_used.append(source_id)
            self.sources_succeeded += 1
    
    def record_source_attempted(self) -> None:
        """Increment sources_attempted counter."""
        self.sources_attempted += 1
    
    def record_remote_breakdown(self, remote_counts: dict[str, int]) -> None:
        """Record remote type breakdown from deduplicator/normalizer."""
        self.remote_breakdown = remote_counts

    def record_skills(self, count: int) -> None:
        self.skills_extracted += count

    def record_metrics(self, count: int) -> None:
        self.metrics_written += count

    def add_source(self, source_id: str) -> None:
        if source_id not in self.sources_used:
            self.sources_used.append(source_id)

    def add_error(self, message: str) -> None:
        self.errors_count += 1
        if len(self.error_samples) < _MAX_ERROR_SAMPLES:
            self.error_samples.append(message)
        logger.error("[run_manager] Error recorded: %s", message)

    def finish(self) -> Path:
        """
        Stamp finished_at and write run_summary.json.
        Returns the path to the written file.
        """
        self.finished_at = datetime.now(timezone.utc).isoformat()
        output_path = self._summary_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

        logger.info(
            "[run_manager] run_summary.json written → %s (errors=%d)",
            output_path, self.errors_count,
        )
        return output_path

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "market_id": self.market_id,
            "week": self.week,
            "sources_attempted": self.sources_attempted,
            "sources_succeeded": self.sources_succeeded,
            "sources_used": self.sources_used,
            "source_stats": self.source_stats,
            "jobs_fetched": self.jobs_fetched,
            "jobs_inserted": self.jobs_inserted,
            "jobs_deduped": self.jobs_deduped,
            "remote_breakdown": self.remote_breakdown,
            "skills_extracted": self.skills_extracted,
            "metrics_written": self.metrics_written,
            "errors_count": self.errors_count,
            "error_samples": self.error_samples,
        }

    def _summary_path(self) -> Path:
        return OUTPUTS_DIR / self.market_id / self.week / "run_summary.json"
