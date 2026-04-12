"""LLM tool catalog and skill-first exposure helpers."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.agent_loop.channel_tool_policy import filter_tool_names_for_runtime
from afkbot.services.agent_loop.sensitive_tool_policy import blocked_tool_names_for_runtime
from afkbot.services.agent_loop.thinking import READ_ONLY_TOOL_NAMES, ToolAccessMode
from afkbot.services.agent_loop.skill_router import SkillRoute
from afkbot.services.agent_loop.tool_skill_resolver import ToolSkillResolver
from afkbot.services.llm.contracts import LLMToolDefinition
from afkbot.services.policy import PolicyEngine, PolicyViolationError
from afkbot.services.tools.registry import ToolRegistry

_CLI_APPROVAL_CANDIDATE_TOOL_NAMES = (
    "bash.exec",
    "session.job.run",
    "file.list",
    "file.read",
    "file.search",
    "file.edit",
    "file.write",
)


@dataclass(frozen=True, slots=True)
class ToolSurface:
    """Visible and directly executable tool sets for one turn."""

    visible_tools: tuple[LLMToolDefinition, ...]
    executable_tool_names: tuple[str, ...]
    approval_required_tool_names: tuple[str, ...]


class ToolExposureBuilder:
    """Build the LLM-visible tool catalog under policy and selected skills."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry | None,
        policy_engine: PolicyEngine,
        tool_skill_resolver: ToolSkillResolver,
        tool_requires_automation_intent: Callable[..., bool],
    ) -> None:
        self._tool_registry = tool_registry
        self._policy_engine = policy_engine
        self._tool_skill_resolver = tool_skill_resolver
        self._tool_requires_automation_intent = tool_requires_automation_intent

    def build_tool_surface(
        self,
        policy: ProfilePolicy,
        *,
        profile_id: str | None = None,
        skill_route: SkillRoute | None = None,
        automation_intent: bool,
        runtime_metadata: dict[str, object] | None = None,
        tool_access_mode: ToolAccessMode = "default",
        approved_tool_names: tuple[str, ...] | None = None,
        cli_approval_surface_enabled: bool = False,
    ) -> ToolSurface:
        """Return visible tools plus the subset executable without extra approval."""

        if self._tool_registry is None:
            return ToolSurface(visible_tools=(), executable_tool_names=(), approval_required_tool_names=())
        allowed_names = self._policy_engine.allowed_tool_names(
            policy=policy,
            available_names=self._tool_registry.list_names(),
        )
        selected_app_names = self._selected_app_names_for_skill_route(
            skill_route=skill_route,
            profile_id=profile_id,
        )
        filtered_names = self._filter_tool_names_by_skill_route(
            allowed_names=allowed_names,
            skill_route=skill_route,
            selected_app_names=selected_app_names,
            runtime_metadata=runtime_metadata,
        )
        filtered_names = self._filter_tool_names_by_access_mode(
            tool_names=filtered_names,
            tool_access_mode=tool_access_mode,
        )
        filtered_names = filter_tool_names_for_runtime(
            tool_names=filtered_names,
            runtime_metadata=runtime_metadata,
        )
        blocked_sensitive_names = blocked_tool_names_for_runtime(runtime_metadata=runtime_metadata)
        if blocked_sensitive_names:
            filtered_names = tuple(
                name for name in filtered_names if name not in blocked_sensitive_names
            )
        filtered_names = self._merge_approved_tool_names(
            filtered_names=filtered_names,
            approved_tool_names=approved_tool_names,
            policy=policy,
            runtime_metadata=runtime_metadata,
            tool_access_mode=tool_access_mode,
        )
        approval_visible_names = self._approval_visible_tool_names(
            executable_tool_names=filtered_names,
            policy=policy,
            runtime_metadata=runtime_metadata,
            tool_access_mode=tool_access_mode,
            automation_intent=automation_intent,
            cli_approval_surface_enabled=cli_approval_surface_enabled,
        )
        visible_name_set = set(filtered_names)
        visible_names = filtered_names + tuple(
            name for name in approval_visible_names if name not in visible_name_set
        )
        definitions: list[LLMToolDefinition] = []
        approval_required_names = set(approval_visible_names)
        for name in visible_names:
            definition = self._tool_definition(
                tool_name=name,
                selected_app_names=selected_app_names,
                automation_intent=automation_intent,
                approval_required=name in approval_required_names,
            )
            if definition is not None:
                definitions.append(definition)
        return ToolSurface(
            visible_tools=tuple(definitions),
            executable_tool_names=filtered_names,
            approval_required_tool_names=approval_visible_names,
        )

    def _passes_runtime_hard_guards(
        self,
        *,
        tool_name: str,
        runtime_metadata: dict[str, object] | None,
        tool_access_mode: ToolAccessMode,
        blocked_sensitive_names: set[str],
    ) -> bool:
        """Return whether one tool survives non-policy runtime guards."""

        if self._tool_registry is None or self._tool_registry.get(tool_name) is None:
            return False
        if tool_name in blocked_sensitive_names:
            return False
        if not self._filter_tool_names_by_access_mode(
            tool_names=(tool_name,),
            tool_access_mode=tool_access_mode,
        ):
            return False
        return bool(
            filter_tool_names_for_runtime(
                tool_names=(tool_name,),
                runtime_metadata=runtime_metadata,
            )
        )

    def _tool_is_explicitly_denied(self, *, policy: ProfilePolicy, tool_name: str) -> bool:
        """Return whether one tool is denied, failing closed on invalid policy config."""

        try:
            return self._policy_engine.is_tool_denied(policy=policy, tool_name=tool_name)
        except PolicyViolationError:
            return True

    def _merge_approved_tool_names(
        self,
        *,
        filtered_names: tuple[str, ...],
        approved_tool_names: tuple[str, ...] | None,
        policy: ProfilePolicy,
        runtime_metadata: dict[str, object] | None,
        tool_access_mode: ToolAccessMode,
    ) -> tuple[str, ...]:
        """Merge explicitly approved tool names into the directly executable surface."""

        if self._tool_registry is None or not approved_tool_names:
            return filtered_names

        blocked_sensitive_names = set(
            blocked_tool_names_for_runtime(runtime_metadata=runtime_metadata)
        )
        merged = list(filtered_names)
        seen = set(filtered_names)
        for raw_name in approved_tool_names:
            name = str(raw_name).strip()
            if (
                not name
                or name in seen
                or self._tool_is_explicitly_denied(policy=policy, tool_name=name)
                or not self._passes_runtime_hard_guards(
                    tool_name=name,
                    runtime_metadata=runtime_metadata,
                    tool_access_mode=tool_access_mode,
                    blocked_sensitive_names=blocked_sensitive_names,
                )
            ):
                continue
            merged.append(name)
            seen.add(name)
        return tuple(merged)

    def visible_enforceable_skill_names(
        self,
        *,
        available_tools: tuple[LLMToolDefinition, ...],
        profile_id: str | None = None,
    ) -> set[str]:
        """Resolve enforceable skills from the current LLM-visible tool surface only."""

        names = {
            skill_name
            for tool in available_tools
            if (skill_name := str(tool.required_skill or "").strip())
        }
        visible_app_names = self._visible_app_names_for_tools(available_tools=available_tools)
        if not visible_app_names:
            return names

        registry = self._tool_skill_resolver.app_registry(profile_id=profile_id)
        for app_name in visible_app_names:
            app_definition = registry.get(app_name)
            if app_definition is None:
                continue
            names.update(app_definition.allowed_skills)
        return names

    @staticmethod
    def _filter_tool_names_by_access_mode(
        *,
        tool_names: tuple[str, ...],
        tool_access_mode: ToolAccessMode,
    ) -> tuple[str, ...]:
        """Restrict tool surface for plan-only or inspection-only turns."""

        if tool_access_mode == "default":
            return tool_names
        if tool_access_mode == "none":
            return ()
        return tuple(name for name in tool_names if name in READ_ONLY_TOOL_NAMES)

    def _dynamic_allowed_app_names_for_tool(
        self,
        *,
        tool_name: str,
        selected_app_names: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Return routed app guards for one dynamically routed tool."""

        if tool_name != "app.run" or not selected_app_names:
            return ()
        return selected_app_names

    def _tool_definition(
        self,
        *,
        tool_name: str,
        selected_app_names: tuple[str, ...],
        automation_intent: bool,
        approval_required: bool,
    ) -> LLMToolDefinition | None:
        """Build one visible tool definition when registry and intent allow it."""

        if self._tool_registry is None:
            return None
        if not automation_intent and self._tool_requires_automation_intent(tool_name=tool_name):
            return None
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return None
        dynamic_app_names = self._dynamic_allowed_app_names_for_tool(
            tool_name=tool.name,
            selected_app_names=selected_app_names,
        )
        schema = self._tool_schema_for_llm(
            tool_name=tool.name,
            required_skill=tool.required_skill,
            raw_schema=tool.llm_parameters_schema(),
            allowed_app_names=dynamic_app_names or None,
        )
        return LLMToolDefinition(
            name=tool.name,
            description=self._tool_description_for_llm(
                description=tool.description,
                parallel_execution_safe=bool(getattr(tool, "parallel_execution_safe", False)),
            ),
            parameters_schema=schema,
            required_skill=tool.required_skill,
            requires_confirmation=approval_required,
        )

    def _approval_visible_tool_names(
        self,
        *,
        executable_tool_names: tuple[str, ...],
        policy: ProfilePolicy,
        runtime_metadata: dict[str, object] | None,
        tool_access_mode: ToolAccessMode,
        automation_intent: bool,
        cli_approval_surface_enabled: bool,
    ) -> tuple[str, ...]:
        """Return CLI-only visible tools that still require runtime approval to execute."""

        if (
            self._tool_registry is None
            or not cli_approval_surface_enabled
            or tool_access_mode != "default"
        ):
            return ()

        direct_names = set(executable_tool_names)
        blocked_sensitive_names = set(
            blocked_tool_names_for_runtime(runtime_metadata=runtime_metadata)
        )
        approval_names: list[str] = []
        for name in _CLI_APPROVAL_CANDIDATE_TOOL_NAMES:
            if (
                name in direct_names
                or self._tool_is_explicitly_denied(policy=policy, tool_name=name)
                or not self._passes_runtime_hard_guards(
                    tool_name=name,
                    runtime_metadata=runtime_metadata,
                    tool_access_mode=tool_access_mode,
                    blocked_sensitive_names=blocked_sensitive_names,
                )
                or (
                    not automation_intent
                    and self._tool_requires_automation_intent(tool_name=name)
                )
            ):
                continue
            approval_names.append(name)
        return tuple(approval_names)

    @staticmethod
    def _tool_description_for_llm(
        description: str,
        *,
        parallel_execution_safe: bool = False,
    ) -> str:
        """Normalize one tool description before provider-specific annotations."""

        normalized = description.rstrip()
        if parallel_execution_safe:
            normalized = (
                f"{normalized} When several independent calls are needed, emit them together "
                "in one assistant response so the runtime can execute them concurrently."
            )
        return normalized

    @staticmethod
    def _tool_schema_for_llm(
        *,
        tool_name: str,
        required_skill: str | None,
        raw_schema: dict[str, object],
        allowed_app_names: tuple[str, ...] | None = None,
    ) -> dict[str, object]:
        """Return one LLM tool schema with routed skill/app guards."""

        schema = json.loads(json.dumps(raw_schema))
        del required_skill
        ToolExposureBuilder._strip_secure_secret_properties(
            schema=schema,
            tool_name=tool_name,
        )
        if allowed_app_names:
            ToolExposureBuilder._bind_string_property_for_llm(
                schema=schema,
                property_name="app_name",
                values=allowed_app_names,
                description=(
                    f"Selected app guard for {tool_name}. "
                    "Use only one of the routed apps for this request."
                ),
            )
        return {str(key): value for key, value in schema.items()}

    @staticmethod
    def _strip_secure_secret_properties(
        *,
        schema: dict[str, object],
        tool_name: str,
    ) -> None:
        """Remove secret-bearing fields from LLM-visible credential tool schemas."""

        if tool_name not in {"credentials.request", "credentials.create", "credentials.update"}:
            return

        properties = schema.get("properties")
        if isinstance(properties, dict):
            properties = {str(key): value for key, value in properties.items()}
            properties.pop("value", None)
            properties.pop("secret_value", None)
            schema["properties"] = properties

        required_raw = schema.get("required")
        if isinstance(required_raw, list):
            schema["required"] = [
                str(item)
                for item in required_raw
                if isinstance(item, str) and item not in {"value", "secret_value"}
            ]

    @staticmethod
    def _bind_string_property_for_llm(
        *,
        schema: dict[str, object],
        property_name: str,
        values: tuple[str, ...],
        description: str,
    ) -> None:
        """Bind one string property to a routed enum or const in tool schema."""

        if not values:
            return

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        normalized_props = {str(key): value for key, value in properties.items()}

        field_schema = normalized_props.get(property_name)
        if not isinstance(field_schema, dict):
            field_schema = {}
        field_schema["type"] = "string"
        if len(values) == 1:
            field_schema["const"] = values[0]
            field_schema.pop("enum", None)
        else:
            field_schema["enum"] = list(values)
            field_schema.pop("const", None)
        if not field_schema.get("description"):
            field_schema["description"] = description
        normalized_props[property_name] = field_schema
        schema["properties"] = normalized_props

        required_raw = schema.get("required")
        required: list[str] = []
        if isinstance(required_raw, list):
            required = [str(item) for item in required_raw if isinstance(item, str)]
        if property_name not in required:
            required.append(property_name)
        schema["required"] = required

    @staticmethod
    def _visible_app_names_for_tools(
        *,
        available_tools: tuple[LLMToolDefinition, ...],
    ) -> tuple[str, ...]:
        """Extract routed app names from the visible app.run schema when it is constrained."""

        app_tool = next((tool for tool in available_tools if tool.name == "app.run"), None)
        if app_tool is None:
            return ()
        properties = app_tool.parameters_schema.get("properties")
        if not isinstance(properties, dict):
            return ()
        app_name_schema = properties.get("app_name")
        if not isinstance(app_name_schema, dict):
            return ()

        visible_names: list[str] = []
        const_value = app_name_schema.get("const")
        if isinstance(const_value, str) and const_value.strip():
            visible_names.append(const_value.strip().lower())

        enum_value = app_name_schema.get("enum")
        if isinstance(enum_value, list):
            visible_names.extend(
                str(item).strip().lower()
                for item in enum_value
                if isinstance(item, str) and item.strip()
            )

        if not visible_names:
            return ()
        return tuple(dict.fromkeys(visible_names))

    def _filter_tool_names_by_skill_route(
        self,
        *,
        allowed_names: tuple[str, ...] | list[str],
        skill_route: SkillRoute | None,
        selected_app_names: tuple[str, ...],
        runtime_metadata: dict[str, object] | None = None,
    ) -> tuple[str, ...]:
        """Restrict visible tools to the selected skill surface when one is routed."""

        if self._tool_registry is None or skill_route is None or not skill_route.has_selection:
            return tuple(allowed_names)
        if self._is_background_runtime(runtime_metadata=runtime_metadata):
            if skill_route.has_explicit_selection and skill_route.has_unavailable_blocking_selection:
                return ()
            return self._ordered_tool_names(
                tool_names=list(allowed_names),
                preferred_tool_order=skill_route.preferred_tool_order,
            )
        if not skill_route.has_executable_selection:
            if skill_route.has_explicit_selection:
                if skill_route.has_unavailable_blocking_selection:
                    return ()
                return tuple(allowed_names)
            return tuple(allowed_names)

        selected_skill_names = set(skill_route.executable_skill_names)
        selected_tool_names = set(skill_route.tool_names)
        filtered: list[str] = []
        for name in allowed_names:
            tool = self._tool_registry.get(name)
            if tool is None:
                continue
            if name == "app.run":
                if selected_app_names:
                    filtered.append(name)
                continue
            required_skill = str(tool.required_skill or "").strip()
            if required_skill and required_skill in selected_skill_names:
                filtered.append(name)
                continue
            if name in selected_tool_names:
                filtered.append(name)

        if not filtered:
            return ()
        return self._ordered_tool_names(
            tool_names=filtered,
            preferred_tool_order=skill_route.preferred_tool_order,
        )

    @staticmethod
    def _ordered_tool_names(
        *,
        tool_names: list[str],
        preferred_tool_order: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Return tool names ordered by skill preference first."""

        ordered: list[str] = []
        seen: set[str] = set()
        tool_set = set(tool_names)
        for name in preferred_tool_order:
            if name not in tool_set or name in seen:
                continue
            ordered.append(name)
            seen.add(name)
        for name in tool_names:
            if name in seen:
                continue
            ordered.append(name)
            seen.add(name)
        return tuple(ordered)

    def _selected_app_names_for_skill_route(
        self,
        *,
        skill_route: SkillRoute | None,
        profile_id: str | None,
    ) -> tuple[str, ...]:
        """Resolve routed app names from selected skills and manifest app metadata."""

        if skill_route is None or not skill_route.has_selection:
            return ()
        registry = self._tool_skill_resolver.app_registry(profile_id=profile_id)
        executable_skill_names = set(skill_route.executable_skill_names)
        explicit_app_names = set(skill_route.app_names)

        if explicit_app_names:
            return tuple(
                app_definition.name
                for app_definition in registry.list()
                if app_definition.name in explicit_app_names
            )

        return tuple(
            app_definition.name
            for app_definition in registry.list()
            if executable_skill_names.intersection(app_definition.allowed_skills)
        )

    @staticmethod
    def _is_background_runtime(*, runtime_metadata: dict[str, object] | None) -> bool:
        """Return whether current turn was entered through trusted detached runtime."""

        if not isinstance(runtime_metadata, dict):
            return False
        transport = str(runtime_metadata.get("transport") or "").strip().lower()
        return transport in {"automation", "taskflow"}
