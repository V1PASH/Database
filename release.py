#!/usr/bin/env python
"""Publish a release of minidb to PyPI.

    python release.py                  # patch bump: 0.7.0 -> 0.7.1
    python release.py --minor          # 0.7.0 -> 0.8.0
    python release.py --major          # 0.7.0 -> 1.0.0
    python release.py --version 1.2.3  # an exact version
    python release.py --dry-run        # do everything except upload and push
    python release.py --tag-only       # bump, commit, tag and push -- no upload

It checks the working tree, runs the tests, bumps the version, builds the
distributions, checks them, uploads them, and tags the release.

Once PyPI has this repository registered as a trusted publisher, use
``--tag-only``: the release workflow on GitHub does the uploading.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGE = "minidb-db"
REPOSITORY = "V1PASH/Database"
VERSION_FILE = ROOT / "minidb" / "__init__.py"
PYPI = "https://pypi.org/pypi"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class Failure(Exception):
    """Something stopped the release."""


def run(
    arguments: list[str], *, capture: bool = False, check: bool = True
) -> str:
    """Run a command, showing what it is."""
    print(f"  $ {' '.join(arguments)}")
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        capture_output=capture,
        text=True,
    )
    if check and result.returncode != 0:
        if capture:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
        raise Failure(f"{' '.join(arguments)} failed")
    return result.stdout if capture else ""


def git(*arguments: str, check: bool = True) -> str:
    return run(["git", *arguments], capture=True, check=check).strip()


def current_version() -> str:
    """The version in minidb/__init__.py."""
    text = VERSION_FILE.read_text()
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise Failure("could not find __version__ in minidb/__init__.py")
    return match.group(1)


def set_version(version: str) -> None:
    """Write a new version into minidb/__init__.py."""
    text = VERSION_FILE.read_text()
    updated = re.sub(
        r'^__version__ = "[^"]+"',
        f'__version__ = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    VERSION_FILE.write_text(updated)


def parse_version(version: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version)
    if not match:
        raise Failure(f"{version!r} is not a version like 1.2.3")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def bump(version: str, part: str) -> str:
    major, minor, patch = parse_version(version)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def published_versions() -> set[str]:
    """The versions already on PyPI."""
    try:
        with urllib.request.urlopen(f"{PYPI}/{PACKAGE}/json", timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return set()
        raise
    except OSError as error:
        raise Failure(f"could not reach PyPI: {error}") from error
    return set(data.get("releases", {}))


# ---------------------------------------------------------------------------
# the steps
# ---------------------------------------------------------------------------


def check_working_tree() -> None:
    """The tree has to be clean, and we have to be on main."""
    if git("status", "--porcelain"):
        raise Failure(
            "the working tree has changes. Commit them before releasing."
        )
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        raise Failure(f"releases are made from main, not {branch!r}")


def check_up_to_date() -> None:
    """The branch has to match the remote."""
    git("fetch", "--quiet", "origin")
    local = git("rev-parse", "HEAD")
    remote = git("rev-parse", "origin/main")
    if local != remote:
        raise Failure(
            "main is not in step with origin/main. Push or pull first."
        )


def run_tests() -> None:
    """The tests have to pass."""
    print("\nRunning the tests")
    run([sys.executable, "-m", "pytest", "tests/", "-q"])


def build() -> list[Path]:
    """Build the wheel and the source distribution."""
    print("\nBuilding")
    for stale in (ROOT / "dist").glob("*"):
        stale.unlink()
    run([sys.executable, "-m", "build"])
    artifacts = sorted((ROOT / "dist").glob("*"))
    if not artifacts:
        raise Failure("nothing was built")
    for artifact in artifacts:
        print(f"  {artifact.name}  ({artifact.stat().st_size // 1024} kB)")
    return artifacts


def check_artifacts(version: str) -> None:
    """The metadata has to be right."""
    print("\nChecking the distributions")
    run([sys.executable, "-m", "twine", "check", *sorted(str(p) for p in (ROOT / "dist").glob("*"))])

    import zipfile

    wheel = next((ROOT / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        )
        text = archive.read(metadata).decode("utf-8")
    if f"Version: {version}" not in text:
        raise Failure(f"the wheel says {version} is not what was expected")
    print(f"  the wheel is version {version}")


def upload(artifacts: list[Path]) -> None:
    """Upload to PyPI."""
    print("\nUploading to PyPI")
    run([sys.executable, "-m", "twine", "upload", *[str(p) for p in artifacts]])
    print(
        f"\n  https://pypi.org/project/{PACKAGE}/{current_version()}/"
    )
    print(
        "  PyPI's index takes a minute or two to catch up, so a fresh\n"
        "  pip install may not see it straight away."
    )


def commit_and_tag(version: str, push: bool) -> None:
    """Commit the version bump, tag it, and push."""
    tag = f"v{version}"
    print("\nCommitting the release")
    run(["git", "add", "minidb/__init__.py"])
    run(["git", "commit", "-m", f"Release {tag}"])
    run(["git", "tag", "-a", tag, "-m", f"Release {tag}"])

    if push:
        print("\nPushing")
        run(["git", "push", "origin", "main"])
        run(["git", "push", "origin", tag])
        print(f"  pushed {tag}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release.py",
        description="Publish a release of minidb to PyPI.",
    )
    parser.add_argument(
        "--version", help="the exact version to release, like 1.2.3"
    )
    parser.add_argument(
        "--minor", action="store_true", help="bump the minor version"
    )
    parser.add_argument(
        "--major", action="store_true", help="bump the major version"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do everything except upload and push",
    )
    parser.add_argument(
        "--tag-only",
        action="store_true",
        help="bump, commit, tag and push, but do not upload",
    )
    arguments = parser.parse_args(argv)

    try:
        print("Checking the repository")
        check_working_tree()
        check_up_to_date()

        version = current_version()
        if arguments.version:
            new_version = arguments.version
        elif arguments.major:
            new_version = bump(version, "major")
        elif arguments.minor:
            new_version = bump(version, "minor")
        else:
            new_version = bump(version, "patch")

        parse_version(new_version)
        if new_version == version:
            raise Failure("the new version is the same as the current one")

        already = published_versions()
        if new_version in already:
            raise Failure(
                f"{new_version} is already on PyPI. Bump the version higher."
            )

        print(f"  {version} -> {new_version}")
        run_tests()

        set_version(new_version)
        try:
            artifacts = build()
            check_artifacts(new_version)
        except Exception:
            set_version(version)   # put the version back
            raise

        if arguments.dry_run:
            print("\nDry run: nothing was uploaded or pushed.")
            return 0

        if not arguments.tag_only:
            upload(artifacts)

        commit_and_tag(new_version, push=True)

        if arguments.tag_only:
            print(
                "\n  The tag is pushed. The release workflow will upload\n"
                "  it, if this repository is a trusted publisher on PyPI."
            )
        print("\nDone.")
        return 0

    except Failure as error:
        print(f"\nStopped: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
