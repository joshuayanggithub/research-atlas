"""Organization directory layer.

Turns the raw OpenAlex authorship affiliation strings we already fetched into
evidence-backed department/lab sub-units *within* each configured organization.

This is deliberately conservative (see ``docs/ORGANIZATION_DIRECTORY.md``):

- A sub-unit is only attributed to a paper when a curated, reviewed unit name matches
  a raw affiliation string on an authorship that OpenAlex also resolved to that org's
  institution. This is the confidence-95 "exact, date-valid unit name in the paper's
  raw affiliation" tier. We never infer a unit from topic, embedding, or email domain.
- A parent-org match never implies a child unit. Papers with no matching sub-unit stay
  attributed to the parent only (a valid, necessary "unresolved" state).
"""

from pipeline.directory.units import (
    ORG_UNITS,
    OrgUnit,
    attribute_units,
    extract_unit_keys,
)

__all__ = ["ORG_UNITS", "OrgUnit", "attribute_units", "extract_unit_keys"]
