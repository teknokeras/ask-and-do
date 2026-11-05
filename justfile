# Run `just` to list commands.

set dotenv-load := true

[private]
default:
    @just --list

# Start the database and wait until it is healthy
db-up: _env _docker
    docker compose up -d --wait db
    @echo "database ready on localhost:${POSTGRES_PORT:-5432}"

# Stop the database (data is kept)
db-down:
    docker compose down

# Follow the database logs
db-logs:
    docker compose logs -f db

# Open a psql shell in the database
db-shell:
    docker compose exec db psql -U "${POSTGRES_USER:-askanddo}" -d "${POSTGRES_DB:-askanddo}"

# Delete the database and all its data, then start a fresh one
[confirm("This deletes all data in the database. Continue? [y/N]")]
db-reset: _docker
    docker compose down -v
    just db-up

# Crawl a docs site into the database, e.g. just ingest --url https://www.python-httpx.org/
ingest *args: _env
    uv run python -m prepare.ingest {{args}}

# Build the Larkspur demo docs (the test corpus) into prepare/demo/docs_site/build/
docs-build port="8001":
    uv run python -m prepare.demo.docs_site.build --base-url http://localhost:{{port}}/

# Build the demo docs and serve them on http://localhost:8001 (then: just ingest --url http://localhost:8001/)
docs-serve port="8001": (docs-build port)
    @echo "serving http://localhost:{{port}}/ (Ctrl+C to stop)"
    uv run python -m http.server {{port}} --bind 127.0.0.1 --directory prepare/demo/docs_site/build

# Create .env from .env.example if it does not exist
_env:
    @[ -f .env ] || { cp .env.example .env; echo "created .env from .env.example (gitignored); change POSTGRES_PASSWORD if you like"; }

# Fail early with a clear message if Docker is not running
_docker:
    @docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and try again."; exit 1; }
