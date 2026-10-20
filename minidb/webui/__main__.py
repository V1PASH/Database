"""``python -m minidb.webui`` -- browse your databases in the browser."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .server import serve
from .workspace import Workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="minidb-ui",
        description=(
            "Browse your minidb databases in the browser. "
            "Point it at a database file, or at a directory to find them all."
        ),
    )
    parser.add_argument(
        "path",
        nargs="*",
        default=[],
        help=(
            "the database files or directories to browse "
            "(default: this directory)"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="the address to bind to")
    parser.add_argument(
        "--port",
        type=int,
        default=5433,
        help="the port to listen on (default: 5433)",
    )
    parser.add_argument(
        "--no-registry",
        action="store_true",
        help="do not remember the databases that were opened",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser window"
    )
    parser.add_argument("--verbose", action="store_true", help="log every request")
    arguments = parser.parse_args(argv)

    roots = [Path(path).expanduser() for path in arguments.path] or [Path.cwd()]
    for root in roots:
        if not root.exists():
            parser.error(f"no such file or directory: {root}")

    workspace = Workspace(roots, use_registry=not arguments.no_registry)
    try:
        serve(
            workspace,
            host=arguments.host,
            port=arguments.port,
            verbose=arguments.verbose,
            open_browser=not arguments.no_browser,
        )
    finally:
        workspace.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
