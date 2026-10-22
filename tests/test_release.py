"""Tests for the release script.

The script is the thing that publishes to PyPI, so its version arithmetic
and its file handling are worth pinning down.
"""

from __future__ import annotations

import pytest

import release


def test_the_bump_arithmetic():
    assert release.bump("1.2.3", "patch") == "1.2.4"
    assert release.bump("1.2.3", "minor") == "1.3.0"
    assert release.bump("1.2.3", "major") == "2.0.0"
    assert release.bump("0.7.0", "patch") == "0.7.1"
    assert release.bump("0.9.9", "minor") == "0.10.0"


def test_a_version_has_to_look_like_a_version():
    assert release.parse_version("1.2.3") == (1, 2, 3)
    with pytest.raises(release.Failure):
        release.parse_version("1.2")
    with pytest.raises(release.Failure):
        release.parse_version("not-a-version")


def test_the_version_in_the_file_matches_the_package():
    import minidb

    assert release.current_version() == minidb.__version__


def test_writing_the_version(tmp_path, monkeypatch):
    target = tmp_path / "__init__.py"
    target.write_text('"""doc"""\n\n__version__ = "1.0.0"\n\nother = 1\n')
    monkeypatch.setattr(release, "VERSION_FILE", target)

    assert release.current_version() == "1.0.0"
    release.set_version("1.2.3")

    text = target.read_text()
    assert '__version__ = "1.2.3"' in text
    assert "other = 1" in text          # the rest of the file is untouched
    assert release.current_version() == "1.2.3"


def test_writing_the_version_only_changes_the_version_line(tmp_path, monkeypatch):
    target = tmp_path / "__init__.py"
    target.write_text('__version__ = "1.0.0"\n')
    monkeypatch.setattr(release, "VERSION_FILE", target)

    release.set_version("2.0.0")
    assert target.read_text() == '__version__ = "2.0.0"\n'


def test_the_script_refuses_a_bad_version():
    with pytest.raises(release.Failure):
        release.parse_version("x")
