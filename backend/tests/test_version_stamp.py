"""The displayed app version, with and without a build stamp.

Test builds handed to users must identify themselves — two dispatch builds of
different commits otherwise both report the same /VERSION string and nobody can
tell which binary is installed. Release builds must NOT carry the stamp, so
`vX.Y.Z` ships as a clean `X.Y.Z`.
"""

import importlib
from pathlib import Path

import pytest

from app import _version


class TestVersionComposition:
    def test_stamp_is_appended_as_build_metadata(self):
        assert _version._compose("0.2.1", "3894c8a") == "0.2.1+3894c8a"

    def test_no_stamp_leaves_the_version_clean(self):
        """What a release build produces."""
        assert _version._compose("0.2.2", "") == "0.2.2"

    def test_dirty_marker_survives(self):
        """A stamp from a modified tree keeps its marker — it is a warning."""
        assert _version._compose("0.2.1", "3894c8a (modified)") == (
            "0.2.1+3894c8a (modified)"
        )


class TestBuildStampDiscovery:
    def test_missing_build_info_reads_as_no_stamp(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_version, "_candidate_dirs", lambda: [tmp_path])
        assert _version._read_build_stamp() == ""

    def test_build_info_is_read_and_stripped(self, monkeypatch, tmp_path):
        (tmp_path / "BUILD_INFO").write_text("3894c8a\n")
        monkeypatch.setattr(_version, "_candidate_dirs", lambda: [tmp_path])
        assert _version._read_build_stamp() == "3894c8a"

    def test_version_still_resolves_without_build_info(self):
        """The dev checkout has no BUILD_INFO — __version__ must still work."""
        assert _version.__version__
        assert "+" not in _version.__version__ or _version._read_build_stamp()

    def test_reported_version_matches_the_version_file(self):
        repo_version = (
            Path(__file__).resolve().parents[2] / "VERSION"
        ).read_text().strip()
        assert _version.__version__.split("+")[0] == repo_version
