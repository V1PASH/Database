"""``python -m minidb.webui`` -- open a database in the web UI."""

from __future__ import annotations

import argparse
import sys

from ..database import Database
from .server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="minidb-ui",
        description="Browse a minidb database in the browser.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="app.db",
        help="the database file to open (use ':memory:' for a scratch database)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="the address to bind to")
    parser.add_argument("--port", type=int, default=5433, help="the port to listen on")
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser window"
    )
    parser.add_argument("--verbose", action="store_true", help="log every request")
    arguments = parser.parse_args(argv)

    database = Database(arguments.path)
    try:
        serve(
            database,
            host=arguments.host,
            port=arguments.port,
            verbose=arguments.verbose,
            open_browser=not arguments.no_browser,
        )
    finally:
        database.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
