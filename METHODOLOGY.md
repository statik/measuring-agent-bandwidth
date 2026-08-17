# How netimpact models AI assistant traffic

netimpact treats the two halves of a workshop differently. Downloads are
**measured** — HEAD requests against publisher CDNs and package-index metadata
return real transfer sizes. Assistant traffic is **modeled**, because it cannot
be measured before the event and because it is small enough that no plausible
modeling error changes a venue decision. This document describes that model,
the rubric behind the usage presets, and the grounding for every assumption.
The constants live in [`netimpact/engine.py`](netimpact/engine.py) and are
printed in the Assumptions section of every report.

## The model

A **conversation** is one chat session. A **round trip** is one API request
and its streamed response. These are not the same as user turns: when an
assistant works agentically — runs code, reads a data frame, then answers —
each tool call is an additional round trip, so one user turn is typically 2–5
round trips.

Chat APIs are stateless: every round trip re-sends the entire conversation so
far. For a conversation of `R` round trips that starts at `C₀` context tokens
and grows by `G` tokens per round trip:

```
uplink   = [ R·C₀ + G·R(R−1)/2 ] × 4 bytes/token × 1.08
downlink =   R × T_resp         × 30 bytes/token × 1.08
```

The `R(R−1)/2` term is the quadratic cost of re-sending a growing context —
the dominant term for long conversations, and the reason the model sums the
context at every round trip rather than using an average.

## Constants and their grounding

| Constant | Value | Grounding |
|---|---|---|
| Request bytes per token | 4 | Tokenizers for English text and code average roughly 4 characters per token (the widely cited provider rule of thumb). Request bodies are UTF-8 JSON — overwhelmingly ASCII, so ~1 byte per character — and HTTP clients do not compress request bodies by default, so wire bytes ≈ characters. |
| Streamed bytes per token | 30 | Responses stream as server-sent events. Each delta event wraps a small piece of text in a JSON envelope plus SSE framing (`event:`/`data:` lines), roughly 100–130 bytes carrying one to a few tokens. Streamed responses are typically sent uncompressed over chunked transfer. 30 B/token is mid-range for observed chunking; downlink is the minor term regardless. |
| Protocol overhead | 1.08× | Header arithmetic: TCP/IP headers are ~40 bytes per ~1,500-byte packet (≈2.7%), TLS record framing adds ~1–2%, HTTP/2 framing ~1%, and per-connection handshakes amortize to little. 8% rounds the sum up. |
| Streaming rate | 75 tokens/s | Used only for the "all streams at once" instantaneous peak. Public model-throughput benchmarks put current chat models roughly in the 40–150 output-tokens/second range; 75 is mid-range. One stream is then 75 × 30 × 1.08 × 8 ≈ 0.02 Mbps. |
| Statelessness; caching | — | The Anthropic Messages API and OpenAI Chat Completions API are stateless by design: the client sends the full message history with each request. Provider-side prompt caching is a *server-side compute* optimization — the full prompt still crosses the wire so the server can match it against the cache. Caching therefore reduces cost and latency, not network transfer. |

Each of the byte constants is directly checkable: serialize a messages array
and divide its JSON length by the `input_tokens` the provider reports; capture
one streamed response with mitmproxy and divide body bytes by `output_tokens`.

## Calibration against real agent sessions

The agentic conversation shapes are calibrated against real usage rather than
guessed. `netimpact.calibrate` reads the SQLite database that agentsview (a
local session viewer for AI coding agents) syncs from local transcripts and
aggregates the per-request token metadata — request counts and token counts
only; conversation content, session identifiers, and project names are never
read, and only aggregates are published.

Calibrating against 597 real coding-agent sessions with at least 5 round
trips (out of 860 total) gives:

| Metric | Median | p75 | p90 |
|---|---|---|---|
| Round trips per session | 21 | 48 | 106 |
| First-request context (tokens) | 24,165 | 36,727 | 44,887 |
| Net context growth per round trip (tokens) | 1,482 | 2,404 | 4,631 |
| Output tokens per response | 334 | 572 | 811 |
| Modeled wire transfer per session | 4.4 MB | 14.9 MB | 51.2 MB |

The headline result: a real, professional agentic coding session — far heavier
use than a workshop exercise — moves a median of about 4.4 MB over the wire,
and even the 90th percentile is ~51 MB. The single largest session observed,
an extreme all-day outlier, modeled at ~1.35 GB — still smaller than two
Positron downloads.

