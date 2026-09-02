"""Measure the generation half: refusal, citation validity, support, latency.

Answers the three questions a portfolio RAG demo cannot:
  - does it refuse when it should, and does the similarity gate or the model do that work?
  - are its citations real? (mechanically checkable, so there is no excuse not to check)
  - do cited papers actually support the sentences citing them?

    uv run python tools/eval_generation.py
"""
from __future__ import annotations

import json
import statistics as stats

import httpx

from tools.calibrate_refusal import IN_DOMAIN, OUT_OF_DOMAIN

SERVICE = "http://localhost:8000/ask"
REFUSAL_MARKER = "do not answer this"


def run(queries: list[str], label: str) -> dict:
    gate, model_refused, answered = 0, 0, 0
    validity, support, gen_ms, invalid_total = [], [], [], 0
    for q in queries:
        r = httpx.post(SERVICE, json={"q": q, "k": 8}, timeout=300.0).json()
        if r.get("refused"):
            gate += 1
            continue
        answer = r.get("answer", "")
        if REFUSAL_MARKER in answer.lower():
            model_refused += 1
            continue
        answered += 1
        c = r["citations"]
        if c["validity_rate"] is not None:
            validity.append(c["validity_rate"])
        invalid_total += len(c["invalid"])
        if r["support"]["mean_cosine"] is not None:
            support.append(r["support"]["mean_cosine"])
        gen_ms.append(r["timing_ms"]["generate"])

    n = len(queries)
    print(f"\n{label}  (n={n})")
    print(f"  refused by similarity gate : {gate}/{n}")
    print(f"  refused by the model       : {model_refused}/{n}")
    print(f"  answered                   : {answered}/{n}")
    if validity:
        print(f"  citation validity rate     : {stats.mean(validity):.3f} "
              f"({invalid_total} fabricated citations total)")
    if support:
        print(f"  support cosine             : mean {stats.mean(support):.3f} "
              f"min {min(support):.3f}")
    if gen_ms:
        print(f"  generate latency           : median {stats.median(gen_ms):.0f} ms")
    return {"gate": gate, "model": model_refused, "answered": answered}


def main() -> None:
    print(f"service: {SERVICE}")
    a = run(IN_DOMAIN, "IN-DOMAIN research questions")
    b = run(OUT_OF_DOMAIN, "OUT-OF-DOMAIN questions (should not be answered)")
    print("\nSUMMARY")
    print(f"  false refusals on real questions : {a['gate'] + a['model']}/{len(IN_DOMAIN)}")
    leaked = b["answered"]
    print(f"  out-of-domain answered anyway    : {leaked}/{len(OUT_OF_DOMAIN)}")
    print(f"  of OOD refusals, gate caught {b['gate']}, model caught {b['model']}")
    print(json.dumps({"in_domain": a, "out_of_domain": b}))


if __name__ == "__main__":
    main()
