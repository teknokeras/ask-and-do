# ask-and-do

A reference implementation of a docs assistant that answers with citations and performs allowlisted API actions behind one chat box. It crawls a docs site into a vector store, turns an OpenAPI spec into curated MCP tools, and ships an embeddable React component, with evals for grounding, routing and tool selection.

Each piece has existing tools (see [Related work](#related-work)). This project shows how they fit together and measures the result.

## Status

**In progress. Nothing here is ready for use yet.**

| Part | State |
|---|---|
| Docs ingestion (crawl, extract, chunk, embed, store) | Working |
| Demo docs site (28 pages) | Working |
| Mock admin API | Not started |
| `search_docs` and chat endpoint | In progress: hybrid retrieval and not-found gate built; answers, quote verification and the chat endpoint not started |
| Tool gateway and routing | Not started |
| Evals | Not started |
| React component | Not started |

The plan and design are in [docs/PLAN.md](docs/PLAN.md).

## Data

All data is public: public documentation sites, plus the fictional Larkspur demo docs and a mock admin API built in this repo.

## Try the ingest step

Needs [uv](https://docs.astral.sh/uv/), [just](https://just.systems/) and Docker.

```sh
uv sync
just db-up       # start Postgres with pgvector (creates .env if missing)
just docs-serve  # serve the demo docs on http://localhost:8001

# in a second terminal
just ingest --url http://localhost:8001/
```

Run the checks with `uv run pytest` and `uv run ruff check .`. Tests that need the database are skipped unless it is running.

## Eval results

No results yet. This table will be filled from `prepare/evals/results/`, including failures and steps that did not help.

| Configuration | Route accuracy | Tool selection | Wrong-write rate | Grounded answers | Refusal on unanswerable |
|---|---|---|---|---|---|
| Raw generated tools | – | – | – | – | – |
| Lint and rewritten descriptions | – | – | – | – | – |
| Plus clarification and read-before-write | – | – | – | – | – |

## Related work

- **Tool linting and tool-selection testing:** agenticlint, MCPLint, mcpolish, mcp-openapi-doctor, agent-ready, mcp-tef.
- **OpenAPI to MCP and curation:** FastMCP, openapi-mcp-generator, Speakeasy and Gram, Stainless.
- **Chat over an API with confirmations and user session passthrough:** any2agent.
- **Docs assistants:** Kapa, Inkeep, Mintlify's assistant.

## License

MIT. See [LICENSE](LICENSE).
