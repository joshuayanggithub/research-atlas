#!/usr/bin/env python3
"""Export Zotero collections to a Research Atlas reading list.

Run this on the machine where Zotero lives (it reads Zotero's own database), then load the
resulting file in the web app: Filters -> Reading list -> Import.

    python3 tools/zotero_export.py
    python3 tools/zotero_export.py --collection "1. Finished" --collection "2. Understand"
    python3 tools/zotero_export.py --list          # just print the collection names it can see

No dependencies beyond the standard library, and nothing is written to your Zotero data — the
database is copied to a temp file and opened read-only, because Zotero holds a write lock while
it is running and an open handle can otherwise trip its own integrity checks.

WHY THIS FORMAT
---------------
The output is CSL-JSON — the interchange format Zotero, Mendeley, Paperpile, Pandoc and most
of the rest already read and write — inside a small envelope that adds the one thing CSL has no
place for: which list each item came from. A plain CSL-JSON array (Zotero's own
"Export Collection -> CSL JSON") imports fine too; it just arrives without list names.

    {
      "format": "research-atlas/reading-list",
      "version": 1,
      "exported_at": "2026-08-17T16:00:00Z",
      "source": "zotero",
      "lists": [{"name": "1. Finished", "count": 128}],
      "items": [
        {
          "id": "ABCD1234",
          "type": "article",
          "title": "...",
          "author": [{"family": "Xing", "given": "Eliot"}],
          "issued": {"date-parts": [[2025, 3]]},
          "DOI": "10.48550/arXiv.2503.12345",
          "URL": "https://arxiv.org/abs/2503.12345",
          "custom": {"list": "1. Finished", "arxiv_id": "2503.12345", "added": "2026-01-04"}
        }
      ]
    }

`custom` is CSL-JSON's sanctioned home for application data, so the file stays valid CSL for
anything else that reads it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# The read-works collections in this library. Override with --collection.
DEFAULT_COLLECTIONS = ["1. Finished", "2. Understood"]
LOCAL_API = "http://localhost:23119/api"
WEB_API = "https://api.zotero.org"

# Where Zotero keeps its data on each platform, in the order worth trying.
CANDIDATE_DIRS = [
    Path.home() / "Zotero",
    Path.home() / "snap/zotero-snap/common/Zotero",
    Path.home() / ".var/app/org.zotero.Zotero/data/Zotero",
    Path.home() / "Library/Application Support/Zotero",   # macOS (older layouts)
    Path(os.environ.get("APPDATA", "")) / "Zotero/Zotero" if os.environ.get("APPDATA") else None,
]

# Zotero item types that are not papers. Attachments and notes are excluded structurally
# (they have a parent), so this only needs to catch standalone non-works.
SKIP_TYPES = {"attachment", "note", "annotation"}

ARXIV_PATTERNS = [
    re.compile(r"arxiv[:\s/]*([0-9]{4}\.[0-9]{4,5})", re.I),
    re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})", re.I),
    re.compile(r"10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})", re.I),
    # Old-style ids: archive/YYMMNNN, e.g. cond-mat/0501001
    re.compile(r"arxiv[:\s/]*([a-z-]+(?:\.[A-Z]{2})?/[0-9]{7})", re.I),
    re.compile(r"arxiv\.org/(?:abs|pdf)/([a-z-]+(?:\.[A-Z]{2})?/[0-9]{7})", re.I),
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def find_data_dir(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not (p / "zotero.sqlite").exists():
            raise SystemExit(f"no zotero.sqlite in {p}")
        return p
    for cand in CANDIDATE_DIRS:
        if cand and (cand / "zotero.sqlite").exists():
            return cand
    raise SystemExit(
        "could not find your Zotero data directory. Zotero shows it under "
        "Settings -> Advanced -> Files and Folders; pass it with --data-dir."
    )


def extract_arxiv_id(*texts: str | None) -> str:
    for text in texts:
        if not text:
            continue
        for pattern in ARXIV_PATTERNS:
            m = pattern.search(text)
            if m:
                return re.sub(r"v\d+$", "", m.group(1))
    return ""


def read_from_sqlite(data_dir: Path, wanted: list[str]) -> tuple[dict[str, list[dict]], list[str]]:
    """Return ({collection name: [item dicts]}, [every collection name seen])."""
    src = data_dir / "zotero.sqlite"
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "zotero.sqlite"
        shutil.copy2(src, copy)
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row

        collections = {
            row["collectionID"]: (row["collectionName"], row["parentCollectionID"])
            for row in con.execute(
                "SELECT collectionID, collectionName, parentCollectionID FROM collections"
            )
        }
        all_names = sorted({name for name, _ in collections.values()})

        # Descendants count as part of the list: "1. Finished/Robotics" is still finished.
        children: dict[int | None, list[int]] = {}
        for cid, (_, parent) in collections.items():
            children.setdefault(parent, []).append(cid)

        def subtree(root: int) -> list[int]:
            out, stack = [], [root]
            while stack:
                cid = stack.pop()
                out.append(cid)
                stack.extend(children.get(cid, []))
            return out

        deleted = {row[0] for row in con.execute("SELECT itemID FROM deletedItems")}
        # Attachments and notes hang off a parent item; only top-level works are wanted.
        child_items = {
            row[0]
            for row in con.execute(
                "SELECT itemID FROM itemAttachments WHERE parentItemID IS NOT NULL "
                "UNION SELECT itemID FROM itemNotes WHERE parentItemID IS NOT NULL"
            )
        }

        result: dict[str, list[dict]] = {}
        for name in wanted:
            ids = [cid for cid, (cname, _) in collections.items() if cname == name]
            if not ids:
                log(f"  ! no collection named {name!r}")
                continue
            member_ids: set[int] = set()
            for root in ids:
                for cid in subtree(root):
                    member_ids.update(
                        row[0]
                        for row in con.execute(
                            "SELECT itemID FROM collectionItems WHERE collectionID = ?", (cid,)
                        )
                    )
            member_ids -= deleted
            member_ids -= child_items
            result[name] = [_load_item(con, iid) for iid in sorted(member_ids)]
            result[name] = [it for it in result[name] if it]
        con.close()
    return result, all_names


def _load_item(con: sqlite3.Connection, item_id: int) -> dict | None:
    row = con.execute(
        "SELECT i.key, i.dateAdded, t.typeName FROM items i "
        "JOIN itemTypes t ON t.itemTypeID = i.itemTypeID WHERE i.itemID = ?",
        (item_id,),
    ).fetchone()
    if not row or row["typeName"] in SKIP_TYPES:
        return None

    fields = {
        r["fieldName"]: r["value"]
        for r in con.execute(
            "SELECT f.fieldName, v.value FROM itemData d "
            "JOIN fields f ON f.fieldID = d.fieldID "
            "JOIN itemDataValues v ON v.valueID = d.valueID WHERE d.itemID = ?",
            (item_id,),
        )
    }
    creators = [
        {"family": r["lastName"] or "", "given": r["firstName"] or ""}
        for r in con.execute(
            "SELECT c.firstName, c.lastName FROM itemCreators ic "
            "JOIN creators c ON c.creatorID = ic.creatorID "
            "JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID "
            "WHERE ic.itemID = ? AND ct.creatorType IN ('author','contributor') "
            "ORDER BY ic.orderIndex",
            (item_id,),
        )
    ]
    tags = [
        r[0]
        for r in con.execute(
            "SELECT t.name FROM itemTags it JOIN tags t ON t.tagID = it.tagID WHERE it.itemID = ?",
            (item_id,),
        )
    ]
    return _to_csl(row["key"], row["typeName"], row["dateAdded"], fields, creators, tags)


def _to_csl(key: str, item_type: str, added: str, fields: dict, creators: list, tags: list) -> dict:
    date = fields.get("date", "") or ""
    parts: list[int] = []
    m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", date)
    if m:
        parts = [int(g) for g in m.groups() if g]
    arxiv = extract_arxiv_id(
        fields.get("extra"), fields.get("url"), fields.get("DOI"),
        fields.get("archiveID"), fields.get("publicationTitle"), fields.get("repository"),
    )
    item: dict = {
        "id": key,
        "type": "article-journal" if item_type == "journalArticle" else "article",
        "title": (fields.get("title") or "").strip(),
        "author": creators,
    }
    if parts:
        item["issued"] = {"date-parts": [parts]}
    if fields.get("DOI"):
        item["DOI"] = fields["DOI"].strip()
    if fields.get("url"):
        item["URL"] = fields["url"].strip()
    if fields.get("publicationTitle"):
        item["container-title"] = fields["publicationTitle"].strip()
    custom = {"added": (added or "")[:10]}
    if arxiv:
        custom["arxiv_id"] = arxiv
    if tags:
        custom["tags"] = tags
    item["custom"] = custom
    return item


def _web_get(path: str, key: str) -> tuple[object, dict]:
    req = urllib.request.Request(
        f"{WEB_API}{path}",
        headers={"Zotero-API-Key": key, "Zotero-API-Version": "3",
                 "User-Agent": "research-atlas/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r), dict(r.headers)


def read_from_web_api(key: str, wanted: list[str]) -> tuple[dict[str, list[dict]], list[str]]:
    """Zotero's hosted API, for a library that is not on this machine.

    Needs only a read-only API key. Everything is paginated at 100 items, and collections are
    walked into their subcollections for the same reason the SQLite path does: a paper filed
    under "1. Finished/Robotics" is still finished.
    """
    who, _ = _web_get("/keys/current", key)
    uid = who.get("userID")
    if not uid:
        raise SystemExit("API key did not resolve to a user — check that it is still valid")
    log(f"  authenticated as userID {uid} ({who.get('username', '?')})")

    cols: list[dict] = []
    start = 0
    while True:
        page, _ = _web_get(f"/users/{uid}/collections?limit=100&start={start}", key)
        if not page:
            break
        cols.extend(page)
        if len(page) < 100:
            break
        start += 100
    by_key = {c["key"]: c["data"] for c in cols}
    all_names = sorted(d["name"] for d in by_key.values())

    children: dict[str | None, list[str]] = {}
    for ckey, data in by_key.items():
        children.setdefault(data.get("parentCollection") or None, []).append(ckey)

    def subtree(root: str) -> list[str]:
        out, stack = [], [root]
        while stack:
            k = stack.pop()
            out.append(k)
            stack.extend(children.get(k, []))
        return out

    out: dict[str, list[dict]] = {}
    for name in wanted:
        roots = [k for k, d in by_key.items() if d["name"] == name]
        if not roots:
            log(f"  ! no collection named {name!r}")
            continue
        items: list[dict] = []
        for root in roots:
            for ckey in subtree(root):
                start = 0
                while True:
                    # /items/top, not /items: the plain endpoint returns child attachments
                    # and notes as first-class rows, so a library of 30 papers came back as
                    # 71 "items" including "Snapshot", "paper.dvi" and stray comments.
                    payload, _ = _web_get(
                        f"/users/{uid}/collections/{ckey}/items/top?format=csljson"
                        f"&itemType=-attachment&limit=100&start={start}", key)
                    batch = payload.get("items", []) if isinstance(payload, dict) else payload
                    if not batch:
                        break
                    for it in batch:
                        custom = it.setdefault("custom", {})
                        aid = extract_arxiv_id(it.get("DOI"), it.get("URL"), it.get("note"),
                                               it.get("archive_location"), it.get("number"))
                        if aid:
                            custom["arxiv_id"] = aid
                        items.append(it)
                    if len(batch) < 100:
                        break
                    start += 100
        out[name] = items
    return out, all_names


def read_from_local_api(wanted: list[str]) -> tuple[dict[str, list[dict]], list[str]] | None:
    """Zotero 7 exposes a read-only local API when it is running. Preferred when available:
    no file copy, and it reflects unsaved-to-disk state."""
    try:
        with urllib.request.urlopen(f"{LOCAL_API}/users/0/collections?limit=100", timeout=5) as r:
            cols = json.load(r)
    except Exception:
        return None
    names = sorted(c["data"]["name"] for c in cols)
    out: dict[str, list[dict]] = {}
    for name in wanted:
        keys = [c["key"] for c in cols if c["data"]["name"] == name]
        items: list[dict] = []
        for key in keys:
            url = f"{LOCAL_API}/users/0/collections/{key}/items?format=csljson&limit=100"
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    payload = json.load(r)
            except Exception:
                continue
            for it in payload.get("items", payload if isinstance(payload, list) else []):
                custom = it.setdefault("custom", {})
                aid = extract_arxiv_id(it.get("DOI"), it.get("URL"), it.get("note"))
                if aid:
                    custom["arxiv_id"] = aid
                items.append(it)
        out[name] = items
    return out, names


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--collection", action="append", dest="collections",
                    help="collection name (repeatable). Default: '1. Finished', '2. Understood'")
    ap.add_argument("--data-dir", help="Zotero data directory (auto-detected if omitted)")
    ap.add_argument("-o", "--out", default="reading-list.json", help="output path")
    ap.add_argument("--list", action="store_true", help="print collection names and exit")
    ap.add_argument("--api-key", default=os.environ.get("ZOTERO_API_KEY"),
                    help="Zotero Web API key (or set ZOTERO_API_KEY) — use when the library "
                         "is not on this machine. Prefer the env var: an argument is visible "
                         "in the process list to every user on the box.")
    args = ap.parse_args()

    wanted = args.collections or DEFAULT_COLLECTIONS

    if args.api_key:
        via = "Zotero Web API"
        got = read_from_web_api(args.api_key, [] if args.list else wanted)
        by_list, all_names = got
        if args.list:
            log(f"collections in your library ({via}):")
            for name in all_names:
                print(name)
            return
        _emit(by_list, via, args.out)
        return

    via = "local API"
    got = read_from_local_api(wanted if not args.list else [])
    if got is None:
        data_dir = find_data_dir(args.data_dir)
        via = f"database at {data_dir}"
        got = read_from_sqlite(data_dir, [] if args.list else wanted)
    by_list, all_names = got

    if args.list:
        log(f"collections in your library ({via}):")
        for name in all_names:
            print(name)
        return

    _emit(by_list, via, args.out)


def _emit(by_list: dict[str, list[dict]], via: str, out_path: str) -> None:
    log(f"read from {via}")
    seen: set[str] = set()
    items: list[dict] = []
    for name, entries in by_list.items():
        kept = 0
        for it in entries:
            key = it.get("id") or it.get("DOI") or it.get("title")
            if key in seen:      # an item filed under both lists keeps its first (earlier) list
                continue
            seen.add(key)
            it.setdefault("custom", {})["list"] = name
            items.append(it)
            kept += 1
        log(f"  {name}: {kept} items")

    with_arxiv = sum(1 for it in items if it.get("custom", {}).get("arxiv_id"))
    with_doi = sum(1 for it in items if it.get("DOI"))
    log(f"  total {len(items)} items — {with_arxiv} with an arXiv id, {with_doi} with a DOI")
    if items and with_arxiv == 0 and with_doi == 0:
        log("  ! none of these carry an arXiv id or DOI; matching will fall back to titles")

    payload = {
        "format": "research-atlas/reading-list",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "zotero",
        "lists": [{"name": n, "count": sum(1 for i in items
                                           if i.get("custom", {}).get("list") == n)}
                  for n in by_list],
        "items": items,
    }
    Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    log(f"wrote {out_path}")


if __name__ == "__main__":
    main()
