"""s01 arXiv mode: resumably harvest OAI-PMH changes after the bulk snapshot.

The 5 GB Cornell/Kaggle JSON snapshot is the fast baseline. OAI-PMH is only the small,
nightly delta, not a per-paper API. Pages are appended durably and later reduced by arXiv
id, so inclusive datestamps and a restarted page sequence are safe.
"""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx

from pipeline.common import log
from pipeline.common.io import read_json, write_json
from pipeline.config import RAW_DIR, Config, ensure_dirs, load_config

OUT = RAW_DIR / "arxiv_oai_updates.jsonl"
CHECKPOINT = RAW_DIR / "arxiv_oai_checkpoint.json"
OAI_NS = "http://www.openarchives.org/OAI/2.0/"
RAW_NS = "http://arxiv.org/OAI/arXivRaw/"


def _text(parent: ET.Element | None, tag: str) -> str | None:
    if parent is None:
        return None
    el = parent.find(f"{{{RAW_NS}}}{tag}")
    return el.text.strip() if el is not None and el.text else None


def _parse_record(el: ET.Element) -> dict | None:
    header = el.find(f"{{{OAI_NS}}}header")
    if header is None:
        return None
    identifier = header.findtext(f"{{{OAI_NS}}}identifier", "")
    arxiv_id = identifier.removeprefix("oai:arXiv.org:")
    datestamp = header.findtext(f"{{{OAI_NS}}}datestamp", "")
    if not arxiv_id:
        return None
    if header.get("status") == "deleted":
        return {"id": arxiv_id, "datestamp": datestamp, "deleted": True}

    raw = el.find(f"{{{OAI_NS}}}metadata/{{{RAW_NS}}}arXivRaw")
    if raw is None:
        return None
    versions = []
    for version in raw.findall(f"{{{RAW_NS}}}version"):
        versions.append({
            "version": version.get("version"),
            "created": _text(version, "date"),
        })
    return {
        "id": _text(raw, "id") or arxiv_id,
        "submitter": _text(raw, "submitter"),
        "authors": _text(raw, "authors") or "",
        "title": _text(raw, "title") or "",
        "comments": _text(raw, "comments"),
        "journal-ref": _text(raw, "journal-ref"),
        "doi": _text(raw, "doi"),
        "report-no": _text(raw, "report-no"),
        "categories": _text(raw, "categories") or "",
        "license": _text(raw, "license"),
        "abstract": _text(raw, "abstract") or "",
        "versions": versions,
        "update_date": datestamp,
        "datestamp": datestamp,
        "deleted": False,
    }


def _parse_page(content: bytes) -> tuple[list[dict], str | None, str]:
    root = ET.fromstring(content)
    error = root.find(f"{{{OAI_NS}}}error")
    if error is not None:
        raise RuntimeError(f"OAI {error.get('code')}: {(error.text or '').strip()}")
    rows = [r for el in root.findall(f".//{{{OAI_NS}}}record") if (r := _parse_record(el))]
    token_el = root.find(f".//{{{OAI_NS}}}resumptionToken")
    token = token_el.text.strip() if token_el is not None and token_el.text else None
    response_date = root.findtext(f"{{{OAI_NS}}}responseDate", "")
    return rows, token, response_date


def _checkpoint(from_date: str) -> dict:
    if CHECKPOINT.exists():
        try:
            state = read_json(CHECKPOINT)
            if state.get("from") == from_date:
                return state
        except (OSError, json.JSONDecodeError):
            pass
    return {"from": from_date, "cycle_from": from_date, "resumption_token": None,
            "pages": 0, "records": 0, "complete": False}


def run(cfg: Config | None = None) -> int:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s01_fetch_arxiv")
    snapshot = Path(cfg.arxiv.snapshot_path)
    if not snapshot.is_absolute():
        snapshot = Path(__file__).resolve().parents[2] / snapshot
    if not snapshot.exists():
        raise FileNotFoundError(
            f"arXiv snapshot missing: {snapshot}. Download Cornell's "
            "arxiv-metadata-oai-snapshot.json before running the pipeline."
        )
    if not cfg.arxiv.oai_enabled:
        log.info(f"snapshot ready ({snapshot.stat().st_size / 1e9:.2f} GB); OAI disabled")
        return 0

    from_date = cfg.arxiv.snapshot_updated_through
    state = _checkpoint(from_date)
    if state.get("complete"):
        today = datetime.now(timezone.utc).date().isoformat()
        last_day = str(state.get("last_response_date") or "")[:10]
        if last_day >= today:
            log.info(f"OAI delta current through {last_day}: {state['records']} records")
            return int(state["records"])
        # Start a new inclusive incremental cycle. Duplicate records are intentionally
        # harmless because s02 reduces the append-only log by arXiv id.
        state["cycle_from"] = last_day or from_date
        state["resumption_token"] = None
        state["complete"] = False

    token = state.get("resumption_token")
    mode = "a" if state.get("pages", 0) and OUT.exists() else "w"
    cycle_from = state.get("cycle_from") or from_date
    log.info(f"harvesting OAI arXivRaw changes from {cycle_from} (resume page "
             f"{state.get('pages', 0)})")
    headers = {"User-Agent": "research-atlas/0.1 (bulk metadata research index)"}
    with httpx.Client(timeout=cfg.arxiv.oai_timeout, headers=headers,
                      follow_redirects=True) as client, OUT.open(mode, encoding="utf-8") as out:
        while True:
            params = ({"verb": "ListRecords", "resumptionToken": token} if token else {
                "verb": "ListRecords", "metadataPrefix": "arXivRaw", "from": cycle_from,
            })
            response = client.get(cfg.arxiv.oai_base_url, params=params)
            response.raise_for_status()
            rows, next_token, response_date = _parse_page(response.content)
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            state["pages"] = int(state.get("pages", 0)) + 1
            state["records"] = int(state.get("records", 0)) + len(rows)
            state["resumption_token"] = next_token
            state["last_response_date"] = response_date
            state["complete"] = next_token is None
            write_json(state, CHECKPOINT)
            log.info(f"OAI page {state['pages']}: +{len(rows)} records "
                     f"({state['records']} total)")
            if not next_token:
                break
            token = next_token
            time.sleep(cfg.arxiv.oai_request_delay)
    return int(state["records"])


if __name__ == "__main__":
    run()
