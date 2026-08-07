# Organization Directory and Research Attribution

Status: proposed target architecture

This document defines how the visualizer will represent universities, companies,
independent research laboratories, their internal units, researchers, and paper
attributions. It replaces the current assumption that one configured OpenAlex
institution is equivalent to one user-facing research organization.

## Summary

The visualizer needs two graphs that are related but independent:

1. A research graph: papers, authors, topics, citations, and semantic similarity.
2. A directory graph: organizations, internal units, researchers, affiliations, and
   evidence-backed paper attribution.

No upstream provider is the canonical directory. CSRankings is the strongest available
academic roster and venue-taxonomy input, OpenAlex and ROR provide broad institutional
identifiers, and official organization pages and curated overrides provide internal-unit
detail. Provider records are normalized into stable local entities with provenance.

The paper corpus must be discovered independently of organizations. Organizations become
many-to-many filters over the corpus rather than predicates that determine which papers
exist.

## Current failure

The current bundle has seven flat organization entries. Each entry is a union of configured
OpenAlex institution IDs and a list of paper node IDs. It has no unit hierarchy, temporal
membership, attribution method, confidence, or evidence.

The Meta entry demonstrates why that model is unsafe. The active corpus contains 1,976
works associated with two broad Meta OpenAlex institution IDs. A high-precision scan of
the raw affiliation strings in the fetched OpenAlex records finds:

| Most-specific explicit label | Works |
|---|---:|
| FAIR / Facebook AI Research | 425 |
| Facebook AI, without "Research" | 194 |
| Reality Labs | 158 |
| Meta AI | 9 |
| Generic or unresolved Meta/Facebook | 1,190 |

These buckets use a deterministic priority order and are a diagnostic, not final ground
truth. They prove that labeling all 1,976 papers as "Meta AI (FAIR)" is incorrect. The
current entity is a Meta parent aggregate; FAIR must be a narrower child attribution.

## Goals

- Cover academic institutions, companies, government organizations, nonprofits, and
  independent research labs through one model.
- Represent schools, departments, institutes, research divisions, labs, teams, and sites.
- Support organizations with multiple parents or cross-affiliations.
- Preserve name changes, reorganizations, and researcher moves over time.
- Attribute papers at the most specific defensible level while retaining evidence.
- Let a parent filter include descendants without double-counting papers.
- Make every provider mapping, merge, and inferred attribution auditable.
- Keep the current offline pipeline and static-client architecture at MVP scale.
- Allow provider-specific data to be excluded from distributable builds when licensing
  does not permit redistribution.

## Non-goals

- Inferring an internal team solely from a paper's topic or embedding neighborhood.
- Treating a product, model name, or temporary project as an organization without
  organizational evidence.
- Producing a probabilistically complete employee directory.
- Making CSRankings the only organization source.
- Shipping millions of papers in one browser bundle; the scale design remains tiled.

## Core decisions

### 1. Stable local identities

Every canonical entity receives a stable string key such as `meta`, `fair`, `cmu`, or
`cmu-mld`. The key does not encode the current parent path, so a reorganization does not
change identity. Build artifacts additionally assign dense integer IDs for compact joins.

External identifiers are attributes, not primary keys. ROR, OpenAlex, DBLP, ORCID, and
provider-specific IDs can be corrected or merged without changing the local identity.

### 2. A typed DAG, not a strict tree

`part_of` relationships define rollup ancestry and must be acyclic. A unit may have more
than one valid parent, as with a university institute shared across departments. One
relationship may be marked `primary_for_display` so the UI can render a predictable tree.

Other relationship types do not affect filter rollups:

- `affiliated_with`
- `successor_of`
- `predecessor_of`
- `located_at`
- `operated_by`

### 3. Time is part of identity resolution

Aliases, organization relationships, and researcher affiliations carry inclusive
`valid_from` and exclusive `valid_to` dates when known. A paper dated 2021 is resolved
against the directory state in 2021, not the latest corporate or university structure.
Unknown dates remain explicit rather than being fabricated.

### 4. Store direct claims; derive rollups

