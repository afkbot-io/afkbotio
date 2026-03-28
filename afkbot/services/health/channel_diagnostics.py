"""Channel routing, delivery, and adapter health diagnostics."""

from __future__ import annotations

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.channel_routing.service import get_channel_binding_service
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.channels.delivery_telemetry import get_channel_delivery_diagnostics
from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.health.contracts import (
    DoctorChannelsReport,
    DoctorDeliveryReport,
    DoctorRoutingReport,
    TelethonUserEndpointReport,
    TelegramPollingEndpointReport,
)
from afkbot.services.health.runtime_support import available_credentials
from afkbot.services.profile_id import validate_profile_id
from afkbot.services.profile_runtime.service import ProfileServiceError, get_profile_service
from afkbot.settings import Settings


async def run_channel_routing_diagnostics(settings: Settings) -> DoctorRoutingReport:
    """Return aggregated in-memory channel-routing telemetry for current runtime root."""

    diagnostics = await get_channel_binding_service(settings).diagnostics()
    fallback_transports = tuple(
        sorted(
            {
                item.strip().lower()
                for item in settings.channel_routing_fallback_transports
                if item.strip()
            }
        )
    )
    return DoctorRoutingReport(
        diagnostics=diagnostics,
        fallback_transports=fallback_transports,
    )


async def run_channel_delivery_diagnostics(settings: Settings) -> DoctorDeliveryReport:
    """Return aggregated in-memory outbound delivery telemetry for current runtime root."""

    return DoctorDeliveryReport(
        diagnostics=get_channel_delivery_diagnostics(settings),
    )


async def run_channel_health_diagnostics(settings: Settings) -> DoctorChannelsReport:
    """Return operator-facing status for configured external channel adapters."""

    endpoint_service = get_channel_endpoint_service(settings)
    telegram_endpoints = await endpoint_service.list(transport="telegram")
    telegram_bindings = await get_channel_binding_service(settings).list(transport="telegram")
    telethon_endpoints = await endpoint_service.list(transport="telegram_user")
    telethon_bindings = await get_channel_binding_service(settings).list(transport="telegram_user")
    profile_ids = {
        endpoint.profile_id.strip()
        for endpoint in [*telegram_endpoints, *telethon_endpoints]
        if endpoint.profile_id.strip()
    }
    existing_profiles = await _load_existing_profile_ids(
        settings=settings,
        profile_ids=tuple(sorted(profile_ids)),
    )
    telegram_reports: list[TelegramPollingEndpointReport] = []
    telethon_reports: list[TelethonUserEndpointReport] = []
    for endpoint in telegram_endpoints:
        profile_id = endpoint.profile_id.strip()
        credential_profile_key = endpoint.credential_profile_key.strip()
        account_id = endpoint.account_id.strip()
        profile_valid = True
        try:
            validate_profile_id(profile_id)
        except ValueError:
            profile_valid = False
        profile_exists = profile_valid and profile_id in existing_profiles
        token_configured = (
            await _has_telegram_token(
                settings=settings,
                profile_id=profile_id,
                credential_profile_key=credential_profile_key,
            )
            if profile_exists
            else False
        )
        binding_count = sum(1 for item in telegram_bindings if item.enabled and item.account_id == account_id)
        telegram_reports.append(
            TelegramPollingEndpointReport(
                endpoint_id=endpoint.endpoint_id,
                enabled=endpoint.enabled,
                profile_id=profile_id,
                credential_profile_key=credential_profile_key,
                account_id=account_id,
                profile_valid=profile_valid,
                profile_exists=profile_exists,
                token_configured=token_configured,
                binding_count=binding_count,
                state_path=str(endpoint_service.telegram_polling_state_path(endpoint_id=endpoint.endpoint_id)),
                state_present=endpoint_service.telegram_polling_state_path(
                    endpoint_id=endpoint.endpoint_id
                ).exists(),
            )
        )
    for endpoint in telethon_endpoints:
        profile_id = endpoint.profile_id.strip()
        credential_profile_key = endpoint.credential_profile_key.strip()
        account_id = endpoint.account_id.strip()
        profile_valid = True
        try:
            validate_profile_id(profile_id)
        except ValueError:
            profile_valid = False
        profile_exists = profile_valid and profile_id in existing_profiles
        available = (
            await _available_telethon_credentials(
                settings=settings,
                profile_id=profile_id,
                credential_profile_key=credential_profile_key,
            )
            if profile_exists
            else set()
        )
        binding_count = sum(1 for item in telethon_bindings if item.enabled and item.account_id == account_id)
        telethon_reports.append(
            TelethonUserEndpointReport(
                endpoint_id=endpoint.endpoint_id,
                enabled=endpoint.enabled,
                profile_id=profile_id,
                credential_profile_key=credential_profile_key,
                account_id=account_id,
                profile_valid=profile_valid,
                profile_exists=profile_exists,
                api_id_configured="api_id" in available,
                api_hash_configured="api_hash" in available,
                phone_configured="phone" in available,
                session_string_configured="session_string" in available,
                policy_allows_runtime=(
                    await _telethon_policy_allows_runtime(settings=settings, profile_id=profile_id)
                    if profile_exists
                    else False
                ),
                binding_count=binding_count,
                state_path=str(endpoint_service.telethon_user_state_path(endpoint_id=endpoint.endpoint_id)),
                state_present=endpoint_service.telethon_user_state_path(
                    endpoint_id=endpoint.endpoint_id
                ).exists(),
            )
        )
    return DoctorChannelsReport(
        telegram_polling=tuple(telegram_reports),
        telethon_userbot=tuple(telethon_reports),
    )


async def _load_existing_profile_ids(
    *,
    settings: Settings,
    profile_ids: tuple[str, ...],
) -> set[str]:
    if not profile_ids:
        return set()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            repo = ProfileRepository(session)
            existing: set[str] = set()
            for profile_id in profile_ids:
                if await repo.get(profile_id) is not None:
                    existing.add(profile_id)
            return existing
    finally:
        await engine.dispose()


async def _has_telegram_token(
    *,
    settings: Settings,
    profile_id: str,
    credential_profile_key: str,
) -> bool:
    try:
        service = get_credentials_service(settings)
    except CredentialsServiceError:
        return False
    try:
        available = await available_credentials(
            service=service,
            profile_id=profile_id,
            integration_name="telegram",
            credential_profile_key=credential_profile_key,
        )
    except CredentialsServiceError:
        return False
    return "telegram_token" in available


async def _available_telethon_credentials(
    *,
    settings: Settings,
    profile_id: str,
    credential_profile_key: str,
) -> set[str]:
    try:
        service = get_credentials_service(settings)
    except CredentialsServiceError:
        return set()
    try:
        return await available_credentials(
            service=service,
            profile_id=profile_id,
            integration_name="telethon",
            credential_profile_key=credential_profile_key,
        )
    except CredentialsServiceError:
        return set()


async def _telethon_policy_allows_runtime(
    *,
    settings: Settings,
    profile_id: str,
) -> bool:
    try:
        profile = await get_profile_service(settings).get(profile_id=profile_id)
    except (ProfileServiceError, ValueError):
        return False
    if not profile.policy.enabled:
        return True
    return "*" in profile.policy.network_allowlist
