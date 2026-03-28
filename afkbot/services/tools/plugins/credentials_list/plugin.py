"""Tool plugin for credentials.list."""

from __future__ import annotations

from pydantic import ConfigDict, Field, model_validator

from afkbot.services.credentials import CredentialsService, CredentialsServiceError, get_credentials_service
from afkbot.services.credentials.contracts import CredentialBindingMetadata
from afkbot.services.credentials.env_alias import compute_env_key
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class CredentialsListParams(ToolParameters):
    """Parameters for credentials.list tool."""

    model_config = ConfigDict(extra="forbid")

    app_name: str | None = Field(default=None, max_length=64)
    profile_name: str | None = Field(default=None, min_length=1, max_length=64)
    include_inactive: bool = False

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        integration_name = data.pop("integration_name", None)
        credential_profile_key = data.pop("credential_profile_key", None)
        if "app_name" not in data and integration_name is not None:
            data["app_name"] = integration_name
        if "profile_name" not in data and credential_profile_key is not None:
            data["profile_name"] = credential_profile_key
        return data


class CredentialsListTool(ToolBase):
    """List credential bindings metadata without plaintext."""

    name = "credentials.list"
    description = "List encrypted credential bindings metadata."
    parameters_model = CredentialsListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = CredentialsListParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        try:
            service = get_credentials_service(self._settings)
            if payload.app_name is None:
                raw_items = await service.list(
                    profile_id=ctx.profile_id,
                    tool_name=None,
                    integration_name=None,
                    credential_profile_key=payload.profile_name,
                    include_inactive=payload.include_inactive,
                )
                items = [item for item in raw_items if item.tool_name in {None, "app.run"}]
            else:
                items = await self._list_bindings_for_app_runtime(
                    service=service,
                    profile_id=ctx.profile_id,
                    app_name=payload.app_name,
                    profile_name=payload.profile_name,
                    include_inactive=payload.include_inactive,
                )
            return ToolResult(
                ok=True,
                payload={"bindings": [serialize_binding_metadata(item) for item in items]},
            )
        except CredentialsServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)

    async def _list_bindings_for_app_runtime(
        self,
        *,
        service: CredentialsService,
        profile_id: str,
        app_name: str,
        profile_name: str | None,
        include_inactive: bool,
    ) -> list[CredentialBindingMetadata]:
        """List bindings actually visible to app runtime, including generic/global fallbacks."""

        return await service.list_bindings_for_app_runtime(
            profile_id=profile_id,
            tool_name="app.run",
            integration_name=app_name,
            credential_profile_key=profile_name,
            include_inactive=include_inactive,
        )


def serialize_binding_metadata(item: CredentialBindingMetadata) -> dict[str, object]:
    """Serialize binding metadata with deterministic CAPS aliases."""

    binding = item.model_dump(mode="json")
    app_name = str(binding.get("integration_name") or "").strip()
    profile_name = str(binding.get("credential_profile_key") or "").strip()
    credential_slug = str(binding.get("credential_name") or "").strip()
    env_key = compute_env_key(
        app_name=app_name,
        profile_name=profile_name,
        credential_slug=credential_slug,
    )
    return {
        **binding,
        "ENV_KEY": env_key,
        "APP_NAME": app_name,
        "PROFILE_NAME": profile_name,
        "CREDENTIAL_SLUG": credential_slug,
    }


def create_tool(settings: Settings) -> ToolBase:
    """Create credentials.list tool instance."""

    return CredentialsListTool(settings=settings)