The pipeline stores only direct, evidence-backed paper-to-organization attributions.
Ancestor membership is computed from the organization DAG. This avoids duplicated facts,
allows hierarchy corrections without re-resolving evidence, and makes direct versus
inherited counts distinguishable.

### 5. Confidence is an evidence tier

`confidence` is an ordinal score from 0 to 100, not a statistical probability. Default
filters include attributions at or above 80. Lower scores remain available for review and
an optional inclusive mode, but they are not silently presented as fact.

### 6. Curated and semantic taxonomies remain separate

CSRankings areas and conferences provide a curated disciplinary facet. OpenAlex topics,
embedding neighborhoods, and nested semantic communities provide a content-derived facet.
A paper can have both. Venue categories must not replace fine semantic topics, and semantic
clusters must not be presented as organizational units.

## Canonical data model

The source-of-truth records live in the offline directory layer. Browser artifacts are a
compact projection of these records.

### Organization

```text
Organization
  key: string                         stable local identity
  name: string
  sector: university | company | nonprofit | government | independent_lab
  unit_type: organization | school | department | institute |
             research_division | lab | team | site
  status: active | historical | proposed
  homepage: string?
  country_code: string?
  external_ids: map<string, list<string>>  materialized crosswalk summary
  valid_from: date?
  valid_to: date?
```

`proposed` records may be used in review tooling but are not emitted to the production
filter. `external_ids` is a convenience projection of the auditable mappings below; one
canonical organization may legitimately have several IDs from one provider.

### External identifier mapping

```text
ExternalIdentifier
  entity_type: organization | researcher
  entity_key: string
  namespace: ror | openalex | wikidata | dblp | orcid |
             semantic_scholar | provider_specific
  value: string
  primary: bool
  valid_from: date?
  valid_to: date?
  evidence_ids: list<string>
```

Uniqueness is enforced on `(namespace, value, overlapping validity interval)`, not on the
provider namespace alone. Provider merges and corrections therefore retain their source
records and dates rather than overwriting a string in place.

### Organization relationship

```text
OrganizationRelation
  parent_key: string
  child_key: string
  relation_type: part_of | affiliated_with | successor_of |
                 predecessor_of | located_at | operated_by
  primary_for_display: bool
  valid_from: date?
  valid_to: date?
  evidence_ids: list<string>
```

Only active `part_of` edges participate in ancestor rollups for a paper's publication date.

### Organization alias

```text
OrganizationAlias
  organization_key: string
  alias: string
  normalized_alias: string
  match_mode: exact | normalized_exact | reviewed_pattern
  valid_from: date?
  valid_to: date?
  context_parent_key: string?
  evidence_ids: list<string>
```

Aliases are contextual. `FAIR` can map to the FAIR lab when the work also resolves to Meta,
while an ambiguous acronym without parent context is not auto-assigned.

### Researcher

```text
Researcher
  key: string
  display_name: string
  external_ids: map<string, list<string>>  materialized crosswalk summary
  homepage: string?
```

### Researcher affiliation

```text
ResearcherAffiliation
  researcher_key: string
  organization_key: string
  role: faculty | researcher | engineer | student | affiliate | unknown
  valid_from: date?
  valid_to: date?
  method: curated_roster | official_profile | orcid | publication_history
  confidence: uint8
  evidence_ids: list<string>
```

A researcher can hold several simultaneous affiliations. CSRankings eligibility is stored
as provider metadata and must not be generalized into a universal definition of faculty.

### Evidence

```text
Evidence
  id: string
  source: csrankings | openalex | ror | dblp | orcid | official_site | curated
  source_record_id: string?
  source_url: string?
  observed_at: datetime
  excerpt: string?
  content_hash: string?
  license: string?
```

Full evidence remains offline unless redistribution is allowed. Browser artifacts expose
the attribution method and confidence, not copied source text.

### Work attribution

```text
WorkAttribution
  paper_id: string
  organization_key: string
  researcher_key: string?
  authorship_index: uint16?
  method: explicit_affiliation | official_publication | temporal_roster |
          provider_affiliation | curated_override
  confidence: uint8
  evidence_ids: list<string>
```

A paper can have direct attributions to multiple organizations when its authors represent
multiple organizations or units.

## Provider architecture

