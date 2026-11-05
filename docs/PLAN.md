# ask-and-do: plan

Last updated: 2026-11-01. Time-box: about 10 days. Aim for a working end-to-end slice by day 4 and a feature freeze after day 7. Use one GitHub issue per phase.

## 1. Goal and non-goals

**Goal:** a reference implementation of an assistant that (a) answers questions from a docs site with citations and refuses when the docs do not cover the question, and (b) performs allowlisted API actions on the user's behalf, with confirmation for writes and permissions enforced by the backend. Measure it with evals and publish the numbers, including failures.

**Honest framing:** each piece has existing tools (see section 11). The contribution is the integration, a four-route eval (docs, action, both, decline), a doc-to-tool mapping with a coverage report, a clear auth write-up, and an embeddable component. Do not claim a market gap.

**Non-goals for v0.1:** login-protected crawling, JavaScript-rendered crawling, a setup wizard, multi-agent orchestration, model routing, a Rust gateway.

## 2. Architecture

### Runtime (what happens on each message)

```
Host web app (React, embeds <AskAndDo/>)
        |  user token + message
        v
Assistant API (FastAPI, tool-calling loop) <----> LLM provider (pluggable)
        |                       |
        v                       v
search_docs                Tool gateway (FastMCP, allowlist)
(retrieve, gate, verify)        |
        |                       v
        v                  Mock admin API (FastAPI + SQLite, enforces roles from JWT)
Postgres + pgvector
```

- The assistant API gives the model the tools: `search_docs` plus the allowlisted action tools. The model chooses: docs, action, both, or decline. There is no separate router.
- Writes return a pending action. The UI shows the exact call and the user confirms through a separate endpoint before anything executes.
- The mock admin API checks the caller's role itself, so a viewer's write is refused by the backend even if the model asks for it.

### Offline (what prepares and tests it)

```
Docs site  -> Ingest (crawl, chunk, embed) -> pgvector
OpenAPI spec -> Tool prep (lint, allowlist, rewrite) -> Tool config (names, docs mapping)
Eval set -> Eval runner (calls the assistant API, three configs) -> Results table
```

The eval runner is a test harness only. It is not part of the product and users never touch it.

## 3. Decisions so far

| Topic | Decision |
|---|---|
| Name | `ask-and-do` |
| Visibility | Public from day one, with secret scanning and push protection on |
| License | MIT |
| Language | Python for the backend, React and TypeScript for the frontend |
| Orchestration | Hand-written tool-calling loop. No LangChain or LangGraph in v0.1 |
| Layout | Two top-level folders: `prepare/` (ingest, tool prep, evals, demo target) and `runtime/` (API, assistant, search_docs, MCP tools). `prepare` may import `runtime`, never the reverse |
| Corpus | Short product docs we write for the mock admin product (about 20 to 30 pages, served as a static site) so ground truth is known |
| Crawler scope | Public docs, no login. Fetching sits behind a `Fetcher` interface so login can be added later |
| Vector store | Postgres with pgvector and full-text search |
| Frontend | assistant-ui for the chat primitives, with our own confirmation card |
| Auth | Document in the README whether the host token is forwarded or exchanged, and why. Forwarding to our own mock API inside one trust boundary is acceptable. Do not forward a token to a different upstream audience |

## 4. Phases

### Phase 0: setup (half a day)

- [ ] Repo skeleton: `prepare/` (`ingest/`, `tools/`, `evals/`, `demo/`) and `runtime/` (`api/`, `assistant/`, `search_docs/`, `mcp_tools/`, `web/`), plus a shared `common/`. See the layout in `CLAUDE.md`
- [ ] `docker-compose.yml` with Postgres and pgvector
- [ ] `.env.example`, CI stub that runs lint and tests
- [ ] README skeleton: pitch, "in progress" status, empty eval table, Related work, note that all data is public docs and a mock API
- [ ] Secret scanning and push protection enabled

Done when: `docker compose up` starts the database and CI runs green on an empty test.

### Phase 1: mock admin API and docs (days 1 to 2)

- [ ] FastAPI + SQLite for users, groups, projects
- [ ] Two roles (admin, viewer) enforced from a mock JWT
- [ ] Deliberately confusing endpoints (see section 7)
- [x] 20 to 30 pages of product docs, built as a crawlable static site (28 pages, `prepare/demo/docs_site/`)
- [ ] Tests for role enforcement

Done when: the OpenAPI spec is generated, a viewer gets 403 on writes, tests pass, and the docs site serves locally.

### Phase 2: ingestion and grounded Q&A (days 2 to 4)

- [x] Ingest pipeline (section 5)
- [ ] `search_docs` with retrieval, not-found gate and quote verification (section 6)
- [ ] Chat endpoint returning answer, citations and a refusal state
- [x] Fixture-based ingest tests

