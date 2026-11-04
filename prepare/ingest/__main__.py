"""Command line: uv run python -m prepare.ingest --url https://docs.example.com/

Incremental by default: unchanged pages are skipped and only changed chunks
are embedded. --reset drops the chunks table and rebuilds it, which is needed
after changing EMBED_MODEL.
"""

import argparse
import sys

import psycopg

from common.embedder import get_embedder
from common.settings import get_settings
from prepare.ingest.fetcher import HttpFetcher
from prepare.ingest.pipeline import ingest
from prepare.ingest.store import EmbedderMismatch
from prepare.ingest.urls import Scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingest", description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True, help="start URL; its path is the default prefix")
    parser.add_argument("--prefix", help="only crawl URLs under this path (default: from --url)")
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="REGEX",
        help="skip URLs matching this pattern; repeatable",
    )  # fmt: skip
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    parser.add_argument("--no-sitemap", action="store_true", help="follow links instead")
    parser.add_argument("--doc-version", help="stored with each chunk, e.g. 'v2'")
    parser.add_argument("--reset", action="store_true", help="drop all chunks and rebuild")
    args = parser.parse_args(argv)

    settings = get_settings()
    scope = Scope(args.url, path_prefix=args.prefix, exclude=args.exclude, max_pages=args.max_pages)
    embedder = get_embedder(settings)
    fetcher = HttpFetcher()
    try:
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            report = ingest(
                scope, fetcher, embedder, conn,
                reset=args.reset, doc_version=args.doc_version,
                delay=args.delay, use_sitemap=not args.no_sitemap,
            )  # fmt: skip
    except EmbedderMismatch as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except psycopg.OperationalError:
        # The message can include connection details; keep it to a hint.
        print("error: cannot connect to the database. Is it running? (just db-up)", file=sys.stderr)
        return 1
    finally:
        fetcher.close()

    print(report.summary())
    return 1 if report.crawl.aborted else 0


if __name__ == "__main__":
    sys.exit(main())
