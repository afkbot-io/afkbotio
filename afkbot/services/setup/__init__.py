"""Setup service exports."""

from afkbot.services.setup.policy_setup import apply_setup_policy
from afkbot.services.setup.state import (
    SetupStateSnapshot,
    clear_setup_state,
    platform_is_bootstrapped,
    setup_is_complete,
    write_setup_state,
)

__all__ = [
    "SetupStateSnapshot",
    "apply_setup_policy",
    "clear_setup_state",
    "platform_is_bootstrapped",
    "setup_is_complete",
    "write_setup_state",
]
