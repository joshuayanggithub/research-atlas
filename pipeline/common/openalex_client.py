"""Minimal OpenAlex client: institution search + cursor-paginated works iteration.

Uses the polite pool (``mailto``) and an optional API key. Retries transient failures
(429/5xx) with exponential backoff. Kept dependency-light (httpx only) so it works
without ``pyalex``.
"""

from __future__ import annotations

from typing import Iterator, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

BASE = "https://api.openalex.org"


def short_id(openalex_url: Optional[str]) -> Optional[str]:
    """'https://openalex.org/W123' -> 'W123'. Also handles bare ids and None."""
    if not openalex_url:
        return None
    return openalex_url.rstrip("/").rsplit("/", 1)[-1]


class OpenAlexClient:
    def __init__(self, mailto: Optional[str] = None, api_key: Optional[str] = None):
        self.mailto = mailto
        self.api_key = api_key
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
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    def _get(self, path: str, params: dict) -> dict:
        r = self._client.get(f"{BASE}{path}", params=self._params(params))
        # Retry on rate-limit / server errors; raise others immediately.
        if r.status_code in (429, 500, 502, 503, 504):
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

    def count_works(self, filter_str: str) -> int:
        data = self._get("/works", {"filter": filter_str, "per-page": 1,
                                    "select": "id"})
        return data.get("meta", {}).get("count", 0)

    def close(self) -> None:
        self._client.close()
