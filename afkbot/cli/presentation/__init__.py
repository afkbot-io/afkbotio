"""CLI presentation primitives."""

from afkbot.cli.presentation.activity_indicator import ActivityIndicator
from afkbot.cli.presentation.chat_interactive import InteractiveChatUX
from afkbot.cli.presentation.chat_plan_renderer import render_chat_plan
from afkbot.cli.presentation.chat_renderer import render_chat_result
from afkbot.cli.presentation.chat_turn_output import render_chat_turn_outcome
from afkbot.cli.presentation.inline_select import confirm_space
from afkbot.cli.presentation.progress_mapper import map_progress_event
from afkbot.cli.presentation.progress_renderer import (
    render_progress_color,
    render_progress_detail,
    render_progress_event,
)
from afkbot.cli.presentation.progress_timeline import (
    ProgressTimelineState,
    reduce_progress_event,
)

__all__ = [
    "ActivityIndicator",
    "InteractiveChatUX",
    "confirm_space",
    "map_progress_event",
    "ProgressTimelineState",
    "reduce_progress_event",
    "render_chat_plan",
    "render_chat_result",
    "render_chat_turn_outcome",
    "render_progress_color",
    "render_progress_detail",
    "render_progress_event",
]