Each provider writes immutable source records plus mappings into the canonical registry.
Provider-specific logic does not leak into the frontend artifact schema.

```text
DirectoryProvider.load(snapshot) -> ProviderBatch

ProviderBatch
  organizations
  organization_relationships
  organization_aliases
  researchers
  researcher_affiliations
  evidence
  unresolved_records
```

Target providers:

| Provider | Primary use | Important limitation |
|---|---|---|
| ROR | Canonical institution roots and external IDs | Little department/lab coverage |
| OpenAlex | Work authorships, institution IDs, raw affiliations | Corporate units are often flattened |
| CSRankings | Academic institutions, eligible faculty, DBLP/ORCID links, venue taxonomy | Flat affiliations; academic only; restrictive license |
| DBLP | Author identity and curated venue records | No general organization hierarchy |
| ORCID | Researcher identity and declared employment | Incomplete and self-reported |
| Official sites | Departments, labs, corporate units, rosters, publication lists | Source-specific and changes over time |
| Curated overrides | Ambiguities, aliases, reorgs, corrections | Requires review and maintenance |

Raw provider snapshots and canonical mappings are separate. Every source record must end in
one of three states: mapped, intentionally ignored with a reason, or unresolved. Records
are never silently dropped.

### CSRankings policy

CSRankings should be implemented as an optional academic provider because its institution
and faculty coverage is valuable. Its current CC BY-NC-ND license may not permit transforming
and redistributing its database in this application's artifacts. Until permission or a
compatible interpretation is confirmed:

- do not commit or publish transformed CSRankings data;
- keep the provider disabled in redistributable builds;
- use synthetic fixtures in tests;
- use CSRankings as an evaluation reference;
- record source version and license in every private/local build manifest.

If permission is obtained, import institution and faculty source rows unchanged, map them
to canonical entities through explicit crosswalk records, and preserve attribution.

### CSRankings adapter contract

This design was checked against CSRankings commit
`60a28e499a444c3b8f7ef3ecf2eda6da9b70d551` (2026-07-07). The adapter consumes distinct
source products instead of treating the rendered website as one database:

| CSRankings input | Canonical use | Must not be inferred |
|---|---|---|
| `institutions.csv` | Academic root candidate, country/region, homepage crosswalk evidence | A stable identity or department/lab hierarchy |
| `csrankings-[a-z].csv` / generated `csrankings.csv` | Faculty roster claim with name, affiliation label, homepage, Scholar ID, and ORCID | Complete faculty coverage, historical validity, or a paper affiliation |
| `generated-author-info.csv` | Per-author, per-year selected-venue area aggregates for evaluation | Individual work identity; its `dept` column is an institution label, not a department |
| `util/csrankings.py` venue rules plus UI area groupings | Versioned CS area/venue facet and inclusion-rule provenance | A semantic topic label or organization unit |
| `old/*.csv` | Disposition evidence for former roster entries | A complete employment history |
| DBLP snapshot and aliases | Provider-side publication matching and author disambiguation | Canonical researcher identity without a crosswalk |

The adapter emits provider records and claims; it never emits canonical entities directly.
In particular:

1. Crosswalk each CSRankings affiliation label to a ROR/OpenAlex-backed university root or
   leave it unresolved. Name similarity only creates a review candidate.
2. Resolve researchers by ORCID first, then reviewed DBLP/OpenAlex crosswalks. A name alone
   is insufficient.
3. Record the snapshot date as `observed_at`. Do not invent `valid_from` or `valid_to` from
   a current roster.
4. Keep CSRankings area and venue identifiers in a separate curated taxonomy. Papers are
   joined to that taxonomy through independently licensed bibliographic records.
5. Use official university pages to add schools, departments, institutes, and labs.
   CSRankings supplies neither those units nor memberships in them.

This preserves the useful CSRankings organization and faculty organization while making
its academic scope, current-roster semantics, and licensing boundary explicit.

## Identity resolution

### Organizations

Resolution order is deterministic:

1. Exact curated crosswalk.
2. Exact ROR or OpenAlex ID.
3. Verified homepage domain plus country.
4. Reviewed alias valid at the relevant date.
5. Name similarity only creates a review candidate; it never auto-merges organizations.

