"""Integration readiness and probe matrix for doctor commands."""

from __future__ import annotations

from afkbot.services.agent_loop.runtime_factory import resolve_profile_settings
from afkbot.services.browser_runtime import get_browser_runtime_status
from afkbot.services.credentials import (
    CredentialsService,
    CredentialsServiceError,
    get_credentials_service,
)
from afkbot.services.health.contracts import (
    INTEGRATIONS,
    IntegrationCheck,
    IntegrationMatrixReport,
    IntegrationSpec,
    IntegrationStatus,
    MatrixMode,
)
from afkbot.services.health.integration_probes import IntegrationProbeError, probe_integration
from afkbot.services.health.runtime_support import available_credentials, ensure_profile_ready
from afkbot.services.llm.provider_catalog import LLMProviderId, parse_provider
from afkbot.services.llm.provider_settings import resolve_api_key, resolve_base_url
from afkbot.services.policy import PolicyViolationError
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


async def run_integration_matrix(
    settings: Settings,
    *,
    profile_id: str,
    credential_profile_key: str,
    probe: bool,
) -> IntegrationMatrixReport:
    """Run integration readiness/probe matrix for profile and credential profile."""

    mode: MatrixMode = "probe" if probe else "config"
    await ensure_profile_ready(settings=settings, profile_id=profile_id)
    effective_settings = resolve_profile_settings(settings=settings, profile_id=profile_id)
    registry = ToolRegistry.from_settings(effective_settings)

    service_error: CredentialsServiceError | None = None
    credentials_service = None
    try:
        credentials_service = get_credentials_service(settings)
    except CredentialsServiceError as exc:
        service_error = exc

    checks: list[IntegrationCheck] = []
    for spec in INTEGRATIONS:
        llm_check = await _llm_runtime_check(
            spec=spec,
            settings=effective_settings,
            mode=mode,
            profile_id=profile_id,
        )
        if llm_check is not None:
            checks.append(llm_check)
            continue

        if spec.tool_name is None:
            checks.append(
                IntegrationCheck(
                    integration=spec.integration,
                    status="fail",
                    mode=mode,
                    reason="Integration has no tool binding.",
                    error_code="integration_tool_missing",
                )
            )
            continue
        tool = registry.get(spec.tool_name)
        if tool is None:
            checks.append(
                IntegrationCheck(
                    integration=spec.integration,
                    status="fail",
                    mode=mode,
                    reason=f"Tool is not registered: {spec.tool_name}",
                    error_code="tool_not_registered",
                )
            )
            continue

        if spec.required_credentials:
            credential_check = await _credentials_readiness_check(
                spec=spec,
                mode=mode,
                service_error=service_error,
                credentials_service=credentials_service,
                profile_id=profile_id,
                credential_profile_key=credential_profile_key,
            )
            if credential_check is not None:
                checks.append(credential_check)
                continue

        if spec.requires_credentials_service and service_error is not None:
            checks.append(
                IntegrationCheck(
                    integration=spec.integration,
                    status="fail",
                    mode=mode,
                    reason=service_error.reason,
                    error_code=service_error.error_code,
                )
            )
            continue

        browser_check = _browser_runtime_check(spec=spec, mode=mode, probe=probe)
        if browser_check is not None:
            checks.append(browser_check)
            continue

        if not probe:
            checks.append(
                IntegrationCheck(
                    integration=spec.integration,
                    status="ok",
                    mode=mode,
                    reason="ready",
                )
            )
            continue

        if not spec.probe_supported:
            checks.append(
                IntegrationCheck(
                    integration=spec.integration,
                    status="skip",
                    mode=mode,
                    reason="Probe is not implemented for this integration.",
                    error_code="probe_not_supported",
                )
            )
            continue

        try:
            await probe_integration(
                settings=effective_settings,
                service=credentials_service,
                profile_id=profile_id,
                credential_profile_key=credential_profile_key,
                spec=spec,
            )
        except IntegrationProbeError as exc:
            checks.append(
                IntegrationCheck(
                    integration=spec.integration,
                    status="fail",
                    mode=mode,
                    reason=exc.reason,
                    error_code=exc.error_code,
                )
            )
            continue
        except CredentialsServiceError as exc:
            checks.append(
                IntegrationCheck(
                    integration=spec.integration,
                    status="fail",
                    mode=mode,
                    reason=exc.reason,
                    error_code=exc.error_code,
                )
            )
            continue
        except PolicyViolationError as exc:
            checks.append(
                IntegrationCheck(
                    integration=spec.integration,
                    status="fail",
                    mode=mode,
                    reason=exc.reason,
                    error_code="profile_policy_violation",
                )
            )
            continue
        except Exception as exc:
            checks.append(
                IntegrationCheck(
                    integration=spec.integration,
                    status="fail",
                    mode=mode,
                    reason=f"{exc.__class__.__name__}: {exc}",
                    error_code="integration_probe_failed",
                )
            )
            continue

        checks.append(
            IntegrationCheck(
                integration=spec.integration,
                status="ok",
                mode=mode,
                reason="probe passed",
            )
        )

    return IntegrationMatrixReport(checks=tuple(checks))


