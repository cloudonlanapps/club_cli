"""The CLI's package version is the VERSION file at the repo root, and nothing else."""

import importlib.metadata
from pathlib import Path

VERSION = (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()


def test_should_take_the_package_version_from_the_version_file():
    assert importlib.metadata.version("club-cli") == VERSION