Multiple legal or geographic OpenAlex institutions may map to one user-facing parent
organization. That mapping does not imply membership in a child research unit.

### Researchers

Resolution order:

1. Exact ORCID.
2. Exact provider crosswalk for OpenAlex, DBLP, or Semantic Scholar IDs.
3. Verified homepage plus compatible organization history.
4. Name and coauthor similarity creates a candidate only.

Common names and DBLP disambiguation suffixes require explicit crosswalks. A provider merge
must retain every contributing source identifier.

## Paper attribution

For each paper and authorship:

1. Resolve root institutions from provider IDs.
2. Normalize retained raw affiliation strings without discarding the originals.
3. Match date-valid, parent-contextual organization aliases.
4. Check official publication lists and curated overrides.
5. Check researcher memberships active on the publication date.
6. Emit the most specific direct attributions supported by independent evidence.
7. Derive parent rollups from date-valid `part_of` relationships.

Default evidence tiers:

| Score | Evidence |
|---:|---|
| 100 | Curated override or official publication listing |
| 95 | Exact, date-valid unit name in the paper's raw affiliation |
| 85 | Official temporal roster plus a compatible root affiliation on the paper |
| 80 | Trusted provider affiliation directly naming the canonical unit |
| 60-79 | Indirect or incomplete evidence; review/inclusive mode only |
| Below 60 | Candidate only; never emitted as a default attribution |

Evidence from different authors may assign one paper to several organizations. Matching a
parent organization never implies a child. A research topic, citation neighborhood, email
domain, or researcher's latest employer is insufficient by itself.

An undated roster observation cannot establish that a membership was active for an older
paper. It can support current directory browsing, but paper attribution requires a
date-compatible affiliation string or independent dated evidence. Evidence used to infer
a researcher affiliation from publication history cannot then be reused to attribute the
same publication; the attribution graph must remain non-circular.

## Meta and FAIR reference model

The initial curated registry should contain at least:

```yaml
organizations:
  - key: meta
    name: Meta
    sector: company
    unit_type: organization
    external_ids:
      openalex_us: I4210114444
      openalex_il: I2252078561

  - key: meta-ai
    name: Meta AI
    sector: company
    unit_type: research_division

  - key: fair
    name: FAIR
    sector: company
    unit_type: lab

  - key: reality-labs
    name: Reality Labs
    sector: company
    unit_type: research_division

  - key: meta-superintelligence-labs
    name: Meta Superintelligence Labs
    sector: company
    unit_type: research_division
    status: proposed

relations:
  - parent_key: meta
    child_key: meta-ai
    relation_type: part_of
  - parent_key: meta
    child_key: fair
    relation_type: part_of
  - parent_key: meta
    child_key: reality-labs
    relation_type: part_of
  - parent_key: meta
    child_key: meta-superintelligence-labs
    relation_type: part_of
```

This is a conservative display graph, not a claim about internal reporting lines. Exact
parentage and validity dates must be backed by official evidence before production. If
dated evidence establishes that FAIR was part of Meta AI for a particular interval, that
time-scoped relationship can replace or supplement the direct Meta relationship. Proposed
units do not appear in production filters.

Initial alias policy:

- `Facebook AI Research`, `Facebook AI Research (FAIR)`, and contextual `FAIR` -> `fair`
- `Meta AI` -> `meta-ai`
- `Facebook AI` -> `meta-ai` only after review; never assume it means FAIR
- `Facebook Reality Labs` and `Meta Reality Labs` -> `reality-labs`
- `Facebook`, `Meta Platforms`, and geographic Meta entities -> `meta`

The migration acceptance baseline is that the current 1,976-paper parent set remains
reachable through `meta`, while only the evidence-backed subset appears under each child.

## Academic reference model

CSRankings can seed a researcher-to-university claim, but not department or lab membership.
For example:

```text
Carnegie Mellon University
  School of Computer Science
    Machine Learning Department
    Robotics Institute
      Biorobotics Lab
```

