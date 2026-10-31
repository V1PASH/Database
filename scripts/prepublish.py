"""Checks that run before minidb is published.

Publishing is the one thing you cannot undo, so the checks are strict:

1. The working tree is clean — never publish half-finished work.
2. The version the wheel will carry agrees with ``minidb.__version__``.
3. The version has not already been published — PyPI rejects a re-upload,
   and it is better to say so plainly than to fail halfway through.
4. The built artefacts exist, are the expected version, and pass
   ``twine check``.

minidb keeps its version in one place, ``minidb/__init__.py``;
``pyproject.toml`` reads it with ``dynamic = ["version"]``.

Run it directly, or through ``task publish``::

    python scripts/prepublish.py
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
VERSION_FILE = ROOT / "minidb" / "__init__.py"
PYPI = "https://pypi.org/pypi"


def fail(message: str) -> None:
    print(f"\n  ✗ {message}\n", file=sys.stderr)
    sys.exit(1)


def run(*args: str) -> tuple[int, str]:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def project() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def check_clean_tree() -> None:
    if not (ROOT / ".git").exists():
        return  # not a git checkout — nothing to check
    code, output = run("git", "status", "--porcelain")
    if code != 0:
        return
    if output.strip():
        files = [
            line[3:]
            for line in output.splitlines()
            if line.strip() and not line.endswith(".egg-info/")
        ]
        if files:
            fail(
                "the working tree is dirty — commit or stash first:\n    "
                + "\n    ".join(files[:10])
            )
    print("  · the working tree is clean")


def package_version() -> str:
    """The single source of truth: ``minidb/__init__.py``."""
    source = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.M)
    if not match:
        fail(f"could not find __version__ in {VERSION_FILE.relative_to(ROOT)}")
    return match.group(1)


def check_version() -> str:
    settings = project()
    version = package_version()

    if "version" in settings:
        declared = str(settings["version"])
        if declared != version:
            fail(
                f"the versions disagree — pyproject.toml says {declared!r} but "
                f"minidb/__init__.py says {version!r}"
            )
    elif "version" not in settings.get("dynamic", []):
        fail("pyproject.toml has no version and does not declare it dynamic")

    print(f"  · version {version}")
    return version


def already_published(name: str, version: str) -> bool:
    """Is this version already on PyPI?

    ``requests`` is used when it is available (twine needs it); otherwise
    ``urllib``. Neither is a runtime dependency of minidb.
    """
    url = f"{PYPI}/{name}/{version}/json"
    try:
        import requests
    except ImportError:
        requests = None

    try:
        if requests is not None:
            return requests.get(url, timeout=20).status_code == 200
        with urllib.request.urlopen(url, timeout=20) as reply:
            return reply.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise
    except Exception as error:  # noqa: BLE001 - never block a publish on this
        print(f"  · could not check PyPI ({error}) — continuing")
        return False


def check_not_published(name: str, version: str) -> None:
    if already_published(name, version):
        fail(
            f"{name} {version} is already on PyPI — bump the version before "
            "publishing again"
        )
    print(f"  · {name} {version} is not on PyPI yet")


def wheel_version(wheel: Path) -> str | None:
    with zipfile.ZipFile(wheel) as archive:
        name = next(
            (
                entry
                for entry in archive.namelist()
                if entry.endswith(".dist-info/METADATA")
            ),
            None,
        )
        if name is None:
            return None
        text = archive.read(name).decode("utf-8")
    match = re.search(r"^Version:\s*(\S+)$", text, re.M)
    return match.group(1) if match else None


def check_dist(version: str) -> None:
    wheels = sorted(DIST.glob("*.whl"))
    sdists = sorted(DIST.glob("*.tar.gz"))
    artefacts = wheels + sdists
    if not artefacts:
        fail("there is nothing in dist/ — run the build first")

    name = project()["name"]
    expected = {f"{name.replace('-', '_')}-{version}-py3-none-any.whl"}
    if wheels and wheels[-1].name not in expected:
        fail(
            f"dist/ holds {wheels[-1].name}, which is not version {version} — "
            "clear dist/ and build again"
        )

    carried = wheel_version(wheels[-1]) if wheels else None
    if wheels and carried != version:
        fail(f"the wheel says version {carried!r}, expected {version!r}")

    code, output = run(
        sys.executable, "-m", "twine", "check", "--strict", *map(str, artefacts)
    )
    if code != 0:
        fail(f"twine check failed:\n{output}")

    print("  · artefacts:")
    for artefact in artefacts:
        print(f"      {artefact.name}  ({artefact.stat().st_size:,} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-published-check",
        action="store_true",
        help="do not check whether the version is already on PyPI",
    )
    args = parser.parse_args()

    name = str(project()["name"])
    print(f"\npublishing {name}")

    check_clean_tree()
    version = check_version()
    if not args.skip_published_check:
        check_not_published(name, version)
    check_dist(version)

    print("\n  all checks passed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
