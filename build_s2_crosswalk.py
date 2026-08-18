"""Phase 1: stream the S2AG `papers` dataset -> arxiv<->corpusid crosswalk + paper table.

Corpus-independent by design (decision D1): nothing here knows or cares which papers are in
Research Atlas. `papers.externalids.ArXiv` gives the crosswalk directly, so this replaces the
~5,400 batch-API requests the old path needed (D6).
"""
from __future__ import annotations
import io, json, os, sys, time, zlib, urllib.request, urllib.error
from pathlib import Path
import polars as pl

try:
    import orjson                      # 3.0x faster than stdlib (measured) - D9
    loads = orjson.loads
except ImportError:
    loads = json.loads

OUT = Path("data/s2ag"); OUT.mkdir(parents=True, exist_ok=True)
KEY = os.environ.get("S2_KEY", "")
API = "https://api.semanticscholar.org/datasets/v1/release"

def api(url, tries=8):
    for i in range(tries):
        r = urllib.request.Request(url)
        if KEY: r.add_header("x-api-key", KEY)
        try:
            with urllib.request.urlopen(r, timeout=120) as x: return json.loads(x.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < tries-1: time.sleep(6*(i+1)); continue
            raise
        except Exception:
            if i < tries-1: time.sleep(6*(i+1)); continue
            raise

def stream_lines(url, tries=5):
    """Yield decoded JSONL lines from a gzipped shard without landing it on disk."""
    for attempt in range(tries):
        try:
            dec = zlib.decompressobj(31); tail = b""
            with urllib.request.urlopen(urllib.request.Request(url), timeout=300) as x:
                while True:
                    chunk = x.read(1 << 20)
                    if not chunk: break
                    buf = tail + dec.decompress(chunk)
                    *lines, tail = buf.split(b"\n")
                    for l in lines:
                        if l.strip(): yield l
            if tail.strip(): yield tail
            return
        except Exception as e:
            if attempt == tries-1: raise
            print(f"  ! shard read failed ({e}); retrying", flush=True)
            time.sleep(5*(attempt+1))

def main():
    rid = api(f"{API}/latest")["release_id"]
    urls = api(f"{API}/{rid}/dataset/papers")["files"]
    print(f"release {rid}: papers dataset has {len(urls)} shards", flush=True)
    # Accumulating all ~237M papers in Python lists OOM-killed the first run (78 GB box;
    # ints alone are ~34 GB before strings). The arXiv crosswalk is small enough to hold
    # (3.1M rows), but the full paper table must be written per shard and merged lazily.
    ax_ids, ax_corpus = [], []
    shard_dir = OUT / "papers_shards"; shard_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time(); n = 0
    for i, url in enumerate(urls, 1):
        p_corpus, p_year, p_refs, p_cites, p_title, p_venue = [], [], [], [], [], []
        for raw in stream_lines(url):
            try: r = loads(raw)
            except Exception: continue
            cid = r.get("corpusid")
            if cid is None: continue
            n += 1
            ext = r.get("externalids") or {}
            ax = ext.get("ArXiv")
            if ax:
                ax_ids.append(str(ax)); ax_corpus.append(int(cid))
            p_corpus.append(int(cid))
            p_year.append(r.get("year") or 0)
            p_refs.append(r.get("referencecount") or 0)
            p_cites.append(r.get("citationcount") or 0)
            p_title.append((r.get("title") or "")[:300])
            p_venue.append((r.get("venue") or "")[:120])
        # Flush this shard's papers and drop the lists before the next shard.
        pl.DataFrame({"corpusid": p_corpus, "year": p_year, "referencecount": p_refs,
                      "citationcount": p_cites, "title": p_title, "venue": p_venue,
                      }).write_parquet(shard_dir / f"papers_{i:04d}.parquet", compression="zstd")
        del p_corpus, p_year, p_refs, p_cites, p_title, p_venue
        el = time.time()-t0
        print(f"  shard {i}/{len(urls)}  papers={n:,}  arxiv={len(ax_ids):,}  "
              f"{el/60:.1f} min  ({n/max(el,1):,.0f} rec/s)", flush=True)
    pl.DataFrame({"arxiv_id": ax_ids, "corpusid": ax_corpus}).write_parquet(OUT/"crosswalk.parquet")
    # Lazy streaming merge — never materialises all 237M rows at once.
    pl.scan_parquet(str(shard_dir / "papers_*.parquet")).sink_parquet(
        OUT/"papers.parquet", compression="zstd")
    print(f"\nDONE in {(time.time()-t0)/3600:.2f} h")
    print(f"  papers    : {n:,}")
    print(f"  arxiv rows: {len(ax_ids):,}")
    print(f"  wrote {OUT/'crosswalk.parquet'} and {OUT/'papers.parquet'}")

if __name__ == "__main__":
    main()
