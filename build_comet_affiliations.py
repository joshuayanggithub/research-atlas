"""Join COMET's arXiv author-affiliation extraction onto our corpus.

COMET (cometadata/arxiv-author-affiliations-matched-ror-ids, CC0) ran a distilled Qwen3-8B over
a markdown conversion of all of arXiv and matched the extracted affiliation strings to ROR ids.
D35 established that no metadata API carries affiliations for arXiv preprints — OpenAlex,
Semantic Scholar, arXiv's own API and DataCite all return zero — so this is the only source, and
it is already done and given away.

This maps their ROR ids onto the OpenAlex institution ids our org tree is keyed by, using the
`ror` field already present on 99.7% of our institution registry. ROR ids we have never seen are
reported, not silently dropped: they are institutions absent from our corpus's OpenAlex
affiliations entirely, which is exactly the gap this is meant to close.

Output: data/interim/comet_affiliations.parquet — node_id, institution_ids, raw_affiliations,
plus `unmapped_rors` for the ids we could not bridge.

    uv run python build_comet_affiliations.py
"""
from __future__ import annotations

import json
import time
from collections import Counter

import polars as pl

from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR
from pipeline.directory.org_names import org_keys_for

COMET = "data/comet/affiliations.jsonl"
OUT = INTERIM_DIR / "comet_affiliations.parquet"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    corpus = pl.read_parquet(CORPUS_ACTIVE, columns=["node_id", "arxiv_id", "institution_ids"])
    # COMET keys on "arXiv:2501.00684"; ours are bare and may carry a version suffix.
    aid_to_node = {}
    already = {}
    for nid, aid, insts in zip(corpus["node_id"].to_list(), corpus["arxiv_id"].to_list(),
                               corpus["institution_ids"].to_list()):
        if not aid:
            continue
        key = aid.split("v")[0] if aid[-1].isdigit() and "v" in aid[-3:] else aid
        aid_to_node[key] = nid
        already[nid] = bool(insts)
    log(f"corpus {corpus.height:,} papers | {sum(already.values()):,} already have affiliations")

    registry = json.load(open(INTERIM_DIR / "institutions.json"))
    ror_to_openalex: dict[str, str] = {}
    for oa_id, meta in registry.items():
        ror = (meta or {}).get("ror")
        if ror:
            ror_to_openalex[ror.rstrip("/").rsplit("/", 1)[-1]] = oa_id
    log(f"ROR -> OpenAlex bridge: {len(ror_to_openalex):,} institutions")

    rows_node, rows_inst, rows_raw, rows_orgs = [], [], [], []
    org_hits: Counter[str] = Counter()
    seen = matched = with_ror = 0
    unmapped: Counter[str] = Counter()
    gained = 0
    with open(COMET) as fh:
        for line in fh:
            seen += 1
            if seen % 500_000 == 0:
                log(f"  {seen:,} rows scanned, {matched:,} matched to our corpus")
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            aid = (rec.get("arxiv_id") or "").replace("arXiv:", "").strip()
            node = aid_to_node.get(aid)
            if node is None:
                continue
            matched += 1
            insts: set[str] = set()
            raws: set[str] = set()
            # org_key -> the affiliation strings attesting to it, the same shape s15 produces,
            # so the existing sub-unit extractor works unchanged on this evidence.
            org_aff: dict[str, list[str]] = {}
            for author in rec.get("prediction") or []:
                for aff in author.get("affiliations") or []:
                    text = (aff.get("affiliation") or "").strip()
                    if text:
                        raws.add(text)
                        # Curated company/neolab matching: ROR links universities well but
                        # misses companies entirely (Google's ROR appears 0 times in the whole
                        # dataset), so these are recovered from the string. See org_names.py.
                        for key in org_keys_for(text):
                            org_aff.setdefault(key, []).append(text)
                    ror = aff.get("ror_id")
                    if not ror:
                        continue
                    key = ror.rstrip("/").rsplit("/", 1)[-1]
                    oa = ror_to_openalex.get(key)
                    if oa:
                        insts.add(oa)
                    else:
                        unmapped[key] += 1
            if insts:
                with_ror += 1
                if not already.get(node):
                    gained += 1
            for key in org_aff:
                org_hits[key] += 1
            if insts or raws or org_aff:
                rows_node.append(node)
                rows_inst.append(sorted(insts))
                rows_raw.append(sorted(raws)[:8])
                rows_orgs.append(json.dumps(
                    {k: sorted(set(v)) for k, v in org_aff.items()},
                    ensure_ascii=False, separators=(",", ":")))

    log(f"\nscanned {seen:,} COMET rows")
    log(f"  matched to our corpus       : {matched:,}")
    log(f"  with >=1 ROR we can map     : {with_ror:,}")
    log(f"  papers that GAIN affiliation: {gained:,}  <-- the win")
    log(f"  unmapped ROR ids            : {len(unmapped):,} distinct "
        f"({sum(unmapped.values()):,} mentions)")
    log(f"  most common unmapped        : {unmapped.most_common(5)}")

    log(f"  curated org matches by NAME  : {dict(org_hits.most_common())}")

    pl.DataFrame({
        "node_id": rows_node,
        "institution_ids": rows_inst,
        "raw_affiliations": rows_raw,
        "org_affiliations_json": rows_orgs,
    }).sort("node_id").write_parquet(OUT)
    log(f"wrote {OUT} ({len(rows_node):,} rows) in {(time.time()-t0)/60:.1f} min")


if __name__ == "__main__":
    main()
