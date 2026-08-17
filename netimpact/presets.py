"""Assistant usage presets.

Each preset is a list of modeled activity blocks. The token counts describe
how a conversation's context grows: chat APIs are stateless, so every round
trip re-sends the conversation so far. An agentic user turn (the assistant
runs code, reads data, then answers) is typically 2-5 round trips.
"""

from __future__ import annotations

from netimpact.engine import ChatItem

FIRST_CONVERSATION = ChatItem(
    name="First conversation with Posit Assistant",
    share=1.0,
    conversations=1,
    requests=16,
    context_tokens_start=4000,
    context_growth_tokens=2000,
    response_tokens=700,
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
    requests=12,
    context_tokens_start=4000,
    context_growth_tokens=2500,
    response_tokens=800,
)

SUSTAINED_AGENTIC = ChatItem(
    name="Sustained agentic sessions (long tool-use loops)",
    share=1.0,
    conversations=6,
    requests=24,
    context_tokens_start=6000,
    context_growth_tokens=3500,
    response_tokens=900,
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
