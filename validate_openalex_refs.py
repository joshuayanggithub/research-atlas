"""Sample-verify OpenAlex reference counts against S2 across the MAG boundary."""
import os, json, time, urllib.request, urllib.error, random
import polars as pl, numpy as np

KEY = os.environ.get("S2_KEY", "")
def batch(ids, tries=6):
    body = json.dumps({"ids": ids}).encode()
    for i in range(tries):
        r = urllib.request.Request(
            "https://api.semanticscholar.org/graph/v1/paper/batch?fields=referenceCount,citationCount",
            data=body, method="POST")
        r.add_header("Content-Type", "application/json")
        if KEY: r.add_header("x-api-key", KEY)
        try:
            with urllib.request.urlopen(r, timeout=90) as x: return json.loads(x.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < tries-1: time.sleep(3*(i+1)); continue
            return None
        except Exception:
            if i < tries-1: time.sleep(3); continue
            return None

a = pl.read_parquet("/tmp/research-atlas-historical-2015-2024/data/interim/corpus.parquet")
m = a.filter(pl.col("openalex_id").is_not_null())
oa = m["openalex_referenced_works"].list.len().fill_null(0).to_numpy()
yr = np.array([int(str(d)[:4]) if d else 0 for d in m["publication_date"].to_list()])
ax = m["arxiv_id"].to_list()
doi = [str(d or "").lower() for d in m["openalex_doi"].to_list()]
pub = np.array([bool(d) and "10.48550/arxiv" not in d for d in doi])

random.seed(11)
print(f"{'cohort':26s} {'n':>5s} {'meanOA':>8s} {'meanS2':>8s} {'OA/S2':>7s} {'OA>=80%ofS2':>12s}")
for label, mask in [
    ("2015-2021 arXiv-only", (yr<=2021) & ~pub),
    ("2015-2021 published",  (yr<=2021) & pub),
    ("2022-2024 arXiv-only", (yr>=2022) & ~pub),
    ("2022-2024 published",  (yr>=2022) & pub),
]:
    idx = np.flatnonzero(mask)
    if len(idx) == 0: continue
    pick = random.sample(list(idx), min(500, len(idx)))
    ids = [f"ARXIV:{ax[i]}" for i in pick]
    oav, s2v = [], []
    for s in range(0, len(ids), 500):
        res = batch(ids[s:s+500])
        if not res: continue
        for i, rec in zip(pick[s:s+500], res):
            if isinstance(rec, dict) and isinstance(rec.get("referenceCount"), int):
                oav.append(int(oa[i])); s2v.append(rec["referenceCount"])
        time.sleep(1.3)
    if not s2v: print(f"{label:26s} no data"); continue
    oav, s2v = np.array(oav), np.array(s2v)
    ok = s2v > 0
    ratio = oav[ok].sum()/s2v[ok].sum() if ok.sum() else 0
    frac = ((oav[ok] >= 0.8*s2v[ok]).mean()*100) if ok.sum() else 0
    print(f"{label:26s} {len(s2v):5d} {oav.mean():8.2f} {s2v.mean():8.2f} {ratio:7.2f} {frac:11.1f}%")