Done when: a docs question returns a cited answer and an unanswerable question returns "not found". Push, update the README status, then start sharing the repo as in progress.

### Phase 3: tool gateway and routing (days 4 to 6)

- [ ] Generate MCP tools from the OpenAPI spec with FastMCP
- [ ] Run an existing linter (mcpolish or MCPLint) on the raw output and save findings
- [ ] Allowlist with writes off by default
- [ ] Rewritten tool names and descriptions, including "when not to use" lines
- [ ] Doc-to-tool mapping file and coverage report (documented but no tool, tool but undocumented)
- [ ] Pending-action and confirmation flow that shows the exact call
- [ ] `search_docs` registered as one more tool so the model routes
- [ ] Clarifying question when two tools score close
- [ ] Read-before-write for actions that name entities
- [ ] README note on token forwarding versus exchange

Done when: an admin can create a user after confirming, a viewer's attempt is refused by the backend, and the assistant asks a clarifying question on an ambiguous pair.

### Phase 4: evals (days 6 to 7)

See section 8. Feature freeze after this phase.

- [ ] About 40 cases with expected routes, including unanswerable and near-miss cases
- [ ] Runner that executes three configurations and writes tables to `prepare/evals/results/`
- [ ] A subset wired into CI
- [ ] README table with real numbers and a short failure analysis

### Phase 5: embeddable React component (days 8 to 9)

- [ ] Package exposing `<AskAndDo apiUrl getToken />`, built on assistant-ui
- [ ] Citation links, streaming states (searching, calling a tool, refused, not found), confirmation card
- [ ] Theming through CSS variables
- [ ] Host demo app with an admin/viewer login switch

Done when: the component runs inside the host app and a viewer's denied action is shown clearly.

### Phase 6: polish and publish (day 10)

- [ ] 2-minute demo video, architecture diagram
- [ ] "Limitations and what I would change for production" section
- [ ] Tag `v0.1.0`, pin the repo, add a short post about what failed in the evals
- [ ] Optional, half a day: a small n8n workflow that calls the assistant's API

### If time runs short, cut in this order

1. Reranker (keep the not-found gate)
2. Component polish (keep a basic version)
3. Third eval configuration
4. Extras on the coverage report

Never cut the evals, the confirmation step, or the backend permission check.

## 5. Ingest design

Offline command, safe to re-run.

1. **Discover:** sitemap first, otherwise same-host links under a path prefix. Respect `robots.txt`, normalize URLs (strip fragments and tracking parameters), dedupe, skip version archives and non-doc sections.
2. **Fetch:** `httpx` with timeouts, retries and a polite rate limit, behind a `Fetcher` interface (`fetch(url) -> content, status`). v1 has one implementation, a plain HTTP fetcher.
3. **Extract:** keep the article body, drop sidebar, header, footer and "on this page" lists.
4. **Convert to Markdown:** preserve heading levels, code blocks, lists and tables.
5. **Chunk by heading:** one chunk per section. Prefix the breadcrumb (`Groups > Creating a group`). Target a few hundred tokens: merge tiny sections into the parent, split oversized ones at paragraph boundaries with a small overlap. Never split inside a code block or table.
6. **Metadata:** source URL, section anchor, page title, heading path, doc version, page hash, chunk hash, ingest time.
7. **Embed:** the breadcrumb plus the chunk text, with a local model by default.
8. **Store:** Postgres with pgvector and a full-text column.

Minimal schema (the vector size depends on the embedding model):

```sql
CREATE TABLE chunks (
  id           text PRIMARY KEY,
  url          text NOT NULL,
  anchor       text,
  title        text,
  breadcrumb   text NOT NULL,
  body         text NOT NULL,
  doc_version  text,
  page_hash    text NOT NULL,
  chunk_hash   text NOT NULL,
  embedding    vector(384),
  tsv          tsvector,
  ingested_at  timestamptz DEFAULT now()
);
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON chunks USING gin (tsv);
```

Re-runs: chunk ID is derived from URL, heading path and index, so re-crawls update in place. Unchanged pages (same page hash) are skipped, changed pages are re-chunked and upserted, and chunks of removed pages are deleted. Running twice in a row must change nothing.

Quality checks: an extraction-coverage check (flag pages whose extracted text is far shorter than the visible text), a run report (found, fetched, skipped, failed with reasons, chunks created, updated, deleted), and fixture tests asserting no navigation text in chunks, intact code blocks, stable IDs across two runs, and resolvable anchors.

Common failure modes to test for: navigation or footer text in chunks, chunks too big or too small (tune with the eval set), flattened tables, duplicate content across versions, stale chunks after page removal.

