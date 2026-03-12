"""
src/collectors/base_collector.py
────────────────────────────────
Abstract base class that all collectors must subclass.

Enforces:
  - Rate limiting via time.sleep
  - robots.txt compliance check
  - Source allowlist enforcement
  - @retry via tenacity for transient API failures
  - Fault isolation: collect() never raises; returns empty list on failure
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from tenacity import retry, stop_after_attempt, wait_exponential, RetryError

from config.sources import SOURCES_BY_ID
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """
    All concrete collectors must:
      1. Set self.source_id matching a key in ALLOWED_SOURCES
      2. Implement _fetch_raw(market) → list[JobRaw]
    """

    source_id: str = ""   # override in subclass

    def __init__(self) -> None:
        self._source_cfg = SOURCES_BY_ID.get(self.source_id)
        if not self._source_cfg:
            raise ValueError(
                f"Collector source_id '{self.source_id}' is not in ALLOWED_SOURCES. "
                "Register it in config/sources.py before use."
            )
        if not self._source_cfg.get("enabled", True):
            raise ValueError(f"Source '{self.source_id}' is disabled in ALLOWED_SOURCES.")

        self._rate_limit_delay = 60.0 / self._source_cfg["rate_limit_per_minute"]
        self._last_request_at: float = 0.0

    # ── Public entry point ───────────────────────────────────────────────────

    def collect(self, market: dict) -> list[JobRaw]:
        """
        Run collection for one market. Never raises.
        Returns empty list if the source is blocked or errors occur.
        """
        # Compliance gate: robots.txt
        if not self._source_cfg.get("robots_txt_allowed", False):
            logger.warning(
                "[%s] robots.txt disallows automated access — skipping.",
                self.source_id,
            )
            return []

        try:
            results = self._fetch_raw(market)
            logger.info(
                "[%s] Collected %d raw jobs for market '%s'.",
                self.source_id, len(results), market["market_id"],
            )
            return results
        except RetryError as exc:
            logger.error(
                "[%s] All retries exhausted for market '%s': %s",
                self.source_id, market["market_id"], exc,
            )
            return []
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[%s] Unexpected error for market '%s': %s",
                self.source_id, market["market_id"], exc, exc_info=True,
            )
            return []

    # ── Rate limiting ────────────────────────────────────────────────────────

    def _wait(self) -> None:
        """Sleep just enough to respect rate_limit_per_minute."""
        elapsed = time.monotonic() - self._last_request_at
        sleep_for = self._rate_limit_delay - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)
        self._last_request_at = time.monotonic()

    # ── Retry decorator (applied by subclasses on their _fetch_raw) ──────────

    @staticmethod
    def _retry_decorator():
        return retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            reraise=True,
        )

    # ── Subclass contract ────────────────────────────────────────────────────

    @abstractmethod
    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """
        Must be implemented by each concrete collector.
        May raise — exceptions are caught by collect().
        """

    # ── Shared helper ────────────────────────────────────────────────────────

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
