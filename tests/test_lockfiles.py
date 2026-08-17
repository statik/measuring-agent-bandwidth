"""Tests for lockfile discovery and parsing (pure logic, no network)."""

import pytest

from netimpact import lockfiles

UV_LOCK = """\
version = 1

[[package]]
name = "myproject"
version = "0.1.0"
source = { virtual = "." }

[[package]]
name = "AnyIO"
version = "4.4.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://example.com/anyio.tar.gz", size = 163930 }
wheels = [
    { url = "https://example.com/anyio-mac.whl", size = 86780 },
    { url = "https://example.com/anyio-win.whl", size = 90000 },
]

[[package]]
name = "sdist-only"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://example.com/s.tar.gz", size = 5000 }
"""

RENV_LOCK = """\
{
  "R": {"Version": "4.6.1"},
  "Packages": {
    "cli": {"Package": "cli", "Version": "3.6.2", "Source": "Repository", "Repository": "CRAN"},
    "mypkg": {"Package": "mypkg", "Version": "0.1", "Source": "GitHub"}
  }
}
"""

R_MANIFEST = '{"version": 1, "packages": {"R6": {}, "shiny": {}}}'
PYTHON_MANIFEST = '{"version": 1, "python": {"version": "3.12"}, "packages": {"ignored": {}}}'

REQUIREMENTS = """\
# comment
shiny==1.7.0
PyYAML>=6  # trailing comment
querychat[dash]
-r other.txt
--index-url https://example.com
package @ https://example.com/x.whl
"""


class TestParseGithubRepo:
    @pytest.mark.parametrize(
        "reference",
        [
            "statik/measuring-agent-bandwidth",
            "https://github.com/statik/measuring-agent-bandwidth",
            "https://github.com/statik/measuring-agent-bandwidth.git",
            "git@github.com:statik/measuring-agent-bandwidth.git",
        ],
    )
    def test_accepted_forms(self, reference):
        assert lockfiles.parse_github_repo(reference) == "statik/measuring-agent-bandwidth"

    def test_rejects_non_repo(self):
        with pytest.raises(ValueError, match="could not parse"):
            lockfiles.parse_github_repo("https://example.com/nothing")


class TestParseUvLock:
    def test_uses_largest_wheel_and_normalizes_names(self):
        sizes = lockfiles.parse_uv_lock(UV_LOCK)
        assert sizes["anyio"] == 90000

    def test_falls_back_to_sdist(self):
        assert lockfiles.parse_uv_lock(UV_LOCK)["sdist-only"] == 5000

    def test_skips_virtual_project(self):
        assert "myproject" not in lockfiles.parse_uv_lock(UV_LOCK)


class TestParseRenvLock:
    def test_includes_repository_sources_only(self):
        assert lockfiles.parse_renv_lock(RENV_LOCK) == ["cli"]


class TestParseManifest:
    def test_r_manifest_lists_packages(self):
        assert lockfiles.parse_manifest_json(R_MANIFEST) == ["R6", "shiny"]

    def test_python_manifest_contributes_nothing(self):
        assert lockfiles.parse_manifest_json(PYTHON_MANIFEST) == []


class TestParseRequirements:
    def test_extracts_names_skips_options_and_urls(self):
        names = lockfiles.parse_requirements_txt(REQUIREMENTS)
        assert names == ["shiny", "pyyaml", "querychat", "package"]