The university root is resolved through ROR/OpenAlex. CSRankings can contribute the
researcher identity and university claim. Official department and lab rosters contribute
more specific, temporal memberships. A professor may be directly affiliated with both a
department and an institute; the DAG preserves both.

Independent labs use the same model. An organization such as Anthropic can be a root
company or independent lab, while officially documented internal research teams are child
units. No academic-specific schema is required.

## Target pipeline

The stage numbers remain stable during migration.

```text
provider snapshots + curated overrides
                 |
                 v
       s00_build_directory
                 |
       canonical directory tables
                 |
OpenAlex broad field/date corpus
                 |
       s01_fetch_openalex
                 |
       s02_build_corpus
       (retain authorship evidence)
                 |
          embedding/map stages
                 |
       s10_indexes
       (resolve work/researcher organizations)
                 |
       s11_emit -> artifact schema v2
```

Target responsibilities:

- `s00`: load enabled providers, canonicalize roots and units, validate the organization
  DAG, and write versioned directory tables.
- `s01`: discover works by field/date, not organization. A development-only sample mode
  may retain an explicit cap.
- `s02`: retain per-authorship OpenAlex IDs, raw author name, raw ORCID, institution IDs,
  and raw affiliation strings in structured columns.
- `s10`: resolve canonical researchers and direct paper/researcher organization
  attributions, then build compact indexes.
- `s11`: emit schema-v2 directory and membership artifacts with provider versions and
  license metadata in the manifest.

Suggested source layout:

```text
directory/
  organizations.yaml
  relationships.yaml
  aliases.yaml
  crosswalks/
  overrides/
pipeline/directory/
  providers/
  canonicalize.py
  attribution.py
  validation.py
data/directory/sources/<provider>/<snapshot>/
```

Generated provider snapshots stay out of git unless their licenses explicitly permit
redistribution. Curated local records and synthetic test fixtures are version controlled.

## Artifact contract v2

### `organizations.json`

Small hierarchy and display metadata:

```text
nodes[]
  id: int32
  key: string
  name: string
  sector: string
  unit_type: string
  status: string
  valid_from_year: int16?
  valid_to_year: int16?
  external_ids: map<string, list<string>>
  direct_count: int
  rollup_count: int

relations[]
  parent_id: int32
  child_id: int32
  relation_type: string
  primary_for_display: bool
  valid_from_year: int16?
  valid_to_year: int16?
```

### `paper_organizations.arrow`

One row per direct attribution:

```text
node_id: int32
organization_id: int32
researcher_id: int32?
method: uint8
confidence: uint8
```

`researcher_id` identifies the authorship supporting an attribution when applicable.
Ancestor membership is derived from `organizations.json`. Evidence IDs remain in an
offline audit artifact and need not inflate the browser bundle.

### `researchers.arrow`

Replaces the minimal author index:

```text
researcher_id: int32
key: string
display_name: string
openalex_id: string?
orcid: string?
dblp_id: string?
homepage: string?
paper_count: int32
```

### `researcher_organizations.arrow`

```text
researcher_id: int32
organization_id: int32
role: uint8
valid_from_year: int16?
valid_to_year: int16?
method: uint8
confidence: uint8
```

The browser loader builds:

- organization ID -> direct paper bitset/list;
- `(organization ID, publication year)` -> date-valid descendant closure;
- researcher ID -> paper list;
- organization ID -> current researcher list.

The displayed primary parent is selected from the date-valid relation marked
`primary_for_display`; it is not stored redundantly on the organization node. The schema
version increments when these files replace `orgs.json` and `authors.arrow`.

## Frontend behavior

The organization filter becomes a searchable hierarchy grouped by sector.

- Selecting a parent includes direct and descendant papers, deduplicated.
- Selecting a child includes only that child's evidence-backed attributions.
- An ancestor rollup uses the relationship graph valid on each paper's publication date,
  not the organization's latest hierarchy.
- Counts distinguish direct membership from descendant rollup.
- Breadcrumbs expose the selected unit's context.
- Multi-parent units appear under their primary display parent and expose their other
  affiliations without duplicating papers.
- Historical units remain searchable when the selected date range overlaps their validity.
- Default mode uses confidence >= 80; an inclusive mode may show reviewed lower-confidence
  attributions.