## 6. `search_docs` design (to refine in Phase 2)

- Hybrid retrieval: vector search plus Postgres full-text search, merged. Optional cross-encoder reranker.
- Not-found gate: if the best score is below a threshold, return no context and let the assistant say the docs do not cover it. Tune the threshold on the eval set, not by feel.
- Structured answer: the model returns the answer, the chunk IDs it used, and a verbatim supporting quote for each claim.
- Verify in code that each quote appears in the cited chunk. If not, discard the answer and return "not found".
- Citations link to the source URL and section anchor.
- Optional later: a semantic groundedness check on the final answer.

## 7. Tool gateway and mock API design

**Mock admin API:** users, groups, projects. Roles: admin and viewer. Include on purpose:

- A near-duplicate pair with different meanings (for example `create_user` and `invite_user`).
- A second pair such as `update_group` (replace) versus `patch_group` (partial).
- One cryptic operation name.
- One same-name, different-effect case (for example `remove_member` from a group versus from the whole org).
- At least one destructive endpoint that stays off the allowlist.

**Gateway:**

- Generate tools from the spec, then lint the raw output with an existing tool and keep the findings.
- Allowlist only the operations we want (read operations plus a few writes). Writes stay off by default.
- Rewrite names to `verb_noun` and descriptions to include when to use and when not to use each tool.
- Mapping file (YAML) per tool: matching doc page, constraints from the docs, confidence, or `doc: null` for undocumented operations (keep those disabled).
- Coverage report both ways: documented but no tool, tool but undocumented.
- Keep the visible tool set small. If it grows, retrieve only the top few relevant tools per message.
- Confirmation: a write returns a pending action with the exact call. A separate endpoint executes it after the user confirms.

## 8. Evals

Each case has: question, expected route (`docs`, `action`, `both`, `decline`), expected tool or doc source where applicable, and for actions the expected parameters.

Include:

- Docs questions, unanswerable questions, and near-miss questions (adjacent topics the docs do not cover).
- "How do I create a group?" (docs, not an action) versus "Create a group called Platform" (action).
- Combined requests (act, then explain from docs).
- Out-of-scope requests, and requests for an operation that is not on the allowlist.
- Ambiguous requests that should trigger a clarifying question.
- A viewer attempting a write, which the backend must refuse.

Run three configurations against the same assistant code, changing only the tool configuration:

1. Raw tools generated from the spec.
2. After lint and rewritten descriptions.
3. Plus clarify-on-ambiguity and read-before-write.

Metrics: route accuracy, tool-selection accuracy, wrong-write rate, grounded-answer accuracy, refusal rate on unanswerable questions, latency and cost per question.

Keep about a quarter of the cases as a hold-out that is not used while tuning, and report both sets. Report steps that did not help. A subset runs in CI.

## 9. Later work (after v0.1.0, not before)

Login-protected docs, in this order:

1. Header or token auth for the HTTP fetcher.
2. Session-cookie support, with detection of redirects to a login page so an expired session fails loudly instead of indexing "Sign in".
3. Optional headless-browser fetcher for JavaScript sites and form logins.
4. Per-chunk access labels and query-time filtering by the caller's identity, if docs have different audiences. Filter before retrieval, not in the prompt.

Other:

- Setup wizard (docs URL, spec URL, tool review) over the same commands. Local only, because fetching user-supplied URLs raises SSRF and token-storage concerns.
- A second corpus (for example Backstage docs) as a generality demo.
- A Backstage Actions Registry variant with permission checks in the handlers.
- A LangGraph version of the orchestrator, evaluated against the same case set.
- A Rust version of the tool gateway behind the same HTTP/MCP boundary.

## 10. Risks

- Auto-generated tools confuse the model: mitigated by the allowlist, rewritten descriptions, clarification, and the lint step.
- Chunking quality caps answer quality: tune with the evals.
- Scope creep: the cut order in section 4 and the v0.1 scope in `CLAUDE.md` apply.
- Fast-moving dependencies: pin versions and check docs per phase.

## 11. Related work (cite in the README, build on or compare)

- Tool linting and tool-selection testing: agenticlint, MCPLint, mcpolish, mcp-openapi-doctor, agent-ready, mcp-tef.
- OpenAPI to MCP and curation: FastMCP, openapi-mcp-generator, Speakeasy and Gram, Stainless.
- Chat over an API with confirmations and user session passthrough: any2agent.
- Docs assistants: Kapa, Inkeep, Mintlify's assistant.
- In-app copilot and chat UI libraries: CopilotKit, assistant-ui, Tool UI.
- Backstage: the MCP Actions Backend and the Roadie RAG AI plugin (no longer actively maintained).

Treat this list as a starting point and check each project's current status when writing the README.