async def _llm_runtime_check(
    *,
    spec: IntegrationSpec,
    settings: Settings,
    mode: MatrixMode,
    profile_id: str,
) -> IntegrationCheck | None:
    """Return LLM runtime readiness entry when special handling is required."""

    if spec.kind != "llm":
        return None
    provider_id = parse_provider(settings.llm_provider)
    provider_label = f"{settings.llm_provider}/{settings.llm_model}"
    if provider_id == LLMProviderId.CUSTOM and not resolve_base_url(
        settings=settings,
        provider_id=provider_id,
    ):
        return IntegrationCheck(
            integration=spec.integration,
            status="fail",
            mode=mode,
            reason=f"Custom LLM endpoint is missing a base URL for {provider_label}.",
            error_code="llm_base_url_missing",
        )
    if resolve_api_key(settings=settings, provider_id=provider_id):
        if mode == "config":
            return IntegrationCheck(
                integration=spec.integration,
                status="ok",
                mode=mode,
                reason=f"configured ({provider_label}; use --probe to verify auth)",
            )
        try:
            await probe_integration(
                settings=settings,
                service=None,
                profile_id=profile_id,
                credential_profile_key=profile_id,
                spec=spec,
            )
        except IntegrationProbeError as exc:
            return IntegrationCheck(
                integration=spec.integration,
                status="fail",
                mode=mode,
                reason=exc.reason,
                error_code=exc.error_code,
            )
        except CredentialsServiceError as exc:
            return IntegrationCheck(
                integration=spec.integration,
                status="fail",
                mode=mode,
                reason=exc.reason,
                error_code=exc.error_code,
            )
        except PolicyViolationError as exc:
            return IntegrationCheck(
                integration=spec.integration,
                status="fail",
                mode=mode,
                reason=exc.reason,
                error_code="profile_policy_violation",
            )
        except Exception as exc:
            return IntegrationCheck(
                integration=spec.integration,
                status="fail",
                mode=mode,
                reason=f"{exc.__class__.__name__}: {exc}",
                error_code="integration_probe_failed",
            )
        return IntegrationCheck(
            integration=spec.integration,
            status="ok",
            mode=mode,
            reason=f"probe passed ({provider_label})",
        )
    if provider_id == LLMProviderId.CUSTOM:
        return IntegrationCheck(
            integration=spec.integration,
            status="ok",
            mode=mode,
            reason=f"ready ({provider_label}; custom endpoint without API key)",
        )
    return IntegrationCheck(
        integration=spec.integration,
        status="skip",
        mode=mode,
        reason=f"Provider credentials are missing for {provider_label}.",
        error_code="llm_credentials_missing",
    )


async def _credentials_readiness_check(
    *,
    spec: IntegrationSpec,
    mode: MatrixMode,
    service_error: CredentialsServiceError | None,
    credentials_service: CredentialsService | None,
    profile_id: str,
    credential_profile_key: str,
) -> IntegrationCheck | None:
    """Return deterministic credentials readiness result, or None when probe may continue."""

    if service_error is not None:
        return IntegrationCheck(
            integration=spec.integration,
            status="fail",
            mode=mode,
            reason=service_error.reason,
            error_code=service_error.error_code,
        )
    assert credentials_service is not None
    try:
        available = await available_credentials(
            service=credentials_service,
            profile_id=profile_id,
            integration_name=spec.integration,
            credential_profile_key=credential_profile_key,
        )
    except CredentialsServiceError as exc:
        return IntegrationCheck(
            integration=spec.integration,
            status="fail",
            mode=mode,
            reason=exc.reason,
            error_code=exc.error_code,
        )
    missing = tuple(name for name in spec.required_credentials if name not in available)
    if not missing:
        return None
    return IntegrationCheck(
        integration=spec.integration,
        status="skip",
        mode=mode,
        reason=f"Missing credentials: {', '.join(missing)}",
        error_code="credentials_missing",
    )


def _browser_runtime_check(
    *,
    spec: IntegrationSpec,
    mode: MatrixMode,
    probe: bool,
) -> IntegrationCheck | None:
    """Return browser runtime status entry when special handling is required."""

    if spec.integration != "browser.control":
        return None
    browser_status = get_browser_runtime_status()
    if not browser_status.ok:
        browser_check_status: IntegrationStatus = (
            "skip"
            if browser_status.error_code
            in {"browser_runtime_missing_package", "browser_runtime_unavailable"}
            else "fail"
        )
        reason = (
            f"{browser_status.reason} {browser_status.remediation}".strip()
            if browser_status.remediation
            else browser_status.reason
        )
        return IntegrationCheck(
            integration=spec.integration,
            status=browser_check_status,
            mode=mode,
            reason=reason,
            error_code=browser_status.error_code or "browser_unavailable",
        )
    return IntegrationCheck(
        integration=spec.integration,
        status="ok",
        mode=mode,
        reason="probe passed" if probe else "ready",
    )
