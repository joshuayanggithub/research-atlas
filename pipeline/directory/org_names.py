"""Curated org-level matching of raw affiliation strings — companies and neolabs only.

WHY THIS EXISTS. COMET's arXiv extraction (D43) gives us an affiliation *string* per author and
a ROR id where its matcher could link one. That linker works well for universities and badly for
companies: across all 2,799,088 rows, Google's ROR (00njsd438) appears **zero** times and
OpenAI's (05wx9n238) zero, while Carnegie Mellon's appears 20,126 times. The strings themselves
are perfectly clear — "Google Research", "Google Brain", "Meta AI", "FAIR at Meta", "NVIDIA" —
so the information is there; only the link is missing.

So this module supplies the missing link for the orgs ROR misses, and ONLY those. Universities
are deliberately absent: they already resolve through ROR at high precision, and matching them by
name would add risk ("Berkeley" is also Lawrence Berkeley National Laboratory, a different
institution) for no gain.

Matching follows the same discipline as `units.py` — reviewed patterns, acronyms only as
standalone uppercase tokens — because this is attribution users are expected to trust. See
docs/ORGANIZATION_DIRECTORY.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class OrgMatcher:
    org_key: str
    patterns: tuple[str, ...] = ()          # case-insensitive
    acronyms: tuple[str, ...] = ()          # standalone UPPERCASE only
    exclude: tuple[str, ...] = ()           # case-insensitive vetoes, checked first


# Ordered: the first matcher that fires wins, so a subsidiary is tested before its parent
# ("Google DeepMind" must resolve to deepmind, not google).
ORG_MATCHERS: tuple[OrgMatcher, ...] = (
    OrgMatcher("deepmind", patterns=(r"\bDeepMind\b",)),
    OrgMatcher(
        "meta",
        # Bare "Meta" is a legitimate affiliation, but "Meta-Learning Lab" is not this company;
        # the lookahead drops the hyphenated compounds without losing the real ones.
        patterns=(r"\bMeta AI\b", r"\bMeta Platforms\b", r"\bMeta Research\b",
                  r"\bFAIR at Meta\b", r"\bFacebook\b", r"\bMeta\b(?!-)"),
        acronyms=("FAIR",),
    ),
    OrgMatcher("msr", patterns=(r"\bMicrosoft\b",)),
    OrgMatcher("openai", patterns=(r"\bOpenAI\b",)),
    OrgMatcher("nvidia", patterns=(r"\bNVIDIA\b", r"\bNvidia\b")),
    OrgMatcher(
        "amazon",
        patterns=(r"\bAmazon\b", r"\bAWS\b"),
        # The river and the rainforest turn up in remote-sensing and ecology papers.
        exclude=(r"Amazon (?:rainforest|forest|basin|River|biome)",),
    ),
    OrgMatcher("ai2", patterns=(r"Allen Institute for (?:AI|Artificial Intelligence)",),
               acronyms=("AI2",)),
    OrgMatcher("redwood", patterns=(r"\bRedwood Research\b",)),
    OrgMatcher("google", patterns=(r"\bGoogle\b",)),
)

_CI: dict[str, re.Pattern] = {}
_CS: dict[str, re.Pattern] = {}
_EX: dict[str, re.Pattern] = {}


def _compiled(m: OrgMatcher) -> tuple[re.Pattern | None, re.Pattern | None, re.Pattern | None]:
    if m.org_key not in _CI:
        _CI[m.org_key] = re.compile("|".join(m.patterns), re.IGNORECASE) if m.patterns else None
        _CS[m.org_key] = (
            re.compile(r"\b(" + "|".join(re.escape(a) for a in m.acronyms) + r")\b")
            if m.acronyms else None
        )
        _EX[m.org_key] = re.compile("|".join(m.exclude), re.IGNORECASE) if m.exclude else None
    return _CI[m.org_key], _CS[m.org_key], _EX[m.org_key]


def org_keys_for(affiliation: str) -> list[str]:
    """Curated org keys this affiliation string attests to (first match per org, ordered)."""
    out: list[str] = []
    for m in ORG_MATCHERS:
        ci, cs, ex = _compiled(m)
        if ex is not None and ex.search(affiliation):
            continue
        if (ci is not None and ci.search(affiliation)) or (cs is not None and cs.search(affiliation)):
            out.append(m.org_key)
    return out
