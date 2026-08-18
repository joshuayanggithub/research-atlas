"""Load and validate curated author-id organization rosters.

Roster attribution is intentionally an exact OpenAlex-author-id join. It does not infer
employment from names, co-authorship, or document text. Those can produce reviewed roster
claims later, but never silently expand membership during a bundle build.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from pipeline.config import REPO_ROOT

ROSTERS_PATH = REPO_ROOT / "org_rosters.yaml"


class RosterMember(BaseModel):
    openalex_id: str
    provenance: Literal["self_asserted", "registry", "publication_history", "coauthorship"]
    valid_from: date | None = None
    valid_to: date | None = None

    @field_validator("openalex_id")
    @classmethod
    def validate_author_id(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("A") or not value[1:].isdigit():
            raise ValueError("must be an OpenAlex author id such as A5037548279")
        return value

    @model_validator(mode="after")
    def validate_dates(self):
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")
        return self


class RosterOrganization(BaseModel):
    key: str
    display_name: str
    organization_id: str
    kind: Literal["neolab"] = "neolab"
    members: list[RosterMember] = Field(default_factory=list)

    @field_validator("organization_id")
    @classmethod
    def validate_org_id(cls, value: str) -> str:
        if not (value.startswith("local:") or value.startswith("https://ror.org/")):
            raise ValueError("must be a local: id or canonical https://ror.org/ id")
        return value


class RostersDoc(BaseModel):
    version: Literal[1]
    organizations: list[RosterOrganization]

    @model_validator(mode="after")
    def validate_uniqueness(self):
        keys = [org.key for org in self.organizations]
        if len(keys) != len(set(keys)):
            raise ValueError("organization roster keys must be unique")
        for org in self.organizations:
            author_ids = [member.openalex_id for member in org.members]
            if len(author_ids) != len(set(author_ids)):
                raise ValueError(f"duplicate author id in roster {org.key}")
        return self


def load_rosters(path: Path = ROSTERS_PATH) -> RostersDoc:
    """Read the reviewed roster file. A missing file is an empty, valid roster."""
    if not path.exists():
        return RostersDoc(version=1, organizations=[])
    return RostersDoc.model_validate(yaml.safe_load(path.read_text()) or {})
