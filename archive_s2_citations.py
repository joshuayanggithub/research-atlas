"""Archive the raw S2AG `citations` shards so citation contexts/intents are never lost again.

Why this exists: phase 2 extracted every EDGE (5,089,547,933) but dropped `contexts` and
`intents`, which the in-run sampler then measured at **35.03% populated** — roughly 1.78
billion records carrying the citing sentence and an intent label. Those fields exist only in
the raw JSONL, and the shards were deleted, so recovering them needs a re-download.

Design notes:
  * RELEASE IS PINNED to the same release the edge graph was built from. A newer release would
    not correspond to the existing refs/cited_by artifacts.
  * Archives only — it does NOT re-extract edges. Those are already built and verified.
  * Measures real context/intent byte sizes while streaming, so the "extract contexts into
    parquet vs keep raw" decision is made from data rather than an estimate.
  * Same robustness as phase 2: parallel downloads, presigned-URL refresh, per-shard
    checkpoint, fully resumable.

    uv run python archive_s2_citations.py
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import orjson
    loads = orjson.loads
except ImportError:
    loads = json.loads

API = "https://api.semanticscholar.org/datasets/v1/release"
KEY = os.environ.get("S2_KEY", "")
RELEASE = "2026-08-11"          # PINNED — must match the release the edge graph came from

ARCHIVE = Path("/mnt/wd/s2ag/citations")
STATE = Path("/mnt/wd/s2ag/archive_state.json")
STATS = Path("/mnt/wd/s2ag/context_stats.json")

WORKERS = 8
LOCK = threading.Lock()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def api(url: str, tries: int = 10):
    for i in range(tries):
        r = urllib.request.Request(url)
        if KEY:
            r.add_header("x-api-key", KEY)
        try:
            with urllib.request.urlopen(r, timeout=120) as x:
                return json.loads(x.read())
        except Exception as exc:
            if i == tries - 1:
                raise
            log(f"  ! manifest fetch failed ({exc}); retry")
            time.sleep(min(120, 6 * (i + 1)))


class Manifest:
    def __init__(self, release: str):
        self.release = release
        self.urls = api(f"{API}/{release}/dataset/citations")["files"]
        self.lock = threading.Lock()

    def refresh(self) -> None:
        with self.lock:
            fresh = api(f"{API}/{self.release}/dataset/citations")["files"]
            if len(fresh) != len(self.urls):
                raise RuntimeError("shard count changed mid-run")
            self.urls = fresh
            log("  ! refreshed presigned URLs")


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"done": [], "release": RELEASE}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)


def sample_contexts(path: Path, stats: dict, limit: int = 40_000) -> None:
    """Measure real context/intent sizes from an archived shard (bounded work)."""
    seen = 0
    try:
        with gzip.open(path, "rb") as fh:
            for raw in fh:
                if seen >= limit:
                    break
                seen += 1
                try:
                    r = loads(raw)
                except Exception:
                    continue
                with LOCK:
                    stats["seen"] += 1
                    ctx = r.get("contexts")
                    if ctx:
                        stats["with_contexts"] += 1
                        stats["context_bytes"] += sum(len(c) for c in ctx if isinstance(c, str))
                        stats["context_items"] += len(ctx)
                    it = r.get("intents")
                    if it:
                        stats["with_intents"] += 1
                        stats["intent_bytes"] += sum(len(i) for i in it if isinstance(i, str))
    except Exception as exc:
        log(f"  ! sampling failed on {path.name}: {exc}")


def fetch(idx: int, man: Manifest, stats: dict) -> None:
    dest = ARCHIVE / f"citations_{idx:04d}.jsonl.gz"
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return
    part = dest.with_suffix(".part")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(man.urls[idx]), timeout=300) as x, \
                 open(part, "wb") as fh:
                shutil.copyfileobj(x, fh, length=1 << 20)
            part.replace(dest)
            if stats["seen"] < 400_000:      # only sample the first handful of shards
                sample_contexts(dest, stats)
            return
        except Exception as exc:
            part.unlink(missing_ok=True)
            if attempt == 5:
                raise
            log(f"  ! shard {idx} failed ({exc}); refresh + retry {attempt+1}/5")
            try:
                man.refresh()
            except Exception:
                pass
            time.sleep(min(120, 10 * (attempt + 1)))


def main() -> None:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    man = Manifest(RELEASE)
    state = load_state()
    done = set(state.get("done", []))
    total = len(man.urls)
    todo = [i for i in range(total) if i not in done]
    log(f"release {RELEASE} (PINNED): {total} shards, {len(done)} archived, {len(todo)} to go")
    free = shutil.disk_usage(ARCHIVE).free / 1e9
    log(f"free on archive volume: {free:.0f} GB (need ~422 GB)")

    stats = {"seen": 0, "with_contexts": 0, "with_intents": 0,
             "context_bytes": 0, "context_items": 0, "intent_bytes": 0}
    t0 = time.time()
    counter = {"n": 0}

    def worker(items):
        for i in items:
            try:
                fetch(i, man, stats)
                with LOCK:
                    done.add(i)
                    state["done"] = sorted(done)
                    save_state(state)
                    STATS.write_text(json.dumps(stats))
                    counter["n"] += 1
                    n = counter["n"]
                el = time.time() - t0
                rate = n / max(el / 3600, 1e-9)
                log(f"  archived {i:3d}  | {len(done)}/{total}  {el/3600:.2f}h  "
                    f"ETA {(len(todo)-n)/max(rate,1e-9):.1f}h  "
                    f"free {shutil.disk_usage(ARCHIVE).free/1e9:.0f} GB")
            except Exception as exc:
                log(f"  !! shard {i} permanently failed: {exc}")

    chunks = [todo[k::WORKERS] for k in range(WORKERS)]
    threads = [threading.Thread(target=worker, args=(c,), daemon=True) for c in chunks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    log(f"\nARCHIVE COMPLETE in {(time.time()-t0)/3600:.2f} h — {len(done)}/{total} shards")
    if stats["seen"]:
        s = stats
        pct = s["with_contexts"] / s["seen"] * 100
        avg = s["context_bytes"] / max(s["with_contexts"], 1)
        log(f"  sampled {s['seen']:,} records")
        log(f"  contexts populated : {pct:.2f}%  avg {avg:,.0f} B/record")
        log(f"  intents populated  : {s['with_intents']/s['seen']*100:.2f}%")
        est = 5_089_547_933 * (s["context_bytes"] + s["intent_bytes"]) / s["seen"]
        log(f"  -> extracting ALL contexts+intents would be ~{est/1e9:,.0f} GB")


if __name__ == "__main__":
    main()
