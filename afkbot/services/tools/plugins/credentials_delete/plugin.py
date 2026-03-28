"""Tool plugin for credentials.delete."""

from __future__ import annotations

from pydantic import ConfigDict, Field, model_validator

from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class CredentialsDeleteParams(ToolParameters):
    """Parameters for credentials.delete tool."""

    model_config = ConfigDict(extra="forbid")

    app_name: str = Field(min_length=1, max_length=64)
    profile_name: str = Field(default="default", min_length=1, max_length=64)
    credential_slug: str = Field(min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        integration_name = data.pop("integration_name", None)
        credential_profile_key = data.pop("credential_profile_key", None)
        credential_name = data.pop("credential_name", None)
        if "app_name" not in data and integration_name is not None:
            data["app_name"] = integration_name
        if "profile_name" not in data and credential_profile_key is not None:
            data["profile_name"] = credential_profile_key
        if "credential_slug" not in data and credential_name is not None:
            data["credential_slug"] = credential_name
        return data


class CredentialsDeleteTool(ToolBase):
    """Deactivate encrypted credential binding."""

    name = "credentials.delete"
    description = "Delete(deactivate) encrypted credential binding."
    parameters_model = CredentialsDeleteParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = CredentialsDeleteParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        try:
            service = get_credentials_service(self._settings)
            await service.delete(
                profile_id=ctx.profile_id,
                tool_name="app.run",
                integration_name=payload.app_name,
                credential_profile_key=payload.profile_name,
                credential_name=payload.credential_slug,
            )
            return ToolResult(
                ok=True,
                payload={
                    "deleted": True,
                    "app_name": payload.app_name,
                    "profile_name": payload.profile_name,
                    "credential_slug": payload.credential_slug,
                },
            )
        except CredentialsServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create credentials.delete tool instance."""

    return CredentialsDeleteTool(settings=settings)
