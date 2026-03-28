"""Tool plugin for credentials.request and credential placeholder resolution helpers."""

from __future__ import annotations

from pydantic import ConfigDict, Field, model_validator

from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.credentials_list.plugin import (
    serialize_binding_metadata,
)
from afkbot.settings import Settings


class CredentialsRequestParams(ToolParameters):
    """Parameters for credentials.request tool."""

    model_config = ConfigDict(extra="forbid")

    app_name: str = Field(min_length=1, max_length=64)
    profile_name: str | None = Field(default=None, min_length=1, max_length=64)
    credential_slug: str = Field(min_length=1, max_length=128)
    value: str | None = Field(default=None)

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


class CredentialsRequestTool(ToolBase):
    """Request or persist credential in request-secure-field-friendly deterministic contract."""

    name = "credentials.request"
    description = (
        "Check whether a credential exists and trigger secure-input recovery metadata when it is missing. "
        "Trusted callers may also persist a value directly."
    )
    parameters_model = CredentialsRequestParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = CredentialsRequestParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        try:
            service = get_credentials_service(self._settings)
            if payload.value is None:
                binding = await service.resolve_metadata_for_app_tool(
                    profile_id=ctx.profile_id,
                    tool_name="app.run",
                    integration_name=payload.app_name,
                    credential_profile_key=payload.profile_name,
                    credential_name=payload.credential_slug,
                )
                return ToolResult(
                    ok=True,
                    payload={
                        "exists": True,
                        "binding": serialize_binding_metadata(binding),
                    },
                )
            effective_profile_name = await service.resolve_effective_profile_key_for_app_tool(
                profile_id=ctx.profile_id,
                tool_name="app.run",
                integration_name=payload.app_name,
                credential_profile_key=payload.profile_name,
                credential_name=payload.credential_slug,
            )
            binding = await service.create(
                profile_id=ctx.profile_id,
                tool_name="app.run",
                integration_name=payload.app_name,
                credential_profile_key=effective_profile_name,
                credential_name=payload.credential_slug,
                secret_value=payload.value,
                replace_existing=True,
            )
            return ToolResult(
                ok=True,
                payload={
                    "stored": True,
                    "binding": serialize_binding_metadata(binding),
                },
            )
        except CredentialsServiceError as exc:
            metadata = {
                str(key): value
                for key, value in {
                    "integration_name": payload.app_name,
                    "tool_name": self.name,
                    "credential_profile_key": payload.profile_name,
                    "credential_name": payload.credential_slug,
                    **exc.details,
                }.items()
            }
            return ToolResult.error(
                error_code=exc.error_code,
                reason=exc.reason,
                metadata=metadata,
            )


def create_tool(settings: Settings) -> ToolBase:
    """Create credentials.request tool instance."""

    return CredentialsRequestTool(settings=settings)
