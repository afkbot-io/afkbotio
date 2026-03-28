"""Tool plugin for credentials.create."""

from __future__ import annotations

from pydantic import ConfigDict, Field, model_validator

from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class CredentialsCreateParams(ToolParameters):
    """Parameters for credentials.create tool."""

    model_config = ConfigDict(extra="forbid")

    app_name: str = Field(min_length=1, max_length=64)
    profile_name: str = Field(default="default", min_length=1, max_length=64)
    credential_slug: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1)
    replace_existing: bool = False

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        integration_name = data.pop("integration_name", None)
        credential_profile_key = data.pop("credential_profile_key", None)
        credential_name = data.pop("credential_name", None)
        secret_value = data.pop("secret_value", None)
        if "app_name" not in data and integration_name is not None:
            data["app_name"] = integration_name
        if "profile_name" not in data and credential_profile_key is not None:
            data["profile_name"] = credential_profile_key
        if "credential_slug" not in data and credential_name is not None:
            data["credential_slug"] = credential_name
        if "value" not in data and secret_value is not None:
            data["value"] = secret_value
        return data


class CredentialsCreateTool(ToolBase):
    """Create encrypted credential binding for one app/profile/slug."""

    name = "credentials.create"
    description = "Create encrypted credential binding for trusted callers. Returns metadata only."
    parameters_model = CredentialsCreateParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = CredentialsCreateParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        try:
            service = get_credentials_service(self._settings)
            metadata = await service.create(
                profile_id=ctx.profile_id,
                tool_name="app.run",
                integration_name=payload.app_name,
                credential_profile_key=payload.profile_name,
                credential_name=payload.credential_slug,
                secret_value=payload.value,
                replace_existing=payload.replace_existing,
            )
            return ToolResult(ok=True, payload={"binding": metadata.model_dump(mode="json")})
        except CredentialsServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create credentials.create tool instance."""

    return CredentialsCreateTool(settings=settings)
