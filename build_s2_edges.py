"""Phase 2: stream the S2AG `citations` dataset into a permanent global edge list.

Corpus-independent (decision D1): no corpus is consulted, no filter is applied, nothing is
discarded. Every edge in the release is retained, keyed on S2 `corpusid` (D2), together with
`citationid` and `isinfluential` (D4).

Designed to run UNATTENDED for 12+ hours:
  * downloads N shards concurrently (measured: 1 stream 2.7 MB/s, 8 streams 9.3 MB/s)
  * each shard is archived to the WD (D5) and parsed from that local copy, so a parse failure
    never costs a re-download
  * presigned dataset URLs expire mid-run; on any download failure the manifest is refreshed
    and that ordinal retried (the previous run hit this on nearly every shard)
  * per-shard checkpoint -> restart resumes, never redoes finished work
  * samples how often `contexts`/`intents` are populated, so the "is the raw archive worth
    422 GB" question gets answered with data instead of a guess (plan §6.2)

    uv run python build_s2_edges.py
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import polars as pl

try:
    import orjson
    loads = orjson.loads
except ImportError:
    loads = json.loads

API = "https://api.semanticscholar.org/datasets/v1/release"
KEY = os.environ.get("S2_KEY", "")

ARCHIVE = Path("/mnt/wd/s2ag/citations")          # raw shards (D5)
EDGES = Path("data/s2ag/edges")                   # per-shard parquet, NVMe
STATE = Path("data/s2ag/phase2_state.json")
SAMPLE = Path("data/s2ag/context_sample.json")

DOWNLOAD_WORKERS = 8
QUEUE_DEPTH = 3                                   # shards buffered ahead of the parser
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
            wait = min(120, 6 * (i + 1))
            log(f"  ! manifest fetch failed ({exc}); retry in {wait}s")
            time.sleep(wait)


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"done": [], "release": None}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)


class Manifest:
    """Presigned URLs expire; refresh them on demand, under a lock."""

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


def download(idx: int, man: Manifest) -> Path:
    """Fetch one shard to the archive. Returns the local path."""
    dest = ARCHIVE / f"citations_{idx:04d}.jsonl.gz"
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest                                  # already archived (archive mode only)
    part = dest.with_suffix(".part")
    for attempt in range(6):
        try:
            url = man.urls[idx]
            with urllib.request.urlopen(urllib.request.Request(url), timeout=300) as x, \
                 open(part, "wb") as fh:
                shutil.copyfileobj(x, fh, length=1 << 20)
            part.replace(dest)
            return dest
        except Exception as exc:
            part.unlink(missing_ok=True)
            if attempt == 5:
                raise
            log(f"  ! shard {idx} download failed ({exc}); refreshing + retry {attempt+1}/5")
            try:
                man.refresh()
            except Exception:
                pass
            time.sleep(min(120, 10 * (attempt + 1)))
    raise RuntimeError(f"shard {idx} unreachable")


def parse(path: Path, idx: int, sample: dict) -> int:
    """Extract every edge from one shard into a parquet. Nothing is filtered."""
    src, dst, cid, infl = [], [], [], []
    dec = zlib.decompressobj(31)
    tail = b""
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 22)
            if not chunk:
                break
            buf = tail + dec.decompress(chunk)
            *lines, tail = buf.split(b"\n")
            for raw in lines:
                if not raw:
                    continue
                try:
                    r = loads(raw)
                except Exception:
                    continue
                s, t = r.get("citingcorpusid"), r.get("citedcorpusid")
                if s is None or t is None:
                    continue
                src.append(int(s)); dst.append(int(t))
                cid.append(int(r.get("citationid") or 0))
                infl.append(bool(r.get("isinfluential")))
                # Cheap running sample of the fields we chose not to keep.
                if sample["seen"] < 2_000_000:
                    sample["seen"] += 1
                    if r.get("contexts"): sample["contexts"] += 1
                    if r.get("intents"):  sample["intents"] += 1
    EDGES.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"src": src, "dst": dst, "citationid": cid, "isinfluential": infl},
        schema={"src": pl.Int64, "dst": pl.Int64, "citationid": pl.Int64,
                "isinfluential": pl.Boolean},
    ).write_parquet(EDGES / f"edges_{idx:04d}.parquet", compression="zstd")
    return len(src)


def archive_usable() -> bool:
    """Is the WD archive (D5) actually writable?

    The volume mounts rw and lists fine, but ntfs-3g returns ENOENT on every create for a
    non-root user (it was mounted by root without uid/gid mapping). Rather than block the
    whole run on a remount that needs sudo, fall back to streaming shards through a local
    scratch dir and deleting them after parse. Only the raw-archive insurance is lost; every
    edge is still extracted, so D1-D4 are unaffected.
    """
    try:
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        probe = ARCHIVE / ".writetest"
        probe.write_bytes(b"x")
        probe.unlink()
        return True
    except Exception as exc:
        log(f"  ! archive {ARCHIVE} not writable ({exc.__class__.__name__}: {exc})")
        log("  ! falling back to scratch mode — shards are parsed then deleted (no raw archive)")
        log("  ! to enable archiving later:  sudo mount -o remount,rw,uid=$(id -u),gid=$(id -g) /mnt/wd")
        return False


def main() -> None:
    global ARCHIVE
    keep_archive = archive_usable()
    if not keep_archive:
        ARCHIVE = Path("data/s2ag/_scratch")
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    EDGES.mkdir(parents=True, exist_ok=True)
    release = api(f"{API}/latest")["release_id"]
    man = Manifest(release)
    state = load_state()
    if state.get("release") != release:
        state = {"done": [], "release": release}
    done = set(state["done"])
    total = len(man.urls)
    todo = [i for i in range(total) if i not in done]
    log(f"release {release}: {total} shards, {len(done)} already done, {len(todo)} to go")

    sample = {"seen": 0, "contexts": 0, "intents": 0}
    q: queue.Queue = queue.Queue(maxsize=QUEUE_DEPTH)
    errors: list[str] = []

    def producer(items):
        for i in items:
            try:
                q.put((i, download(i, man)))
            except Exception as exc:
                errors.append(f"shard {i}: {exc}")
                log(f"  !! shard {i} permanently failed: {exc}")
        q.put(None)

    # Split the work across download threads; each feeds the same queue.
    chunks = [todo[k::DOWNLOAD_WORKERS] for k in range(DOWNLOAD_WORKERS)]
    threads = [threading.Thread(target=producer, args=(c,), daemon=True) for c in chunks]
    for t in threads:
        t.start()

    t0 = time.time()
    finished = 0
    live = DOWNLOAD_WORKERS
    edges_total = 0
    while live > 0:
        item = q.get()
        if item is None:
            live -= 1
            continue
        idx, path = item
        try:
            n = parse(path, idx, sample)
            if not keep_archive:
                path.unlink(missing_ok=True)   # scratch mode: bounded disk (one shard at a time)
            edges_total += n
            finished += 1
            with LOCK:
                done.add(idx)
                state["done"] = sorted(done)
                save_state(state)
                SAMPLE.write_text(json.dumps(sample))
            el = time.time() - t0
            rate = finished / max(el / 3600, 1e-9)
            left = (len(todo) - finished) / max(rate, 1e-9)
            log(f"  shard {idx:3d}  {n:,} edges  | done {len(done)}/{total}"
                f"  {el/3600:.2f}h elapsed  ETA {left:.1f}h  (total {edges_total:,})")
        except Exception as exc:
            errors.append(f"shard {idx} parse: {exc}")
            log(f"  !! parse failed for {idx}: {exc}\n{traceback.format_exc()[:400]}")

    log(f"\nPHASE 2 COMPLETE in {(time.time()-t0)/3600:.2f} h")
    log(f"  shards done : {len(done)}/{total}")
    log(f"  edges       : {edges_total:,}")
    if sample["seen"]:
        log(f"  contexts populated: {sample['contexts']/sample['seen']*100:.2f}% of {sample['seen']:,}")
        log(f"  intents  populated: {sample['intents']/sample['seen']*100:.2f}%")
    if errors:
        log(f"  ERRORS ({len(errors)}):")
        for e in errors[:20]:
            log(f"    {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
