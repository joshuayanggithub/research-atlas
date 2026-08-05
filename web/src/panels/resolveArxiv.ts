// Shared arXiv-id + abstract resolver for the paper-preview features (ArxivPreview's Paper
// tab and FirstFigure's inline thumbnail). Most arXiv papers are indexed in OpenAlex under a
// publisher/conference DOI, not an arXiv one, so only ~14% of the corpus carries a direct
// arXiv id. Semantic Scholar can map a DOI (or a title) to the arXiv id for far more of
// them, which is what unlocks the PDF-based previews. S2 is CORS-enabled so this is
// client-side, no backend. Results are cached per (arxiv|doi|title) key.

export interface SemanticScholarPaper {
  title?: string;
  abstract?: string | null;
  tldr?: { text?: string | null } | null;
  externalIds?: Record<string, string | null>;
}

interface LookupPayload extends SemanticScholarPaper {
  data?: SemanticScholarPaper[];
}

export interface Resolved {
  arxivId: string | null;
  tldr: string | null;
  abstract: string | null;
}

export function canonicalArxivId(rawId: string): string {
  return rawId
    .trim()
    .replace(/^arxiv:\s*/i, "")
    .replace(/^https?:\/\/(?:www\.)?arxiv\.org\/(?:abs|pdf)\//i, "")
    .replace(/\.pdf$/i, "");
}

export function canonicalDoi(rawDoi: string): string {
  return rawDoi
    .trim()
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
    .replace(/^doi:\s*/i, "");
}

export function arxivPath(id: string): string {
  return id.split("/").map(encodeURIComponent).join("/");
}

// Recover an arXiv id already present in the corpus row: either the arxiv_id field, or a DOI
// that encodes it (10.48550/arXiv.<id>). Cheap, no network — try this before hitting S2.
export function localArxivId(arxivId: string | null, doi: string | null): string | null {
  if (arxivId) return canonicalArxivId(arxivId);
  if (doi) {
    const m = doi.match(/arxiv\.([^/\s]+)$/i);
    if (m) return m[1];
  }
  return null;
}

function normalizedTitle(title: string): string {
  return title.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function extractPaper(payload: LookupPayload): SemanticScholarPaper | null {
  if (Array.isArray(payload.data)) return payload.data[0] ?? null;
  return payload;
}

function arxivIdFrom(paper: SemanticScholarPaper | null): string | null {
  const id = paper?.externalIds?.ArXiv ?? paper?.externalIds?.arXiv;
  return id ? canonicalArxivId(id) : null;
}

async function fetchSemanticScholar(url: string): Promise<SemanticScholarPaper | null> {
  const response = await fetch(url);
  if (response.status === 404) return null;
  if (response.status === 429) throw new Error("Paper lookup is temporarily rate limited");
  if (!response.ok) throw new Error(`Paper lookup failed (${response.status})`);
  return extractPaper((await response.json()) as LookupPayload);
}

// Resolve arXiv id (if not already known locally) plus TLDR + abstract, in one pass.
async function resolvePaper(
  directArxiv: string | null,
  doi: string | null,
  title: string,
): Promise<Resolved> {
  // A locally-known arXiv id is self-sufficient for the PDF previews — skip S2 (its browser
  // CORS is flaky and would only supply the text fallback we won't need).
  if (directArxiv) return { arxivId: directArxiv, tldr: null, abstract: null };

  const fields = "title,externalIds,abstract,tldr";
  let paper: SemanticScholarPaper | null = null;

  if (doi) {
    paper = await fetchSemanticScholar(
      `https://api.semanticscholar.org/graph/v1/paper/DOI:${encodeURIComponent(canonicalDoi(doi))}?fields=${fields}`,
    ).catch(() => null);
  }
  if (!paper && title) {
    const match = await fetchSemanticScholar(
      `https://api.semanticscholar.org/graph/v1/paper/search/match?query=${encodeURIComponent(title)}&fields=${fields}`,
    ).catch(() => null);
    if (match?.title && normalizedTitle(match.title) === normalizedTitle(title)) paper = match;
  }

  return {
    arxivId: arxivIdFrom(paper),
    tldr: paper?.tldr?.text ?? null,
    abstract: paper?.abstract ?? null,
  };
}

const lookupCache = new Map<string, Promise<Resolved>>();

export function lookupKey(arxivId: string | null, doi: string | null, title: string): string {
  return `${arxivId ?? ""}|${doi ? canonicalDoi(doi) : ""}|${normalizedTitle(title)}`;
}

/** Cached resolve: first honor a locally-known arXiv id, else ask Semantic Scholar. */
export function cachedResolve(
  arxivId: string | null,
  doi: string | null,
  title: string,
): Promise<Resolved> {
  const local = localArxivId(arxivId, doi);
  const key = lookupKey(local, doi, title);
  const cached = lookupCache.get(key);
  if (cached) return cached;
  const request = resolvePaper(local, doi, title).catch((error) => {
    lookupCache.delete(key);
    throw error;
  });
  lookupCache.set(key, request);
  return request;
}

export function dropCached(arxivId: string | null, doi: string | null, title: string): void {
  lookupCache.delete(lookupKey(localArxivId(arxivId, doi), doi, title));
}

// Fetch just the abstract/TLDR for a known arXiv id (fallback text when a PDF render fails).
export async function fetchAbstract(arxivId: string): Promise<Resolved> {
  const paper = await fetchSemanticScholar(
    `https://api.semanticscholar.org/graph/v1/paper/arXiv:${encodeURIComponent(arxivId)}?fields=abstract,tldr`,
  );
  return { arxivId, tldr: paper?.tldr?.text ?? null, abstract: paper?.abstract ?? null };
}