Reproduce against your own transcripts with:

```bash
uv run python -m netimpact.calibrate            # reads ~/.agentsview/sessions.db
```

Two corrections came out of calibration. First-request context is much larger
than naive estimates: a modern coding agent's system prompt, tool schemas, and
project instructions start conversations near 24k tokens, not 4k. Responses
are smaller than assumed: ~330 median output tokens per round trip, not
700–900. The presets below incorporate both.

## The conversation rubric (presets)

The presets in [`netimpact/presets.py`](netimpact/presets.py) describe *shapes*
of conversations, not transcripts. The agentic blocks take their parameters
from the calibration medians and 75th percentiles above; the querychat block
follows that tool's design instead.

| Block | Conversations/student | Round trips | C₀ | Growth/trip | Response tokens |
|---|---|---|---|---|---|
| First conversation with Posit Assistant | 1 | 16 | 20,000 | 2,000 | 500 |
| querychat exercises | 2 | 10 | 2,500 | 600 | 300 |
| General assistant use | 4 | 21 | 24,000 | 1,700 | 400 |
| Sustained agentic sessions | 2 | 48 | 36,000 | 2,400 | 550 |

**First conversation.** The [getting-started
walkthrough](https://assistant.posit.co/docs/getting-started/#your-first-conversation)
suggests prompts like "Load the mtcars dataset and show me how mpg relates to
weight" and "Build a dashboard showing sales by region." These are agentic
turns: the assistant reads the live session, writes and runs code, inspects
results, and iterates. Four to five such turns at 3–4 round trips each gives
~16 round trips — shorter than the measured median session because it is a
guided introduction. C₀ of 20,000 and growth of 2,000/trip follow the
calibration (median 24k start, median–p75 growth), slightly discounted for an
assistant with a leaner instruction set than a full development CLI.

**querychat exercises.** Grounded in querychat's actual design: it embeds the
table schema in a system prompt and the model answers each question with SQL
(plus a short explanation), one round trip per question. Ten questions per app
session, small context growth because the transcript is Q&A text rather than
tool output, and ~300-token responses (a SELECT statement is short).

**General assistant use.** The measured *median* session, almost verbatim:
21 round trips, 24k starting context, ~1,700 growth, ~400-token responses —
about 4 MB per conversation, matching the calibrated median of 4.4 MB. Four
of these per student over an afternoon is a generous allowance.

**Sustained agentic sessions.** The measured *75th-percentile* session:
48 round trips from a 36k-token start, ~20 MB per conversation, twice a day.
This is professional-developer-grade usage; it exists so the "full day, heavy
use" preset overshoots a workshop rather than undershoots it.

## Worked example

First conversation, one student, straight from the formula:

```
request tokens = 16 × 20,000 + 2,000 × (16 × 15 / 2) = 560,000 tokens
uplink         = 560,000 × 4 × 1.08                   ≈ 2.42 MB
downlink       = 16 × 500 × 30 × 1.08                 ≈ 0.26 MB
total                                                 ≈ 2.68 MB
```

This matches the 2.68 MB per-conversation figure in the example report.

## Sensitivity

The model's job is order-of-magnitude honesty, not precision, and the
conclusion is insensitive to its parameters. In the example workshop the
assistant total is 385.78 MB against a 33.66 GB download burst — about 1% of
the total. A 10× modeling error — every conversation ten times larger than
the calibrated shapes — puts the room at 3.9 GB, still about a tenth of the
burst. The instantaneous peak is bounded by streaming physics: all 20
assistants streaming at once draw ~0.39 Mbps.

## What the model does not count

- Authentication handshakes, telemetry, and update checks — a few kilobytes.
- Web and documentation browsing during exercises — a venue baseline that
  exists with or without the workshop.
- Students pasting unusually large content into chat. A pasted 10 MB dataset
  is 10 MB of uplink (once, plus re-sends within that conversation); workshop
  materials that instead load data from disk — as the Posit Assistant flow
  does — avoid this entirely.
- The choice of provider or the Posit AI managed service: the wire pattern
  (JSON request up, SSE stream down) is the same across providers.

## Validating the model

- Proxy the IDE through mitmproxy and compare per-host byte counts for the
  provider's API host against the model while running the workshop script.
- Multiply the token usage the provider's dashboard reports for a session by
  the byte constants above and compare to a per-process counter
  (`nettop` on macOS, Resource Monitor on Windows).
- At the venue, compare a pilot student's access-point byte counter against
  the per-student column of the report.
