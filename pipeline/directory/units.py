"""Curated, evidence-backed department/lab sub-units per configured organization.

Each :class:`OrgUnit` is matched against the *raw affiliation strings* OpenAlex attached
to an authorship. Matching is intentionally strict to keep attribution defensible:

- ``full_patterns`` are descriptive names ("Robotics Institute", "Facebook AI Research").
  They match case-insensitively because affiliation casing is inconsistent.
- ``acronyms`` are short, ambiguous tokens ("FAIR", "SAIL", "EECS"). They match ONLY as
  standalone UPPERCASE tokens, because lowercase "fair"/"sail" occur in ordinary prose and
  would create false attributions. Real affiliation strings write these units in capitals.

Units are ordered most-specific first within an org; the first matching unit wins, so a
lab (Biorobotics) is preferred over its parent school (SCS) when both would match.

Adding a unit is a reviewed, curated act — never an automatic merge from name similarity.
See ``docs/ORGANIZATION_DIRECTORY.md`` for the confidence tiers and the non-goals this
respects (no topic/embedding inference; a parent match never implies a child).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrgUnit:
    """A department/lab within a parent org, resolved from raw affiliation strings."""

    key: str  # stable local key, e.g. "cmu-ri"
    name: str  # display name, e.g. "Robotics Institute"
    unit_type: str  # school | department | institute | lab | research_division
    full_patterns: tuple[str, ...] = ()  # case-insensitive descriptive names
    acronyms: tuple[str, ...] = ()  # case-sensitive UPPERCASE standalone tokens

    def _compiled(self) -> tuple[re.Pattern, re.Pattern | None]:
        full = "|".join(self.full_patterns) if self.full_patterns else r"(?!x)x"
        ci = re.compile(full, re.IGNORECASE)
        cs = None
        if self.acronyms:
            # \b around an UPPERCASE token; case-sensitive so "fair" (prose) never matches.
            cs = re.compile(r"\b(" + "|".join(re.escape(a) for a in self.acronyms) + r")\b")
        return ci, cs

    def matches(self, affiliation: str) -> bool:
        ci, cs = _cache_for(self)
        if self.full_patterns and ci.search(affiliation):
            return True
        if cs is not None and cs.search(affiliation):
            return True
        return False


_COMPILED: dict[str, tuple[re.Pattern, re.Pattern | None]] = {}


def _cache_for(unit: OrgUnit) -> tuple[re.Pattern, re.Pattern | None]:
    hit = _COMPILED.get(unit.key)
    if hit is None:
        hit = unit._compiled()
        _COMPILED[unit.key] = hit
    return hit


# Ordered most-specific -> least-specific per org. Curated from the raw affiliation-string
# distribution of the active corpus; every entry corresponds to observed, named evidence.
ORG_UNITS: dict[str, list[OrgUnit]] = {
    "cmu": [
        OrgUnit("cmu-ri", "Robotics Institute", "institute",
                full_patterns=(r"robotics institute",)),
        OrgUnit("cmu-lti", "Language Technologies Institute", "institute",
                full_patterns=(r"language technolog",), acronyms=("LTI",)),
        OrgUnit("cmu-mld", "Machine Learning Department", "department",
                full_patterns=(r"machine learning department",
                               r"department of machine learning"), acronyms=("MLD",)),
        OrgUnit("cmu-hcii", "Human-Computer Interaction Institute", "institute",
                full_patterns=(r"human-?computer interaction institute",),
                acronyms=("HCII",)),
        OrgUnit("cmu-sei", "Software Engineering Institute", "institute",
                full_patterns=(r"software engineering institute",)),
        OrgUnit("cmu-csd", "Computer Science Department", "department",
                full_patterns=(r"computer science department",
                               r"department of computer science")),
        OrgUnit("cmu-ece", "Electrical & Computer Engineering", "department",
                full_patterns=(r"electrical and computer engineering",
                               r"electrical & computer engineering")),
        OrgUnit("cmu-scs", "School of Computer Science", "school",
                full_patterns=(r"school of computer science",)),
    ],
    "mit": [
        OrgUnit("mit-csail", "CSAIL", "lab",
                full_patterns=(r"computer science and artificial intelligence lab",),
                acronyms=("CSAIL",)),
        OrgUnit("mit-rle", "Research Laboratory of Electronics", "lab",
                full_patterns=(r"research laboratory of electronics",), acronyms=("RLE",)),
        OrgUnit("mit-lids", "Lab for Information & Decision Systems", "lab",
                full_patterns=(r"information and decision systems",), acronyms=("LIDS",)),
        OrgUnit("mit-medialab", "MIT Media Lab", "lab",
                full_patterns=(r"media lab",)),
        OrgUnit("mit-lincoln", "Lincoln Laboratory", "lab",
                full_patterns=(r"lincoln laborator",)),
        OrgUnit("mit-eecs", "EECS", "department",
                full_patterns=(r"electrical engineering and computer science",),
                acronyms=("EECS",)),
    ],
    "meta": [
        OrgUnit("meta-fair", "FAIR (Facebook AI Research)", "lab",
                full_patterns=(r"facebook ai research", r"fundamental ai research"),
                acronyms=("FAIR",)),
        OrgUnit("meta-reality-labs", "Reality Labs", "research_division",
                full_patterns=(r"reality labs",)),
        OrgUnit("meta-ai", "Meta AI", "research_division",
                # "Facebook AI" WITHOUT "Research"; FAIR is handled first and wins.
                full_patterns=(r"\bmeta ai\b", r"facebook ai(?! research)")),
    ],
    "berkeley": [
        OrgUnit("berkeley-bair", "BAIR (Berkeley AI Research)", "lab",
                full_patterns=(r"berkeley artificial intelligence",), acronyms=("BAIR",)),
        OrgUnit("berkeley-eecs", "EECS", "department",
                full_patterns=(r"electrical engineering and computer",), acronyms=("EECS",)),
        OrgUnit("berkeley-stat", "Statistics", "department",
                full_patterns=(r"department of statistics",)),
    ],
    "stanford": [
        OrgUnit("stanford-sail", "SAIL (Stanford AI Lab)", "lab",
                full_patterns=(r"stanford artificial intelligence lab",), acronyms=("SAIL",)),
        OrgUnit("stanford-hai", "HAI (Human-Centered AI)", "institute",
                full_patterns=(r"human-centered artificial intelligence",)),
        OrgUnit("stanford-cs", "Computer Science", "department",
                full_patterns=(r"computer science",)),
        OrgUnit("stanford-ee", "Electrical Engineering", "department",
                full_patterns=(r"electrical engineering",)),
        OrgUnit("stanford-stat", "Statistics", "department",
                full_patterns=(r"department of statistics",)),
    ],
    "msr": [
        OrgUnit("msr-asia", "Microsoft Research Asia", "site",
                full_patterns=(r"research asia",)),
        OrgUnit("msr-india", "Microsoft Research India", "site",
                full_patterns=(r"research india", r"research lab.{0,8}bangalore")),
        OrgUnit("msr-cambridge", "Microsoft Research Cambridge", "site",
                full_patterns=(r"research.{0,20}cambridge",)),
        OrgUnit("msr-redmond", "Microsoft Research Redmond", "site",
                full_patterns=(r"redmond",)),
    ],
    "deepmind": [
        OrgUnit("deepmind-core", "DeepMind", "research_division",
                full_patterns=(r"deepmind",)),
    ],
}


def extract_unit_keys(org_key: str, affiliations: list[str]) -> list[str]:
    """Return the sub-unit keys evidenced by any of ``affiliations`` for ``org_key``.

    First matching unit per affiliation wins (units are ordered specific->general), so a
    lab is preferred over its parent school. Returns a de-duplicated, order-preserving list.
    Empty when no curated unit name is present (the paper stays attributed to the parent).
    """
    units = ORG_UNITS.get(org_key)
    if not units:
        return []
    found: list[str] = []
    for aff in affiliations:
        if not aff:
            continue
        for unit in units:
            if unit.matches(aff):
                if unit.key not in found:
                    found.append(unit.key)
                break  # most-specific unit for this string only
    return found


def attribute_units(
    org_affiliations: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Map ``{org_key: [raw affiliation strings]}`` to ``{org_key: [unit_key, ...]}``.

    Only orgs present in the input are considered, and only their own curated units are
    matched, so a Stanford affiliation string can never be attributed to a CMU unit.
    """
    out: dict[str, list[str]] = {}
    for org_key, affs in org_affiliations.items():
        keys = extract_unit_keys(org_key, affs)
        if keys:
            out[org_key] = keys
    return out
