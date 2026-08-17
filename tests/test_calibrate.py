"""Tests for calibration from agent session metadata."""

import sqlite3

import pytest

from netimpact import calibrate


def make_db(path, sessions):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE messages (session_id TEXT, ordinal INTEGER, role TEXT,"
        " has_context_tokens INTEGER, context_tokens INTEGER, output_tokens INTEGER)"
    )
    for session_id, requests in sessions.items():
        for ordinal, (context, output) in enumerate(requests, start=1):
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, 'assistant', 1, ?, ?)",
                (session_id, ordinal, context, output),
            )
    connection.commit()
    connection.close()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "sessions.db"
    make_db(
        str(path),
        {
            "long": [(10000 + 1000 * i, 200) for i in range(6)],
            "short": [(5000, 100), (6000, 100)],
        },
    )
    return str(path)


class TestLoadSessions:
    def test_parses_per_session_stats(self, db_path):
        sessions = {s.requests: s for s in calibrate.load_sessions(db_path)}
        long = sessions[6]
        assert long.first_context == 10000
        assert long.last_context == 15000
        assert long.uplink_tokens == sum(10000 + 1000 * i for i in range(6))
        assert long.output_tokens == 1200
        assert long.growth_per_request() == 1000

    def test_missing_database_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="no agentsview database"):
            calibrate.load_sessions(str(tmp_path / "absent.db"))

    def test_wrong_schema_raises(self, tmp_path):
        path = tmp_path / "other.db"
        sqlite3.connect(str(path)).execute("CREATE TABLE t (x)")
        with pytest.raises(RuntimeError, match="does not look like"):
            calibrate.load_sessions(str(path))


class TestSummarize:
    def test_excludes_short_sessions(self, db_path):
        summary = calibrate.summarize(calibrate.load_sessions(db_path))
        assert summary["sessions_total"] == 2
        assert summary["sessions_used"] == 1
        assert summary["metrics"]["requests_per_session"]["median"] == 6

    def test_no_qualifying_sessions_raises(self, db_path):
        sessions = calibrate.load_sessions(db_path)
        with pytest.raises(RuntimeError, match="at least 50 requests"):
            calibrate.summarize(sessions, min_requests=50)

    def test_wire_megabytes_uses_model_constants(self):
        session = calibrate.SessionStats(
            requests=5,
            first_context=1000,
            last_context=1000,
            uplink_tokens=100_000,
            output_tokens=1_000,
        )
        expected = (100_000 * 4.0 + 1_000 * 30.0) * 1.08 / 1e6
        assert session.wire_megabytes() == pytest.approx(expected)


class TestFormatSummary:
    def test_mentions_privacy_and_suggests_parameters(self, db_path):
        summary = calibrate.summarize(calibrate.load_sessions(db_path))
        text = calibrate.format_summary(summary)
        assert "no conversation content" in text
        assert "ChatItem(requests=6" in text
