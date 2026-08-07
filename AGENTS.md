This tool is a research visualizer web application. 

Do not use brazil standards, aws standards. Instead treat this project as a standard open source project without internal Amazon usage. 

### Organization
It allows one to view the research of any organization in any granularity. This includes 
    - Universities (Berkeley), Departments (CMU's MLD or RI) and individual Research Labs/Groups within (Berkley's BAIR, CMU's BIG lab, Biorobotics lab) 
    - Companies (Amazon, Google) and individual research Groups (FAIR, Meta Superintelligence, Amazon FAR, Amazon AGI, Google Deepmind, Google Brain, etc)
    - NeoLabs (Redwood Research, Anthropic, OpenAI, Deepseek, Kimi, Minimax)

The tool allows us to filter computer science research works, particularly AI/ML works, by granularity. 

What counts as research are publications, which loosely can be defined as those published on ArXiv (which may not be affliliated with conferences), and especially those submitted and accepted to conferences. 

### Topics
The visualizer uses a feasible embedding model to very detailedly embed at least the abstract and title (and more depth if needed) into vectors that can be used to visualize the closness of related research works. The view is in graph view, where nodes (research works) close to other nodes are very similar research works. Directed edges should include citations. Think about what size and how to do this step carefully. 

With this graph view and embedding, zooming in and out in this tool allows grouping different sizes of clusters of nodes the topics of related works by granularity:
    - zooming in you should be able to see similar subtopics such as action-conditioned world models vs World Action Models
    - in the middle you should see topics in machine learning such as self supervised learning, world models, etc
    - zooming out could mean viewing computer architecture, vs computer systems work
This is a very important feature and think carefully about how to implement this with embedding model. 

### Researchers / Time Periods
You must also be able to filter works by author and date (start date - end date) in graph view. 

### Related Works

Some ideas for organization are https://github.com/emeryberger/CSRankings. Clone this and analyze how it works for generating

Anothe idea is clustering related works by citations, we know that citations between works intrinsically means the works are related, even though no explicit notion of topic is defined by in/out edges. This is seen from https://www.connectedpapers.com/

### Logging
Keep the project docs current. They live in `docs/` (only `AGENTS.md`, `CLAUDE.md`, and
`README.md` stay in the repo root) and split into four roles:

- `docs/Features.md` — **what's possible** (capabilities). Update when a change adds/removes a
  user-facing capability; keep its Feature → test-coverage table in sync.
- `docs/Design.md` — **how it's implemented** (the current mechanism / rationale).
- `docs/DESIGN_DECISIONS.md` — **tradeoffs + revert conditions**: why a choice was made over
  the alternatives and what undoing it would cost. Never delete an entry — mark it superseded.
- `docs/TODO.md` — **remaining tasks** for handoff (actionable; complements `docs/ROADMAP.md`).

Also: `docs/HANDOFF.md` (getting productive fast), `docs/ARCHITECTURE.md` (code map),
`docs/ROADMAP.md` (prioritized strategy), `docs/ORGANIZATION_DIRECTORY.md` (org/lab design),
and the `docs/RESEARCH_PRIOR_WORK.md` / `docs/PRIOR_WEBSITES.md` reference surveys.

### Browser Verification

Every feature implementation and every prompt that changes application behavior MUST be
verified in a real browser with Playwright before the work is considered complete.

- Start the application and use Playwright against the actual local URL.
- Exercise the primary workflow affected by the change; do not treat a successful build,
  typecheck, or unit test as browser verification.
- Check at least one desktop viewport and one mobile viewport for user-facing changes.
- Capture screenshots of the resulting state and inspect them for clipping, overlap,
  unreadable text, blank canvases, and inconsistent controls.
- Inspect browser console and page errors. Unexpected errors MUST be resolved.
- For graph or canvas changes, verify that the canvas contains non-background pixels and
  that pan, zoom, selection, filters, labels, and directed edges still behave as intended.
- Network-dependent experiences MUST be tested with deterministic Playwright route mocks
  in addition to any live smoke check.

Automated Playwright tests SHOULD cover stable critical workflows. Browser verification
is still required for visual changes even when those tests pass.

The automated suite lives in `web/e2e/` and runs against the built bundle:

```bash
cd web && npm run test:e2e            # desktop + mobile (Chromium)
npx playwright test --project=desktop # one project
```

It covers load + canvas render, title search/select, organization drill-down and
org-scoped researchers, the date presets, and a route-mocked load-failure path. On a fresh
machine run `npx playwright install chromium` once. These tests guard regressions; they do
not replace the manual browser check above for new visual work.
