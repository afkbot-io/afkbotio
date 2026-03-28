"""Unified app runtime for `app.run` tool dispatch."""

from __future__ import annotations

from collections.abc import Mapping

from afkbot.services.apps.common import (
    AppCallContext,
    credentials_error_result,
    resolve_credential_placeholders,
)
from afkbot.services.credentials import CredentialsServiceError
from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.apps.registry import AppRegistry, get_app_registry
from afkbot.services.tools.base import ToolResult
from afkbot.settings import Settings

_APP_ACTION_ALIASES: Mapping[str, Mapping[str, str]] = {
    "telegram": {
        "sendmessage": "send_message",
        "sendphoto": "send_photo",
        "senddocument": "send_document",
        "sendchataction": "send_chat_action",
        "getme": "get_me",
        "getupdates": "get_updates",
        "banchatmember": "ban_chat_member",
        "unbanchatmember": "unban_chat_member",
    }
}


class AppRuntime:
    """Runtime dispatch entrypoint for integration app actions."""

    def __init__(self, settings: Settings, *, app_registry: AppRegistry | None = None) -> None:
        self._settings = settings
        self._app_registry = app_registry

    async def run(
        self,
        *,
        app: str,
        action: str,
        ctx: AppRuntimeContext,
        params: dict[str, object],
    ) -> ToolResult:
        """Run one app action and return canonical tool result."""

        normalized_app = app.strip().lower()
        normalized_action = action.strip().lower()
        if not normalized_app:
            return ToolResult.error(error_code="app_not_supported", reason="app is required")
        if not normalized_action:
            return ToolResult.error(error_code="app_action_not_supported", reason="action is required")

        app_registry = self._app_registry or get_app_registry(
            settings=self._settings,
            profile_id=ctx.profile_id,
        )
        app_definition = app_registry.get(normalized_app)
        if app_definition is None:
            return ToolResult.error(
                error_code="app_not_supported",
                reason=f"Unsupported app: {normalized_app}",
            )
        canonical_action = self._normalize_action_alias(
            app_name=normalized_app,
            normalized_action=app_definition.normalize_action(normalized_action),
        )
        if canonical_action not in app_definition.allowed_actions:
            return ToolResult.error(
                error_code="app_action_not_supported",
                reason=(
                    f"Unsupported {normalized_app} action: {action}. "
                    f"Supported actions: {', '.join(sorted(app_definition.allowed_actions))}."
                ),
                metadata={
                    "app_name": normalized_app,
                    "supported_actions": sorted(app_definition.allowed_actions),
                },
            )

        call_context = AppCallContext(
            profile_id=ctx.profile_id,
            app_name=normalized_app,
            action=canonical_action,
            profile_name=ctx.credential_profile_key,
        )
        try:
            resolved_params = await resolve_credential_placeholders(
                settings=self._settings,
                context=call_context,
                params={str(key): value for key, value in params.items()},
            )
        except CredentialsServiceError as exc:
            error_code, reason, metadata = credentials_error_result(
                exc=exc,
                context=call_context,
            )
            return ToolResult.error(
                error_code=error_code,
                reason=reason,
                metadata=metadata,
            )

        return await app_definition.handler(
            self._settings,
            ctx,
            canonical_action,
            resolved_params,
        )

    @staticmethod
    def _normalize_action_alias(*, app_name: str, normalized_action: str) -> str:
        """Map common external action aliases to canonical AFKBOT action names."""

        aliases = _APP_ACTION_ALIASES.get(app_name)
        if aliases is None:
            return normalized_action
        return aliases.get(normalized_action, normalized_action)
