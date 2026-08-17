"""Calibrate conversation-size parameters from local agent session metadata.

Reads the SQLite database that agentsview (a local viewer for AI agent
sessions) syncs from coding-agent transcripts, and computes aggregate
statistics over per-request token usage: round trips per session, context
size at the first request, net context growth per request, and output tokens
per response. These are exactly the ChatItem parameters, so the output is a
drop-in grounding for the presets.

Privacy: only token-count metadata is queried. Conversation content, session
identifiers, and project names are never read, and the output contains only
aggregates.

Usage:
    uv run python -m netimpact.calibrate [path/to/sessions.db]
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass

BYTES_PER_REQUEST_TOKEN = 4.0
BYTES_PER_STREAMED_TOKEN = 30.0
PROTOCOL_OVERHEAD = 1.08

DEFAULT_DB = "~/.agentsview/sessions.db"
MIN_REQUESTS = 5

SESSION_QUERY = """
WITH pm AS (
  SELECT session_id, context_tokens, COALESCE(output_tokens, 0) AS output_tokens,
         ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY ordinal) AS rn,
         COUNT(*) OVER (PARTITION BY session_id) AS requests
  FROM messages
  WHERE role = 'assistant' AND has_context_tokens = 1 AND context_tokens > 0
)
SELECT MAX(requests),
       MAX(CASE WHEN rn = 1 THEN context_tokens END),
       MAX(CASE WHEN rn = requests THEN context_tokens END),
       SUM(context_tokens),
       SUM(output_tokens)
FROM pm
GROUP BY session_id
"""


@dataclass(frozen=True)
class SessionStats:
    """Token metadata for one agent session.

    Attributes:
        requests: API round trips in the session.
        first_context: Prompt tokens sent with the first request.
        last_context: Prompt tokens sent with the final request.
        uplink_tokens: Sum of prompt tokens across every request.
        output_tokens: Sum of generated tokens across every response.
    """

    requests: int
    first_context: int
    last_context: int
    uplink_tokens: int
    output_tokens: int

    def growth_per_request(self) -> float:
        """Net context growth per round trip (compactions included)."""
        if self.requests < 2:
            return 0.0
        return (self.last_context - self.first_context) / (self.requests - 1)

    def wire_megabytes(self) -> float:
        """Modeled wire transfer for the session, in decimal megabytes."""
        uplink = self.uplink_tokens * BYTES_PER_REQUEST_TOKEN
        downlink = self.output_tokens * BYTES_PER_STREAMED_TOKEN
        return (uplink + downlink) * PROTOCOL_OVERHEAD / 1e6


def load_sessions(db_path: str) -> list[SessionStats]:
    """Load per-session token metadata from an agentsview database.

    Args:
        db_path: Path to the sessions.db SQLite file.

    Returns:
        One SessionStats per session that has usage metadata.

    Raises:
        RuntimeError: If the database is missing or lacks the messages table.
    """
    path = os.path.expanduser(db_path)
    if not os.path.exists(path):
        raise RuntimeError(f"no agentsview database at {path}; run 'agentsview sync' first")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(SESSION_QUERY).fetchall()
    except sqlite3.OperationalError as error:
        raise RuntimeError(f"{path} does not look like an agentsview database: {error}") from error
    finally:
        connection.close()
    return [SessionStats(*row) for row in rows]


def percentile(values: list[float], fraction: float) -> float:
    """Return the nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    index = min(int(len(ordered) * fraction), len(ordered) - 1)
    return ordered[index]


def summarize(sessions: list[SessionStats], min_requests: int = MIN_REQUESTS) -> dict:
    """Aggregate session metadata into calibration statistics.

    Args:
        sessions: Per-session token metadata.
        min_requests: Sessions with fewer round trips are excluded, so
            one-shot invocations do not drag the medians down.

    Returns:
        Aggregate percentiles for each ChatItem parameter and for the modeled
        per-session wire transfer.

    Raises:
        RuntimeError: If no session meets the threshold.
    """
    kept = [s for s in sessions if s.requests >= min_requests]
    if not kept:
        raise RuntimeError(f"no sessions with at least {min_requests} requests to calibrate from")
    metrics = {
        "requests_per_session": [float(s.requests) for s in kept],
        "first_context_tokens": [float(s.first_context) for s in kept],
        "growth_tokens_per_request": [s.growth_per_request() for s in kept],
        "output_tokens_per_request": [s.output_tokens / s.requests for s in kept],
        "wire_mb_per_session": [s.wire_megabytes() for s in kept],
    }
    return {
        "sessions_total": len(sessions),
        "sessions_used": len(kept),
        "min_requests": min_requests,
        "metrics": {
            name: {
                "median": percentile(values, 0.5),
                "p75": percentile(values, 0.75),
                "p90": percentile(values, 0.9),
            }
            for name, values in metrics.items()
        },
    }


def format_summary(summary: dict) -> str:
    """Render calibration statistics with suggested ChatItem parameters."""
    metrics = summary["metrics"]
    lines = [
        f"Calibrated from {summary['sessions_used']} sessions with >= "
        f"{summary['min_requests']} round trips ({summary['sessions_total']} total). "
        "Token metadata only; no conversation content was read.",
        "",
        f"{'metric':34s} {'median':>10s} {'p75':>10s} {'p90':>10s}",
    ]
    for name, stats in metrics.items():
        lines.append(
            f"{name:34s} {stats['median']:>10.0f} {stats['p75']:>10.0f} {stats['p90']:>10.0f}"
            if name != "wire_mb_per_session"
            else f"{name:34s} {stats['median']:>10.2f} {stats['p75']:>10.2f} {stats['p90']:>10.2f}"
        )
    typical = (
        f"ChatItem(requests={metrics['requests_per_session']['median']:.0f}, "
        f"context_tokens_start={metrics['first_context_tokens']['median']:.0f}, "
        f"context_growth_tokens={metrics['growth_tokens_per_request']['median']:.0f}, "
        f"response_tokens={metrics['output_tokens_per_request']['median']:.0f})"
    )
    heavy = (
        f"ChatItem(requests={metrics['requests_per_session']['p75']:.0f}, "
        f"context_tokens_start={metrics['first_context_tokens']['p75']:.0f}, "
        f"context_growth_tokens={metrics['growth_tokens_per_request']['p75']:.0f}, "
        f"response_tokens={metrics['output_tokens_per_request']['p75']:.0f})"
    )
    lines += ["", f"typical session (medians): {typical}", f"heavy session (p75s):      {heavy}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    """Run the calibration CLI."""
    args = sys.argv[1:] if argv is None else argv
    db_path = args[0] if args else DEFAULT_DB
    try:
        summary = summarize(load_sessions(db_path))
    except RuntimeError as error:
        sys.exit(f"error: {error}")
    print(format_summary(summary))


if __name__ == "__main__":
    main()
