"""Tests for the measurement and estimation engine (pure logic, no network)."""

import pytest

from netimpact import engine

SAMPLE_PACKAGES = """\
Package: querychat
Version: 0.3.0
Depends: R (>= 4.1.0)
Imports: bsicons, bslib (>= 0.11.0), cli,
        ellmer (>= 0.4.1), MASS
License: MIT + file LICENSE

Package: bslib
Version: 0.11.0
Imports: cli
License: MIT + file LICENSE
"""


def chat(requests: int = 2, start: int = 1000, growth: int = 500, response: int = 100):
    return engine.ChatItem(
        name="test",
        share=1.0,
        conversations=1,
        requests=requests,
        context_tokens_start=start,
        context_growth_tokens=growth,
        response_tokens=response,
    )


class TestConversationBytes:
    def test_uplink_resends_growing_context(self):
        uplink, _ = engine.conversation_bytes(chat())
        assert uplink == int((2 * 1000 + 500) * 4.0 * 1.08)

    def test_downlink_uses_streamed_token_size(self):
        _, downlink = engine.conversation_bytes(chat())
        assert downlink == int(2 * 100 * 30.0 * 1.08)

    def test_single_request_has_no_growth(self):
        uplink, _ = engine.conversation_bytes(chat(requests=1))
        assert uplink == int(1000 * 4.0 * 1.08)


class TestFormatBytes:
    def test_bytes(self):
        assert engine.format_bytes(999) == "999 B"

    def test_megabytes(self):
        assert engine.format_bytes(105_066_342) == "105.07 MB"

    def test_whole_gigabytes_drop_decimals(self):
        assert engine.format_bytes(1_000_000_000) == "1 GB"


class TestRequirementName:
    def test_versioned(self):
        assert engine.requirement_name("chatlas>=0.18.0") == "chatlas"

    def test_extra_marker_is_skipped(self):
        assert engine.requirement_name('dash-ag-grid>=31.0; extra == "dash"') is None

    def test_name_is_normalized(self):
        assert engine.requirement_name("PyYAML") == "pyyaml"

    def test_garbage_returns_none(self):
        assert engine.requirement_name("===") is None


class TestPypiDistributionSize:
    def test_prefers_universal_wheel(self):
        payload = {
            "urls": [
                {"packagetype": "bdist_wheel", "filename": "x-1.0-cp313-macosx.whl", "size": 900},
                {"packagetype": "bdist_wheel", "filename": "x-1.0-py3-none-any.whl", "size": 100},
            ]
        }
        assert engine.pypi_distribution_size(payload) == 100

    def test_uses_largest_platform_wheel(self):
        payload = {
            "urls": [
                {"packagetype": "bdist_wheel", "filename": "x-1.0-cp313-macosx.whl", "size": 900},
                {"packagetype": "bdist_wheel", "filename": "x-1.0-cp313-win.whl", "size": 700},
            ]
        }
        assert engine.pypi_distribution_size(payload) == 900

    def test_falls_back_to_sdist(self):
        payload = {"urls": [{"packagetype": "sdist", "filename": "x-1.0.tar.gz", "size": 50}]}
        assert engine.pypi_distribution_size(payload) == 50

    def test_no_files_raises(self):
        with pytest.raises(RuntimeError, match="no downloadable files"):
            engine.pypi_distribution_size({"urls": [], "info": {"name": "x"}})


class TestCranMetadata:
    def test_parses_stanzas_and_continuations(self):
        packages = engine.parse_cran_metadata(SAMPLE_PACKAGES)
        assert packages["querychat"]["Version"] == "0.3.0"
        assert "ellmer (>= 0.4.1)" in packages["querychat"]["Imports"]
        assert packages["bslib"]["Imports"] == "cli"

    def test_dependencies_exclude_r_and_bundled(self):
        packages = engine.parse_cran_metadata(SAMPLE_PACKAGES)
        deps = engine.cran_dependencies(packages["querychat"])
        assert deps == ["bsicons", "bslib", "cli", "ellmer"]


class TestSummarize:
    def test_totals_and_shares(self):
        items = [
            engine.MeasuredItem(name="A", group="G", share=0.5, bytes_each=1_000_000),
            engine.MeasuredItem(name="B", group="G", share=1.0, bytes_each=2_000_000),
        ]
        results = engine.summarize("W", 10, 2.0, items, [chat()])
        assert results["one_time"]["aggregate_bytes"] == 5_000_000 + 20_000_000
        assert results["verdict"]["label"] == "LIGHT"
        assert results["assistant"]["aggregate_bytes"] > 0

    def test_verdict_scales_with_download_volume(self):
        big = [engine.MeasuredItem(name="A", group="G", share=1.0, bytes_each=3_000_000_000)]
        moderate = engine.summarize("W", 5, 2.0, big, [])
        heavy = engine.summarize("W", 20, 2.0, big, [])
        assert moderate["verdict"]["label"] == "MODERATE"
        assert heavy["verdict"]["label"] == "PLAN DOWNLOADS"
