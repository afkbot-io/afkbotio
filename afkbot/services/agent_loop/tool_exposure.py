"""LLM tool catalog and skill-first exposure helpers."""

from __future__ import annotations

import json
from collections.abc import Callable

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.agent_loop.channel_tool_policy import filter_tool_names_for_runtime
from afkbot.services.agent_loop.sensitive_tool_policy import blocked_tool_names_for_runtime
from afkbot.services.agent_loop.thinking import READ_ONLY_TOOL_NAMES, ToolAccessMode
from afkbot.services.agent_loop.skill_router import SkillRoute
from afkbot.services.agent_loop.tool_skill_resolver import ToolSkillResolver
from afkbot.services.llm.contracts import LLMToolDefinition
from afkbot.services.policy import PolicyEngine
from afkbot.services.tools.registry import ToolRegistry


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

    def available_tool_definitions(
        self,
        policy: ProfilePolicy,
        *,
        profile_id: str | None = None,
        skill_route: SkillRoute | None = None,
        automation_intent: bool,
        runtime_metadata: dict[str, object] | None = None,
        tool_access_mode: ToolAccessMode = "default",
    ) -> tuple[LLMToolDefinition, ...]:
        """Return structured tool definitions visible for LLM planning."""

        if self._tool_registry is None:
            return ()
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
        definitions: list[LLMToolDefinition] = []
        for name in filtered_names:
            if not automation_intent and self._tool_requires_automation_intent(tool_name=name):
                continue
            tool = self._tool_registry.get(name)
            if tool is None:
                continue
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
            definitions.append(
                LLMToolDefinition(
                    name=tool.name,
                    description=tool.description,
                    parameters_schema=schema,
                    required_skill=tool.required_skill,
                )
            )
        return tuple(definitions)

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
        return {
            str(key): value for key, value in schema.items()
        }

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
        if self._is_automation_runtime(runtime_metadata=runtime_metadata):
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
    def _is_automation_runtime(*, runtime_metadata: dict[str, object] | None) -> bool:
        """Return whether current turn was entered through automation runtime."""

        if not isinstance(runtime_metadata, dict):
            return False
        return str(runtime_metadata.get("transport") or "").strip().lower() == "automation"
