"""Minimal OpenAlex client: institution search + cursor-paginated works iteration.

Uses the polite pool (``mailto``) and an optional API key. Retries transient failures
(429/5xx) with exponential backoff, honoring the ``Retry-After`` header, and throttles
proactively so long pulls (hundreds of thousands of works) don't trip the rate limit and
lose progress. Kept dependency-light (httpx only) so it works without ``pyalex``.
"""

from __future__ import annotations

import time
from typing import Iterator, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from pipeline.common import log

BASE = "https://api.openalex.org"

# A 429 during a long unauthenticated pull is transient throttling, not a hard failure, so
# retry patiently: without honoring this the whole fetch dies and discards everything
# already streamed to disk. ~10 attempts with a long ceiling rides out multi-minute limits.
_MAX_ATTEMPTS = 8
_MAX_BACKOFF = 120.0
# A 429 with Retry-After beyond this is a daily-quota exhaustion, not transient throttling —
# retrying is futile (the reset is hours away). Raise a distinct error so the caller stops
# cleanly and keeps everything fetched so far, instead of hammering the wall.
_QUOTA_WALL_SECONDS = 600.0


class QuotaExhausted(RuntimeError):
    """OpenAlex daily request quota is spent; the reset is `reset_seconds` away."""

    def __init__(self, reset_seconds: float):
        self.reset_seconds = reset_seconds
        hours = reset_seconds / 3600.0
        super().__init__(f"OpenAlex daily quota exhausted; resets in ~{hours:.1f}h")


def short_id(openalex_url: Optional[str]) -> Optional[str]:
    """'https://openalex.org/W123' -> 'W123'. Also handles bare ids and None."""
    if not openalex_url:
        return None
    return openalex_url.rstrip("/").rsplit("/", 1)[-1]


class OpenAlexClient:
    def __init__(self, mailto: Optional[str] = None, api_key: Optional[str] = None,
                 page_pause: float = 0.0):
        self.mailto = mailto
        self.api_key = api_key
        # Small inter-page sleep to stay under the rate limit on long pulls. The polite pool
        # allows ~10 req/s; a pause is cheap insurance against a 429 wall mid-fetch. With an
        # api_key the limit is higher, so callers can pass 0.
        self.page_pause = page_pause
        self._client = httpx.Client(
            timeout=60.0,
            headers={"User-Agent": f"research-visualizer (mailto:{mailto or 'anon'})"},
        )

    def _params(self, extra: dict) -> dict:
        p = dict(extra)
        if self.mailto:
            p["mailto"] = self.mailto
        if self.api_key:
            p["api_key"] = self.api_key
        return p

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        wait=wait_exponential(multiplier=2, min=3, max=_MAX_BACKOFF),
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        reraise=True,
    )
    def _get(self, path: str, params: dict) -> dict:
        r = self._client.get(f"{BASE}{path}", params=self._params(params))
        if r.status_code == 429:
            # Distinguish transient throttling from daily-quota exhaustion. OpenAlex sends
            # the seconds-until-reset in Retry-After / x-ratelimit-reset; if it's hours away,
            # retrying is pointless — raise QuotaExhausted so the fetch stops and keeps its
            # progress. Otherwise sleep the short interval (capped) and let tenacity retry.
            reset = r.headers.get("retry-after") or r.headers.get("x-ratelimit-reset")
            wait_s = float(reset) if reset and reset.replace(".", "", 1).isdigit() else 5.0
            if wait_s > _QUOTA_WALL_SECONDS:
                raise QuotaExhausted(wait_s)
            log.warn(f"OpenAlex 429 rate-limited; sleeping {wait_s:.0f}s before retry")
            time.sleep(min(wait_s, _MAX_BACKOFF))
            r.raise_for_status()
        if r.status_code in (500, 502, 503, 504):
            r.raise_for_status()
        r.raise_for_status()
        return r.json()

    def resolve_institution(self, search: str) -> Optional[dict]:
        """Return the best-matching institution object for a display-name search."""
        data = self._get(
            "/institutions",
            {"search": search, "per-page": 5,
             "select": "id,display_name,ror,type,country_code,works_count,lineage"},
        )
        results = data.get("results", [])
        if not results:
            return None
        # OpenAlex ranks by relevance; the top hit is almost always right for a full name.
        return results[0]

    def iter_works(
        self,
        filter_str: str,
        select: str,
        per_page: int = 200,
        max_records: Optional[int] = None,
        start_cursor: str = "*",
    ) -> Iterator[dict]:
        """Yield work objects for a filter, following cursor pagination.

        Yields raw OpenAlex work dicts. Stops at ``max_records`` if given.
        """
        cursor = start_cursor
        emitted = 0
        while cursor:
            data = self._get(
                "/works",
                {"filter": filter_str, "select": select,
                 "per-page": per_page, "cursor": cursor},
            )
            results = data.get("results", [])
            if not results:
                break
            for w in results:
                yield w
                emitted += 1
                if max_records is not None and emitted >= max_records:
                    return
            cursor = data.get("meta", {}).get("next_cursor")
            if cursor and self.page_pause:
                time.sleep(self.page_pause)

    def count_works(self, filter_str: str) -> int:
        data = self._get("/works", {"filter": filter_str, "per-page": 1,
                                    "select": "id"})
        return data.get("meta", {}).get("count", 0)

    def close(self) -> None:
        self._client.close()
