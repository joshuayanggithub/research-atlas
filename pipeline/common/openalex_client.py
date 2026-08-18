"""Minimal OpenAlex client: institution search + cursor-paginated works iteration.

Uses the polite pool (``mailto``) and an optional API key. Retries transient failures
(429/5xx) with exponential backoff, honoring the ``Retry-After`` header, and throttles
proactively so long pulls (hundreds of thousands of works) don't trip the rate limit and
lose progress. Kept dependency-light (httpx only) so it works without ``pyalex``.
"""

from __future__ import annotations

import time
import threading
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
                 page_pause: float = 0.0, api_keys: Optional[list[str]] = None):
        self.mailto = mailto
        configured_keys = api_keys or ([api_key] if api_key else [])
        self._api_keys = list(dict.fromkeys(key for key in configured_keys if key))
        self._api_key_index = 0
        self._api_key_lock = threading.Lock()
        # Small inter-page sleep to stay under the rate limit on long pulls. The polite pool
        # allows ~10 req/s; a pause is cheap insurance against a 429 wall mid-fetch. With an
        # api_key the limit is higher, so callers can pass 0.
        self.page_pause = page_pause
        self._client = httpx.Client(
            timeout=60.0,
            headers={"User-Agent": f"research-visualizer (mailto:{mailto or 'anon'})"},
        )

    @property
    def api_key(self) -> Optional[str]:
        return self._api_keys[self._api_key_index] if self._api_keys else None

    def _key_for_request(self) -> tuple[Optional[str], int]:
        with self._api_key_lock:
            return self.api_key, self._api_key_index

    def _rotate_api_key(self, request_index: int) -> bool:
        """Advance once when the key that made this request hits its daily budget."""
        with self._api_key_lock:
            if self._api_key_index != request_index:
                return True  # another concurrent request already rotated the pool
            if request_index + 1 >= len(self._api_keys):
                return False
            self._api_key_index += 1
            return True

    def _params(self, extra: dict, api_key: Optional[str] = None) -> dict:
        p = dict(extra)
        if self.mailto:
            p["mailto"] = self.mailto
        if api_key:
            p["api_key"] = api_key
        return p

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        wait=wait_exponential(multiplier=2, min=3, max=_MAX_BACKOFF),
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        reraise=True,
    )
    def _get(self, path: str, params: dict) -> dict:
        api_key, request_index = self._key_for_request()
        r = self._client.get(f"{BASE}{path}", params=self._params(params, api_key))
        if r.status_code == 429:
            # Distinguish transient throttling from daily-quota exhaustion. OpenAlex sends
            # the seconds-until-reset in Retry-After / x-ratelimit-reset; if it's hours away,
            # retrying is pointless — raise QuotaExhausted so the fetch stops and keeps its
            # progress. Otherwise sleep the short interval (capped) and let tenacity retry.
            reset = r.headers.get("retry-after") or r.headers.get("x-ratelimit-reset")
            wait_s = float(reset) if reset and reset.replace(".", "", 1).isdigit() else 5.0
            if wait_s > _QUOTA_WALL_SECONDS:
                if self._rotate_api_key(request_index):
                    log.warn("OpenAlex daily quota exhausted; switched to fallback API key")
                    return self._get(path, params)
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

    def list_works(self, filter_str: str, select: str, per_page: int = 100) -> list[dict]:
        """Return every work matching a bounded filter, following cursor pagination.

        This is intended for batched exact-id filters (at most 100 requested identifiers),
        not whole-corpus downloads. Cursoring still matters because duplicate OpenAlex work
        records can make 100 requested arXiv ids return more than 100 rows.
        """
        rows: list[dict] = []
        cursor = "*"
        while cursor:
            data = self._get(
                "/works",
                {
                    "filter": filter_str,
                    "select": select,
                    "per_page": min(per_page, 100),
                    "cursor": cursor,
                },
            )
            results = data.get("results", [])
            if not results:
                break
            rows.extend(results)
            cursor = data.get("meta", {}).get("next_cursor")
            if cursor and self.page_pause:
                time.sleep(self.page_pause)
        return rows

    def close(self) -> None:
        self._client.close()
