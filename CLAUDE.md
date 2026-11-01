ask-and-do

Reference implementation of a docs assistant that answers with citations and performs allowlisted API actions behind one chat box. It crawls a docs site into a vector store (RAG), turns an OpenAPI spec into curated MCP tools, and ships an embeddable React chat component. It includes evals for grounding, routing and tool selection.

This is a public portfolio project. The detailed plan, architecture and eval design are in docs/PLAN.md. Read it before starting any phase.

Non-negotiable rules
Public repo hygiene. No employer or client material anywhere: no internal docs, specs, names, screenshots or copied text. Use only public docs and the mock admin API we build.
Secrets. Only in .env (gitignored). Commit .env.example only. Never print or log tokens, keys or cookies.
No invented results. Numbers in the README come from prepare/evals/results/ produced by the eval runner. Report failures and steps that did not help, not only wins.
No novelty claims. The README has a "Related work" section. Build on or compare against existing tools (see docs/PLAN.md) instead of rebuilding them, and describe this project as a reference implementation.
Permissions live in the backend. The mock admin API enforces roles from the caller's token. Never rely on the model or the prompt for access control.
Writes are off by default. Only allowlisted tools are exposed. Write tools return a pending action that the user must confirm, and the confirmation shows the exact call (method, path, parameters).
"Not found" is decided in code. A retrieval-score gate plus verbatim quote verification decide whether the assistant may answer from docs. Do not rely on prompt wording alone.
Pin versions. FastMCP, assistant-ui and similar libraries are pre-1.0 and change quickly. Pin exact versions and check current docs before using an API.
v0.1 scope (do not expand without asking)

In: public static docs only, Python backend, React component, evals, Docker Compose. Out: LangChain/LangGraph, Redis, Kubernetes, a Rust gateway, login-protected crawling, JavaScript-rendered crawling, a setup wizard. These are listed as later work in docs/PLAN.md.

Stack
Backend: Python 3.12, FastAPI, Pydantic, uvicorn, SSE for streaming.
LLM access: thin provider interface (Anthropic SDK, AWS Bedrock, optional Ollama) selected by env var.
Embeddings: local model by default (fastembed or sentence-transformers); hosted option via env var.
Storage: Postgres with pgvector, plus Postgres full-text search for hybrid retrieval.
Crawler: httpx plus trafilatura (or a CSS selector for our own docs). Crawl4AI is later work.
Tool gateway: FastMCP (from_openapi) with allowlist and description overrides.
Mock admin API: FastAPI, SQLite, PyJWT (roles: admin, viewer).
Lint and evals: mcpolish or MCPLint, optionally mcp-tef, pytest, our own eval runner.
Frontend: React, TypeScript, Vite, Tailwind, assistant-ui. Small host demo app.
Tooling: uv, ruff, pytest, pnpm, Docker Compose, GitHub Actions.

Ask before adding a dependency that is not listed here.

Repository layout

Two groups. prepare/ runs offline before serving. runtime/ serves users once preparation is done.

ask-and-do/
├── prepare/
│   ├── ingest/         crawl docs, extract, chunk, embed, write to pgvector (Fetcher interface)
│   ├── tools/          read the OpenAPI spec, lint, allowlist, rewrite names and descriptions, write tools.yaml
│   ├── evals/          run cases against the runtime API to tune the system
│   │   ├── cases/      eval questions with expected routes (tracked)
│   │   ├── results/    result tables (tracked, they are the evidence)
│   │   └── traces/     raw traces (gitignored, may contain tokens)
│   └── demo/           the test target: admin_api/ (mock API with role checks) and docs_site/ (static docs)
├── runtime/
│   ├── api/            FastAPI: chat endpoint (SSE) and action confirmation endpoint
│   ├── assistant/      tool-calling loop: decides search_docs, a tool, both, or decline
│   ├── search_docs/    query pgvector, not-found gate, quote verification, citations
│   ├── mcp_tools/      load tools.yaml, call the API with the user's token, pending writes
│   ├── common/         settings, db, LLM provider, embedder
│   └── web/            embeddable React component and host demo app
├── docs/PLAN.md
├── docker-compose.yml
└── README.md

Dependency direction: prepare may import runtime (for example the embedder, so ingest and search use the same model). runtime never imports prepare. The groups otherwise hand off through data: ingest writes the chunks table, tools writes tools.yaml, and evals call the runtime API over HTTP. The demo target runs as separate processes and imports neither.
Commands

Target commands, to be kept accurate as they are implemented:

docker compose up starts Postgres/pgvector and the services.
uv run pytest runs backend tests.
uv run ruff check . and uv run ruff format . for lint and format.
ask-and-do ingest --url <docs-url> runs ingestion (incremental by default, --reset for a full rebuild).
ask-and-do evals runs the eval suite and writes tables to prepare/evals/results/.
Conventions
Small, frequent commits with clear messages. Each feature ships with tests.
Ingestion has fixture-based tests using saved HTML pages in the repo.
Run ruff and pytest before committing. Do not commit with failing tests.
Update the phase checklist in docs/PLAN.md and the status note in README.md as phases complete.
Keep the README honest about status ("in progress" until it is not).
Ask before deleting files or changing the folder layout.
Name the eval folder prepare/evals/cases/, not data/. The .gitignore anchors data ignores to the repo root (/corpus/, /pgdata/), and prepare/evals/results/ must stay tracked.