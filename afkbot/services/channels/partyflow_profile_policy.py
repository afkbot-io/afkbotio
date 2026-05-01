"""Profile-policy preparation for PartyFlow channel runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.services.channels.endpoint_service import ChannelEndpointServiceError
from afkbot.services.policy.evaluation_helpers import host_matches
from afkbot.settings import Settings

PARTYFLOW_API_HOST = "api.partyflow.ru"
PARTYFLOW_PROFILE_POLICY_CAPABILITY = "apps"
PARTYFLOW_RUNTIME_PROFILE_POLICY_TOOLS = ("app.run",)


@dataclass(frozen=True, slots=True)
class PartyFlowProfilePolicyAdjustment:
    """Profile-policy changes needed for PartyFlow runtime startup."""

    changed: bool = False
    added_capabilities: tuple[str, ...] = ()
    added_tools: tuple[str, ...] = ()
    added_network_hosts: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()


def ensure_partyflow_profile_runtime_policy(
    *,
    settings: Settings,
    profile_id: str,
) -> PartyFlowProfilePolicyAdjustment:
    """Ensure the selected profile can start, probe, and reply through PartyFlow."""

    return asyncio.run(
        ensure_partyflow_profile_runtime_policy_async(
            settings=settings,
            profile_id=profile_id,
        )
    )


async def ensure_partyflow_profile_runtime_policy_async(
    *,
    settings: Settings,
    profile_id: str,
) -> PartyFlowProfilePolicyAdjustment:
    """Async implementation for service callers that already own an event loop."""

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            row = await ProfilePolicyRepository(session).get_or_create_default(profile_id)
            if not row.policy_enabled:
                return PartyFlowProfilePolicyAdjustment()

            denied_rules = _load_policy_json_string_tuple(
                row.denied_tools_json,
                field_name="denied_tools_json",
            )
            denied_tools = tuple(
                tool
                for tool in PARTYFLOW_RUNTIME_PROFILE_POLICY_TOOLS
                if _tool_rule_matches_any(tool_name=tool, rules=denied_rules)
            )
            if denied_tools:
                raise ChannelEndpointServiceError(
                    error_code="partyflow_profile_policy_denies_runtime",
                    reason=(
                        f"Profile `{profile_id}` explicitly denies PartyFlow runtime tool(s): "
                        f"{', '.join(denied_tools)}. Remove the deny rule or choose another "
                        "profile before enabling this PartyFlow channel."
                    ),
                )

            added_tools: tuple[str, ...] = ()
            allowed_tools = _load_policy_json_string_tuple(
                row.allowed_tools_json,
                field_name="allowed_tools_json",
            )
            if allowed_tools:
                missing_tools = tuple(
                    tool
                    for tool in PARTYFLOW_RUNTIME_PROFILE_POLICY_TOOLS
                    if not _tool_rule_matches_any(tool_name=tool, rules=allowed_tools)
                )
                if missing_tools:
                    row.allowed_tools_json = _dump_sorted_strings((*allowed_tools, *missing_tools))
                    added_tools = missing_tools

            added_capabilities: tuple[str, ...] = ()
            capabilities = _load_policy_json_string_tuple(
                row.policy_capabilities_json,
                field_name="policy_capabilities_json",
            )
            if added_tools and PARTYFLOW_PROFILE_POLICY_CAPABILITY not in capabilities:
                row.policy_capabilities_json = _dump_sorted_strings(
                    (*capabilities, PARTYFLOW_PROFILE_POLICY_CAPABILITY)
                )
                added_capabilities = (PARTYFLOW_PROFILE_POLICY_CAPABILITY,)

            added_network_hosts: tuple[str, ...] = ()
            network_allowlist = _load_policy_json_string_tuple(
                row.network_allowlist_json,
                field_name="network_allowlist_json",
            )
            if not any(
                host_matches(host=PARTYFLOW_API_HOST, allowed=allowed_host)
                for allowed_host in network_allowlist
            ):
                row.network_allowlist_json = _dump_sorted_strings(
                    (*network_allowlist, PARTYFLOW_API_HOST)
                )
                added_network_hosts = (PARTYFLOW_API_HOST,)

            if added_tools or added_capabilities or added_network_hosts:
                await session.flush()
                return PartyFlowProfilePolicyAdjustment(
                    changed=True,
                    added_capabilities=added_capabilities,
                    added_tools=added_tools,
                    added_network_hosts=added_network_hosts,
                )
            return PartyFlowProfilePolicyAdjustment()
    finally:
        await engine.dispose()


def partyflow_profile_policy_adjustment_payload(
    adjustment: PartyFlowProfilePolicyAdjustment,
) -> dict[str, object]:
    """Return a stable JSON-serializable CLI/API payload for policy adjustments."""

    return {
        "changed": adjustment.changed,
        "added_capabilities": list(adjustment.added_capabilities),
        "added_tools": list(adjustment.added_tools),
        "added_network_hosts": list(adjustment.added_network_hosts),
        "denied_tools": list(adjustment.denied_tools),
    }


def _load_policy_json_string_tuple(raw: str, *, field_name: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise ChannelEndpointServiceError(
            error_code="partyflow_profile_policy_invalid",
            reason=f"Profile policy field `{field_name}` must be a JSON string list.",
        ) from exc
    if not isinstance(decoded, list):
        raise ChannelEndpointServiceError(
            error_code="partyflow_profile_policy_invalid",
            reason=f"Profile policy field `{field_name}` must be a JSON string list.",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in decoded:
        if not isinstance(item, str):
            continue
        value = item.strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _dump_sorted_strings(values: tuple[str, ...]) -> str:
    return json.dumps(sorted({item.strip().lower() for item in values if item.strip()}))


def _tool_rule_matches_any(*, tool_name: str, rules: tuple[str, ...]) -> bool:
    return any(
        tool_name.startswith(rule[:-1]) if rule.endswith("*") else tool_name == rule
        for rule in rules
    )
