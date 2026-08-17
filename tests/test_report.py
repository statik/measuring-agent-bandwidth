"""Tests for report rendering."""

from netimpact import engine, report


def sample_results() -> dict:
    items = [
        engine.MeasuredItem(
            name="Positron (macOS)",
            group="Installers",
            share=0.5,
            bytes_each=1_045_024_118,
            detail="Positron-2026.08.1-2-arm64.dmg",
        ),
        engine.MeasuredItem(
            name="R packages: querychat & <shinychat>",
            group="Workshop packages",
            share=1.0,
            bytes_each=50_000_000,
            detail="42 packages [resolved]",
        ),
    ]
    chats = [
        engine.ChatItem(
            name="First conversation",
            share=1.0,
            conversations=1,
            requests=16,
            context_tokens_start=4000,
            context_growth_tokens=2000,
            response_tokens=700,
        )
    ]
    return engine.summarize("Test # Workshop", 20, 6.0, items, chats)


class TestEscapeTypst:
    def test_escapes_markup_characters(self):
        assert report.escape_typst("a # b *c* [d] @e") == r"a \# b \*c\* \[d\] \@e"

    def test_plain_text_unchanged(self):
        assert (
            report.escape_typst("querychat 0.3.0 (42 packages)") == "querychat 0.3.0 (42 packages)"
        )


class TestTypstReport:
    def test_contains_measurements_and_escapes_names(self):
        source = report.typst_report(sample_results())
        assert "Test \\# Workshop" in source
        assert "1.05 GB" in source
        assert "\\[resolved\\]" in source

    def test_empty_scenario_renders(self):
        results = engine.summarize("Empty", 5, 2.0, [], [])
        source = report.typst_report(results)
        assert "None — nothing is downloaded" in source
        assert "None modeled" in source


class TestHtmlReport:
    def test_escapes_html_and_shows_verdict(self):
        results = sample_results()
        html = report.html_report(results)
        assert "&lt;shinychat&gt;" in html
        assert results["verdict"]["label"] in html
        assert "1.05 GB" in html


class TestSparkPercent:
    def test_sqrt_scaling_keeps_small_values_visible(self):
        assert report._spark_percent(25, 100) == 50.0

    def test_zero_maximum_is_safe(self):
        assert report._spark_percent(10, 0) == 0.0
