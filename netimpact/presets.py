"""Assistant usage presets.

Each preset is a list of modeled activity blocks. The token counts describe
how a conversation's context grows: chat APIs are stateless, so every round
trip re-sends the conversation so far. An agentic user turn (the assistant
runs code, reads data, then answers) is typically 2-5 round trips.

The agentic blocks are calibrated against aggregate token metadata from 597
real coding-agent sessions (medians: 21 round trips, 24k-token starting
context, ~1.5k tokens of growth per round trip, ~330 output tokens per
response). The querychat block instead follows that tool's design: one round
trip per question, schema-sized prompt, SQL-sized answers. See METHODOLOGY.md
for the grounding and the calibration procedure.
"""

from __future__ import annotations

from netimpact.engine import ChatItem

FIRST_CONVERSATION = ChatItem(
    name="First conversation with Posit Assistant",
    share=1.0,
    conversations=1,
    requests=16,
    context_tokens_start=20000,
    context_growth_tokens=2000,
    response_tokens=500,
)

QUERYCHAT_EXERCISES = ChatItem(
    name="querychat exercises (natural-language data queries)",
    share=1.0,
    conversations=2,
    requests=10,
    context_tokens_start=2500,
    context_growth_tokens=600,
    response_tokens=300,
)

GENERAL_USE = ChatItem(
    name="General assistant use (debugging, plots, dashboards)",
    share=1.0,
    conversations=4,
    requests=21,
    context_tokens_start=24000,
    context_growth_tokens=1700,
    response_tokens=400,
)

SUSTAINED_AGENTIC = ChatItem(
    name="Sustained agentic sessions (long tool-use loops)",
    share=1.0,
    conversations=2,
    requests=48,
    context_tokens_start=36000,
    context_growth_tokens=2400,
    response_tokens=550,
)

PRESETS: dict[str, list[ChatItem]] = {
    "none": [],
    "first-conversation": [FIRST_CONVERSATION],
    "half-day-workshop": [FIRST_CONVERSATION, QUERYCHAT_EXERCISES, GENERAL_USE],
    "full-day-heavy": [FIRST_CONVERSATION, QUERYCHAT_EXERCISES, GENERAL_USE, SUSTAINED_AGENTIC],
}

PRESET_LABELS: dict[str, str] = {
    "none": "None — no AI assistant",
    "first-conversation": "First conversation only",
    "half-day-workshop": "Half-day workshop (getting started + exercises)",
    "full-day-heavy": "Full day with heavy agentic use",
}