- Organization color chooses the active selected organization first. Without a selection,
  it uses the most specific, highest-confidence direct attribution with a deterministic
  tie-breaker.
- Researcher results can be scoped to an organization and show current/historical unit
  memberships.

The UI must never relabel a parent aggregate as a specific lab. An unresolved parent
bucket is a valid and necessary state.

## Scale

At the current 28k-paper scale, the browser can load organization nodes and direct
memberships once and compute rollups locally. The directory does not require an application
backend.

The local development bundle may remain a deterministic field/date sample, but it must not
claim complete coverage or "any organization." A production broad-corpus build moves to
partitioned artifacts. Past approximately 100k papers:

- partition `paper_organizations` with the same spatial/versioned tiles as points;
- load researcher profiles and unit rosters lazily;
- keep the small organization DAG global;
- serve immutable provider and artifact versions;
- add query endpoints only for cross-partition directory searches that static indexes
  cannot answer efficiently.

## Validation and quality gates

Directory builds fail on:

- cycles in date-overlapping `part_of` relationships;
- more than one date-overlapping `primary_for_display` parent for a child;
- duplicate active canonical external IDs;
- references to missing organizations, researchers, papers, or evidence;
- invalid time ranges;
- ambiguous aliases marked as automatic exact matches;
- default attributions below the configured confidence threshold;
- rollup counts that do not equal the deduplicated union of direct descendants;
- provider records that disappear without a mapped, ignored, or unresolved disposition.

Required reports:

- organization and researcher crosswalk coverage by provider;
- direct versus inferred paper attribution counts;
- unresolved and ambiguous aliases;
- confidence distribution by organization and method;
- hierarchy changes between snapshots;
- work-count changes above a configured threshold.

Required golden fixtures:

- Meta parent, FAIR, Meta AI, and Reality Labs separation;
- a researcher moving between organizations;
- a university researcher with simultaneous department and institute memberships;
- a historical organization rename or merger;
- a multi-organization paper;
- an ambiguous acronym that must remain unresolved.

Human review targets for the first production directory:

- at least 95% precision on a sampled set of default child-unit attributions;
- 100% of source records have a recorded disposition;
- 100% of user-visible organizations have provenance and a stable key;
- the 425 explicit FAIR papers remain attributable to FAIR before adding inferred recall.

## Migration plan

### Phase 0: correct semantics

- Rename the current broad Meta entry to `Meta`.
- Preserve the existing seven filters and bundle shape.
- Record the current Meta/FAIR diagnostic as a regression fixture.

### Phase 1: preserve evidence

- Extend `s02` to retain structured authorship affiliation evidence.
- Add synthetic fixtures and normalization tests.
- Do not change user-visible filters yet.

### Phase 2: canonical registry

- Add the directory modules, stable keys, relationships, aliases, evidence, and validation.
- Seed the seven current roots plus reviewed child units.
- Resolve current papers into the new direct-attribution table.
- Emit both legacy and v2 artifacts for comparison.

### Phase 3: academic provider

- Implement the license-gated CSRankings adapter.
- Crosswalk institutions to ROR/OpenAlex and researchers by ORCID/DBLP.
- Keep every unresolved record visible in a build report.
- Add official department/lab roster providers incrementally.

### Phase 4: decouple corpus discovery

- Fetch or ingest a broad field/date corpus independently of organization.
- Move organization membership entirely into `s10`.
- Publish a small reproducible sample bundle for local development.

### Phase 5: directory UI and scale

- Replace flat chips with hierarchical search and parent/child selection.
- Add organization-scoped researcher browsing.
- Remove legacy `orgs.json`.
- Introduce tiled membership data when corpus size requires it.

Each phase is independently releasable and retains an auditable fallback to the previous
artifact version.

## Decisions still requiring explicit resolution

1. Obtain permission or legal guidance before redistributing transformed CSRankings data.
2. Choose refresh cadences per provider and how official-page snapshots are archived.
3. Define who can approve curated organization and alias changes.
4. Decide whether lower-confidence attributions are exposed publicly or only in review
   tooling.
5. Establish the initial set of corporate and academic units whose rosters will be
   maintained manually.
